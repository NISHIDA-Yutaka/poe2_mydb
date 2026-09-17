"""poe2db: 受け入れテスト（docs/SPEC.md §9 の T1〜T19）.

    python -m pytest tests -q

poe2db.sqlite が出来ていることが前提（python build_db.py）。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import parsers  # noqa: E402
import search as S  # noqa: E402

DB = ROOT / "poe2db.sqlite"
pytestmark = pytest.mark.skipif(not DB.exists(), reason="poe2db.sqlite が無い")


@pytest.fixture(scope="session")
def conn():
    c = S.connect(DB)
    yield c
    c.close()


@pytest.fixture(scope="session")
def raw():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    yield c
    c.close()


# ------------------------------------------------------------- T1〜T3 横断

def test_t1_cross_kind_rage(conn):
    """T1: 「憤怒」が主要な kind を横断してヒットする."""
    res = S.search(conn, "憤怒")
    kinds = {d["kind"] for d in res}
    assert {"unique", "notable", "ascendancy", "mod", "gem"} <= kinds, kinds
    assert len(res) >= 50


def test_t2_kind_narrowed(conn):
    """T2: エナジーシールドを notable / ascendancy に絞れる."""
    res = S.search(conn, "エナジーシールド", kinds=["notable", "ascendancy"])
    assert {d["kind"] for d in res} <= {"notable", "ascendancy"}
    for kind in ("notable", "ascendancy"):
        assert sum(1 for d in res if d["kind"] == kind) >= 10


def test_t3_socketable_by_slot(conn):
    """T3: 兜に装着できるソケット可能アイテムをテキスト無しで一覧できる."""
    res = S.search(conn, "", kinds=["socketable"], slots=["helmet"])
    assert res
    assert all("helmet" in d["slots"] for d in res)
    assert all(any("helmet" in e["target_slots"] for e in d["meta"]["effects"]) for d in res)


# ------------------------------------------------------- T4〜T5 mod / unique

def test_t4_mod_reverse_lookup(conn):
    """T4: mod から付く装備を逆引きできる."""
    res = S.search(conn, "maximum life", kinds=["mod"])
    hale = [d for d in res if d["meta"]["mod_id"] == "IncreasedLife1"]
    assert hale, "IncreasedLife1 が見つからない"
    slots = set(hale[0]["slots"])
    assert {"body_armour", "shield", "helmet", "gloves", "boots",
            "belt", "amulet", "ring"} <= slots, slots


def test_t5_unique_slots(conn):
    """T5: ユニークの装備部位が区別できる."""
    res = S.search(conn, "", kinds=["unique"])
    astra = [d for d in res if d["name_en"] == "Astramentis"]
    assert astra and astra[0]["slots"] == ["amulet", "jewellery"]
    by_class = Counter(d["meta"]["item_class"] for d in res)
    assert by_class["Charm"] == 12
    assert by_class["Flask"] == 6
    assert by_class["Jewel"] == 15
    for d in res:
        if d["meta"]["item_class"] == "Charm":
            assert "charm" in d["slots"]


# ------------------------------------------------------------ T6〜T7 正規化

def test_t6_case_and_width_insensitive(conn):
    """T6: 大小文字・全角半角を区別しない."""
    a = S.search(conn, "RAGE")
    b = S.search(conn, "rage")
    c = S.search(conn, "ｒａｇｅ")
    assert len(a) == len(b) == len(c) > 0
    assert [d["id"] for d in a] == [d["id"] for d in c]


def test_t7_two_character_query(conn):
    """T7: 2 文字クエリ（FTS5 trigram の穴）でも結果が返る."""
    assert len(S.search(conn, "憤怒")) > 0
    assert len(S.search(conn, "回避")) > 0


# ------------------------------------------------------------ T8 翻訳カバー率

def test_t8_translation_coverage(raw):
    """T8: 日本語のカバー率（初回計測を下回らないこと）."""
    def pct(sql):
        n, total = raw.execute(sql).fetchone()
        return n / max(total, 1)

    assert pct("SELECT SUM(name_ja != ''), COUNT(*) FROM uniques") >= 0.95
    assert pct("SELECT SUM(text_ja != ''), COUNT(*) FROM mods WHERE text_en != ''") >= 0.90
    assert pct("SELECT SUM(name_ja != ''), COUNT(*) FROM passives") >= 0.90
    assert pct("SELECT SUM(desc_ja != '' OR summary_ja != ''), COUNT(*) FROM gems") >= 0.90
    assert pct("SELECT SUM(name_ja != ''), COUNT(*) FROM socketables") >= 0.90


def test_t9_markup_is_well_formed(raw):
    """T9 補助: 行に残したリンク記法が壊れていない（UI が用語リンクに変える）."""
    import re
    ok = re.compile(r"\[([^\[\]|]+)(?:\|([^\[\]]+))?\]")
    seen = 0
    for (lines_json,) in raw.execute("SELECT lines_json FROM search_docs LIMIT 6000"):
        for line in json.loads(lines_json):
            for text in (line.get("en", ""), line.get("ja", "")):
                if "[" not in text:
                    continue
                seen += 1
                # 記法を全て取り除いたら括弧が残らない = 対応が壊れていない
                assert "[" not in ok.sub("", text), text
    assert seen > 100, seen


def test_keyword_links_resolve(raw):
    """本文のリンクの鍵が用語解説に解決できる（99% 以上）."""
    import re
    kw = {r[0] for r in raw.execute("SELECT id FROM keywords")}
    rx = re.compile(r"\[([^\[\]|]+)(?:\|[^\[\]]+)?\]")
    hit = total = 0
    for (text,) in raw.execute("SELECT text_en FROM mods WHERE text_en LIKE '%[%'"):
        for m in rx.finditer(text):
            total += 1
            hit += m.group(1) in kw
    assert total > 1000, total
    assert hit / total >= 0.99, hit / total


# --------------------------------------------------------- T10 除外ルール

def test_t10_legacy_ascendancies_excluded(conn, raw):
    """T10: PoE1 のレガシーアセンダンシーが混ざらない."""
    names = {r["name_en"] for r in raw.execute("SELECT name_en FROM ascendancies")}
    assert not any("DNT" in n for n in names), names
    assert {"Martial Artist", "Smith of Kitava", "Deadeye", "Infernalist"} <= names
    res = S.search(conn, "", kinds=["ascendancy"])
    assert res and all(d["meta"]["ascendancy_en"] for d in res)
    assert all(d["name_en"] or d["lines"] for d in res)


def test_keystones_separate_from_notables(conn, raw):
    """キーストーンは kind='keystone' として切り分けられている."""
    keys = S.search(conn, "", kinds=["keystone"])
    notes = S.search(conn, "", kinds=["notable"])
    assert len(keys) == 33, len(keys)
    assert len(notes) >= 950
    assert all(d["meta"]["is_keystone"] for d in keys)
    assert not any(d["meta"]["is_keystone"] for d in notes)
    names = {d["name_en"] for d in keys}
    assert {"Zealot's Oath", "Unwavering Stance", "Avatar of Fire"} <= names
    # 通常ツリー由来のみ（アセンダンシーのキーストーンは kind='ascendancy'）
    assert all(not d["meta"].get("ascendancy_id") for d in keys)


def test_uniques_grouped_by_slot(conn):
    """ユニークは部位ごとの塊で並ぶ（同じ部位が飛び飛びにならない）."""
    for query in ("", "憤怒", "increased"):
        res = S.search(conn, query, kinds=["unique"])
        if len(res) < 5:
            continue
        seen: list[str] = []
        for d in res:
            slot = d["slots"][0] if d["slots"] else ""
            if not seen or seen[-1] != slot:
                assert slot not in seen, f"{query!r}: {slot} が分断されている"
                seen.append(slot)
        assert len(seen) >= 2, query
    # 既定の並びは装備欄順（兜が鎧より前）
    res = S.search(conn, "", kinds=["unique"])
    order = [d["slots"][0] for d in res if d["slots"]]
    assert order.index("helmet") < order.index("body_armour") < order.index("gloves")


def test_unique_implicits_usable(conn, raw):
    """ユニークの implicit（付与スキル名など）が一覧用データに入っていて検索できる."""
    res = S.search(conn, "", kinds=["unique"])
    with_imp = [d for d in res if d["meta"].get("implicits")]
    assert len(with_imp) >= 200, len(with_imp)
    # 付与スキルの implicit はレベル値が `#` に潰れず残っている
    grants = [l for d in res for l in d["meta"]["implicits"]
              if l["en"].startswith("Grants Skill")]
    assert len(grants) >= 90, len(grants)
    assert all("#" not in l["ja"] for l in grants if l["ja"]), \
        [l["ja"] for l in grants if "#" in l["ja"]][:3]
    # implicit の文言でも検索に当たる
    assert len(S.search(conn, "スキルを付与", kinds=["unique"])) >= 50
    # trade2 由来の `#` は全体でもほぼ残っていない
    left = raw.execute(
        "SELECT COUNT(*) FROM unique_lines WHERE text_ja LIKE '%#%'").fetchone()[0]
    assert left <= 3, left


def test_keywords(conn, raw):
    """ゲーム内の用語解説が日本語つきで入っている."""
    n = raw.execute("SELECT COUNT(*) FROM keywords").fetchone()[0]
    assert n >= 700, n
    ja = raw.execute("SELECT COUNT(*) FROM keywords WHERE definition_ja != ''").fetchone()[0]
    assert ja / n >= 0.99, ja / n
    stun = raw.execute("SELECT term_ja, definition_ja FROM keywords WHERE id='Stun'").fetchone()
    assert stun and stun[0] == "スタン閾値"
    assert "ライトスタン" in stun[1] and "ヘビースタン" in stun[1]
    res = S.search(conn, "スタン閾値", kinds=["keyword"])
    assert res and any(d["name_ja"] == "スタン閾値" for d in res)
    # 用語は横断検索にも出る
    assert any(d["kind"] == "keyword" for d in S.search(conn, "憤怒"))


def test_icons_present(raw):
    """アイコンのパスが主要な kind に入っている."""
    have = dict(raw.execute(
        "SELECT kind, SUM(icon != '') * 1.0 / COUNT(*) FROM search_docs GROUP BY kind"))
    for kind in ("unique", "gem", "socketable", "notable", "keystone", "ascendancy"):
        assert have.get(kind, 0) >= 0.95, (kind, have.get(kind))
    sample = raw.execute(
        "SELECT icon FROM search_docs WHERE kind='unique' AND icon != '' LIMIT 1").fetchone()[0]
    assert sample.endswith(".dds") and not sample.startswith("Art/")


def test_t11_csd_handlers_applied(raw):
    """T11: `.csd` のハンドラが効いている（17m ではなく 1.7m）."""
    rows = list(raw.execute(
        "SELECT detail_json FROM gems WHERE detail_json LIKE '%metre%' LIMIT 400"))
    texts = [d for (j,) in rows for x in json.loads(j)
             for d in ([x] if "en" in x else x.get("levels", []))]
    assert texts
    # 10 倍で出ていれば「17 metres」のような桁が並ぶ。小数表記が存在することを見る
    assert any("." in t.get("en", "") for t in texts), "小数の値が 1 つも無い"


# ------------------------------------------------------------ T12〜T13 石の拳

def test_t12_handwraps_mods(raw):
    """T12: 石の拳の変化後 mod と元 mod が双方向に繋がっている."""
    n = raw.execute("SELECT COUNT(*) FROM mods WHERE sub_kind='handwraps'").fetchone()[0]
    assert n >= 590
    linked = raw.execute(
        "SELECT COUNT(*) FROM mods WHERE sub_kind='handwraps' AND transforms_from != ''"
    ).fetchone()[0]
    assert linked >= 580
    row = raw.execute("SELECT transforms_from FROM mods WHERE id='HandWrapsStrength1'").fetchone()
    assert row and row[0] == "Strength1"
    row = raw.execute("SELECT handwraps_id FROM mods WHERE id='Strength1'").fetchone()
    assert row and row[0] == "HandWrapsStrength1"


def test_t13_handwraps_on_unique_gloves(conn):
    """T13: ユニーク手袋の行に変化後が付く."""
    res = S.search(conn, "", kinds=["unique"], slots=["gloves"], hw="yes")
    assert len(res) >= 30
    total = sum(1 for d in res
                for l in d["lines"] + (d["meta"].get("implicits") or [])
                if l.get("handwraps"))
    assert total >= 95, total
    sample = next(l for d in res for l in d["lines"] if l.get("handwraps"))
    assert sample["handwraps"]["en"] and sample["handwraps"]["mod_id"]


# ------------------------------------------------------------- T14 培養

def test_t14_cultivation(conn, raw):
    """T14: 培養のオーブのプールと Vaal ユニークの置換対象."""
    pool = raw.execute("SELECT COUNT(*) FROM mods WHERE sub_kind='cultivation'").fetchone()[0]
    assert pool >= 200
    gens = {r[0] for r in raw.execute(
        "SELECT DISTINCT generation_type FROM mods WHERE sub_kind='cultivation'")}
    assert gens == {"unique"}
    originals = raw.execute(
        "SELECT COUNT(*) FROM mods WHERE cultivation_replaceable=1").fetchone()[0]
    assert originals == 242
    vaal = raw.execute("SELECT COUNT(*) FROM uniques WHERE is_vaal_unique=1").fetchone()[0]
    assert vaal >= 35
    names = {r[0] for r in raw.execute("SELECT name_en FROM uniques WHERE is_vaal_unique=1")}
    assert {"Atziri's Acuity", "Drillneck"} <= names

    targets = S.search(conn, "", kinds=["unique"], cult="yes")
    assert len(targets) >= 30
    assert all(d["meta"]["is_vaal_unique"] for d in targets)
    assert all(any(l.get("cultivation_replaceable")
                   for l in d["lines"] + (d["meta"].get("implicits") or []))
               for d in targets)


def test_t15_unique_line_mod_match(raw):
    """T15: ユニーク性能行 → mod ID の対応率.

    同じ英文を持つ mod が複数ある行（`ambiguous`）も mod_id は付く。付加情報
    （石の拳・培養）は全候補が一致したときだけ出すので、指標は「mod_id が付いた率」。
    """
    matched, total = raw.execute(
        "SELECT SUM(mod_id != ''), COUNT(*) FROM unique_lines").fetchone()
    assert matched / max(total, 1) >= 0.80, matched / max(total, 1)
    exact, _ = raw.execute(
        "SELECT SUM(match_kind='exact'), COUNT(*) FROM unique_lines").fetchone()
    assert exact / max(total, 1) >= 0.45, exact / max(total, 1)


# ------------------------------------------------------------ T16〜T17 ジェム

def test_t16_gems(conn, raw):
    """T16: スキル・サポート・リネージュ・スピリットが揃っている."""
    total = raw.execute("SELECT COUNT(*) FROM gems").fetchone()[0]
    assert total >= 1100
    res = S.search(conn, "", kinds=["gem"])
    subs = Counter(d["sub_kind"] for d in res)
    # [DNT] 開発用ジェムを除いた実測値（SPEC §13）
    assert subs["active"] >= 450
    assert subs["support"] + subs["lineage"] >= 600
    assert subs["lineage"] >= 80
    assert subs["spirit"] >= 38
    for d in res:
        if d["sub_kind"] == "lineage":
            assert d["meta"]["gem_type"] == "support"
    assert any(d["kind"] == "gem" for d in S.search(conn, "憤怒"))


def test_t17_skill_tag_filter(conn, raw):
    """T17: スキルをタグの AND で絞れる."""
    res = S.search(conn, "", kinds=["gem"], subs=["active"], tags=["melee", "nova"])
    assert res
    for d in res:
        assert {"melee", "nova"} <= set(d["meta"]["tags"])
    nova = S.search(conn, "", kinds=["gem"], tags=["nova"])
    assert len(nova) >= 15
    row = raw.execute("SELECT name_ja FROM gem_tags WHERE id='nova'").fetchone()
    assert row and row[0]


# ---------------------------------------------------------- T18 タイムレス

def test_t18_timeless(conn, raw):
    """T18: タイムレスジュエルの変化パッシブ（PoE1 レガシーを含まない）."""
    counts = Counter(r[0] for r in raw.execute("SELECT jewel FROM timeless_passives"))
    assert 38 <= counts["kalguur"] <= 42, counts
    assert 34 <= counts["abyss"] <= 38, counts
    assert set(counts) == {"kalguur", "abyss"}
    keys = raw.execute(
        "SELECT COUNT(*) FROM timeless_passives WHERE jewel='kalguur' "
        "AND passive_type='keystone'").fetchone()[0]
    assert keys == 3
    row = raw.execute(
        "SELECT name_ja FROM timeless_passives WHERE name_en='Scorched Earth'").fetchone()
    assert row and row[0] == "焦げた大地"
    res = S.search(conn, "", kinds=["timeless"], jewel="kalguur")
    assert len(res) >= 38


def test_t19_gem_detail_translation(raw):
    """T19: ジェム効果詳細の訳が付いている（検証を通ったものだけ）."""
    rows = list(raw.execute("SELECT detail_json FROM gems"))
    total = ja = 0
    for (j,) in rows:
        for x in json.loads(j):
            for item in (x.get("levels") or [x]):
                if item.get("en"):
                    total += 1
                    ja += bool(item.get("ja"))
    assert total > 2000
    assert ja / total >= 0.75, ja / total


# ------------------------------------------------------------- パーサ単体

def test_parser_normalization():
    assert parsers.strip_markup("[Resistances|Cold Resistance]") == "Cold Resistance"
    assert parsers.strip_markup("[Fire]") == "Fire"
    assert parsers.normalize("+(10-19) to maximum Life") == "# to maximum life"
    assert parsers.normalize_for_search("ＲＡＧＥ") == "rage"


def test_parser_query_syntax():
    q = parsers.parse_query('kind:mod,gem slot:helmet "maximum life" -reduced 憤怒')
    assert q.filters["kind"] == ["mod", "gem"]
    assert q.filters["slot"] == ["helmet"]
    assert q.phrases == ["maximum life"]
    assert q.excludes == ["reduced"]
    assert q.terms == ["憤怒"]


def test_parser_csd_handlers():
    assert parsers.apply_handler("divide_by_ten_1dp_if_required", 17) == "1.7"
    assert parsers.apply_handler("divide_by_ten_1dp_if_required", 20) == "2"
    assert parsers.apply_handler("negate", 25) == "-25"
    assert parsers.apply_handler("milliseconds_to_seconds", 1500) == "1.5"


def test_search_filters_are_additive(conn):
    """フィルタが AND で効く."""
    a = S.search(conn, "", kinds=["mod"], slots=["helmet"])
    b = S.search(conn, "", kinds=["mod"])
    assert 0 < len(a) < len(b)
    assert all("helmet" in d["slots"] for d in a)
