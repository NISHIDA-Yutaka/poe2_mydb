"""poe2db: 検索（docs/SPEC.md §7）. CLI かつ tests から使うライブラリ.

    python search.py 憤怒
    python search.py -k notable,ascendancy エナジーシールド
    python search.py -k socketable -s helmet
    python search.py -k gem --tag melee,nova
    python search.py -k mod "chance to Ignite" --json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from parsers import Query, match_haystack, normalize_for_search, parse_query

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "poe2db.sqlite"

KIND_LABEL = {
    "unique": ("Unique", "ユニーク"),
    "notable": ("Notable", "ノータブル"),
    "keystone": ("Keystone", "キーストーン"),
    "ascendancy": ("Ascendancy", "アセンダンシー"),
    "mod": ("Mod", "mod"),
    "socketable": ("Socketable", "ソケット"),
    "gem": ("Gem", "ジェム"),
    "timeless": ("Timeless", "タイムレス"),
}
KIND_ORDER = list(KIND_LABEL)


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_doc(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"], "kind": row["kind"], "sub_kind": row["sub_kind"],
        "slots": [s for s in (row["slots"] or "").split(",") if s],
        "name_en": row["name_en"], "name_ja": row["name_ja"],
        "group_en": row["group_en"], "group_ja": row["group_ja"],
        "lines": json.loads(row["lines_json"]), "meta": json.loads(row["meta_json"]),
        "haystack": row["haystack"], "sort_key": row["sort_key"],
    }


def _candidate_rows(conn: sqlite3.Connection, q: Query):
    """FTS5 trigram は 3 文字未満に当たらないので分岐する（SPEC §5 の落とし穴）."""
    long_terms = [t for t in (q.terms + q.phrases) if len(t) >= 3]
    if long_terms:
        term = max(long_terms, key=len).replace('"', '""')
        return conn.execute(
            "SELECT d.* FROM search_fts f JOIN search_docs d ON d.id = f.id "
            "WHERE f.haystack MATCH ?", (f'"{term}"',))
    return conn.execute("SELECT * FROM search_docs")


def _meta_tokens(doc: dict, key: str) -> list[str]:
    meta = doc["meta"]
    if key == "asc":
        return [normalize_for_search(str(meta.get(k, "")))
                for k in ("ascendancy_id", "ascendancy_en", "ascendancy_ja",
                          "class_en", "class_ja")]
    if key == "origin":
        return [normalize_for_search(str(meta.get("origin", "")))]
    if key == "tag":
        return [normalize_for_search(t) for t in
                (meta.get("tags") or []) + (meta.get("tags_en") or []) +
                (meta.get("tags_ja") or [])]
    if key == "type":
        return [normalize_for_search(t) for t in (meta.get("skill_types") or [])]
    if key == "color":
        return [normalize_for_search(str(meta.get("color", "")))]
    if key == "jewel":
        return [normalize_for_search(str(meta.get("jewel", "")))]
    return []


def _passes_filters(doc: dict, q: Query) -> bool:
    f = q.filters
    if "kind" in f and doc["kind"] not in f["kind"]:
        return False
    if "sub" in f and normalize_for_search(doc["sub_kind"]) not in f["sub"]:
        return False
    if "gem" in f:
        if doc["kind"] != "gem" or normalize_for_search(doc["sub_kind"]) not in f["gem"]:
            return False
    if "slot" in f and not set(f["slot"]) & set(doc["slots"]):
        return False
    if "jewel" in f and doc["kind"] != "timeless":
        return False
    if "hw" in f:
        want = f["hw"][0] in ("yes", "1", "true")
        has = bool(doc["meta"].get("handwraps")) or doc["sub_kind"] == "handwraps" or \
            any(l.get("handwraps") for l in doc["lines"]) or \
            any(l.get("handwraps") for l in doc["meta"].get("implicits") or [])
        if want != has:
            return False
    if "cult" in f:
        want = f["cult"][0] in ("yes", "1", "true")
        has = bool(doc["meta"].get("cultivation_target")) or \
            bool(doc["meta"].get("cultivation_replaceable")) or \
            doc["sub_kind"] == "cultivation"
        if want != has:
            return False
    for key in ("asc", "origin", "type", "color", "jewel"):
        if key in f:
            tokens = set(_meta_tokens(doc, key))
            if not tokens & set(f[key]):
                return False
    if "tag" in f:  # タグは AND
        tokens = set(_meta_tokens(doc, "tag"))
        if not set(f["tag"]) <= tokens:
            return False
    return True


def _score(doc: dict, q: Query) -> int:
    """SPEC §7.3 のランキング（大きいほど上）."""
    names = [normalize_for_search(doc["name_en"]), normalize_for_search(doc["name_ja"])]
    groups = [normalize_for_search(doc["group_en"]), normalize_for_search(doc["group_ja"])]
    total = 0
    for term in q.terms + q.phrases:
        if any(term == n for n in names if n):
            total += 100
        elif any(n.startswith(term) for n in names if n):
            total += 60
        elif any(term in n for n in names if n):
            total += 40
        elif any(term in g for g in groups if g):
            total += 20
        else:
            total += 5
    return total


def search(conn: sqlite3.Connection, text: str = "", *, kinds=None, slots=None,
           subs=None, tags=None, limit: int | None = None, **filters) -> list[dict]:
    """テキスト + フィルタで検索する。フィルタは引数でもクエリ構文でも指定できる."""
    q = parse_query(text or "")
    for key, value in (("kind", kinds), ("slot", slots), ("sub", subs), ("tag", tags)):
        if value:
            vals = [value] if isinstance(value, str) else list(value)
            q.filters.setdefault(key, []).extend(normalize_for_search(v) for v in vals)
    for key, value in filters.items():
        if value is None or value is False:
            continue
        if value is True:
            value = "yes"
        vals = [value] if isinstance(value, str) else list(value)
        q.filters.setdefault(key, []).extend(normalize_for_search(str(v)) for v in vals)

    out: list[dict] = []
    for row in _candidate_rows(conn, q):
        doc = _row_to_doc(row)
        if not match_haystack(q, doc["haystack"]):
            continue
        if not _passes_filters(doc, q):
            continue
        doc["score"] = _score(doc, q)
        out.append(doc)
    kind_rank = {k: n for n, k in enumerate(KIND_ORDER)}
    # ユニークは部位ごとの塊で見たいので、関連度より先に slot 順を効かせる
    out.sort(key=lambda d: (kind_rank.get(d["kind"], 99),
                            d["sort_key"] if d["kind"] == "unique" else 0,
                            -d["score"], d["sort_key"], d["name_en"]))
    return out[:limit] if limit else out


# --------------------------------------------------------------------- CLI

def pick_text(en: str, ja: str, lang: str) -> str:
    if lang == "en":
        return en
    if lang == "both":
        return f"{ja} / {en}" if ja and en and ja != en else (ja or en)
    return ja if ja else (f"{en} [EN]" if en else "")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="*", help="検索語（空白区切りは AND、-語 で除外）")
    ap.add_argument("-k", "--kind", help="kind をカンマ区切りで")
    ap.add_argument("-s", "--slot", help="slot をカンマ区切りで")
    ap.add_argument("--sub", help="sub_kind をカンマ区切りで")
    ap.add_argument("--tag", help="ジェムタグをカンマ区切りで（AND）")
    ap.add_argument("--lang", choices=("ja", "en", "both"), default="ja")
    ap.add_argument("-n", "--limit", type=int, default=40)
    ap.add_argument("--json", action="store_true", help="SearchDoc の配列で出す")
    ap.add_argument("--db", default=str(DB_PATH))
    args = ap.parse_args()

    conn = connect(args.db)
    results = search(conn, " ".join(args.query),
                     kinds=args.kind.split(",") if args.kind else None,
                     slots=args.slot.split(",") if args.slot else None,
                     subs=args.sub.split(",") if args.sub else None,
                     tags=args.tag.split(",") if args.tag else None)
    if args.json:
        json.dump(results[:args.limit], sys.stdout, ensure_ascii=False, indent=1)
        print()
        return

    total = len(results)
    shown = results[:args.limit]
    by_kind: dict[str, list[dict]] = {}
    for doc in shown:
        by_kind.setdefault(doc["kind"], []).append(doc)
    for kind in KIND_ORDER:
        docs = by_kind.get(kind)
        if not docs:
            continue
        label = pick_text(*KIND_LABEL[kind], args.lang)
        n_all = sum(1 for d in results if d["kind"] == kind)
        print(f"\n== {label} ({n_all})")
        for doc in docs:
            name = pick_text(doc["name_en"], doc["name_ja"], args.lang)
            slot_txt = " ".join(doc["slots"][:4])
            head = f"  {name}"
            if doc["group_en"] or doc["group_ja"]:
                head += f"  [{pick_text(doc['group_en'], doc['group_ja'], args.lang)}]"
            if slot_txt:
                head += f"  <{slot_txt}>"
            print(head)
            for line in doc["lines"][:3]:
                text = pick_text(line.get("en", ""), line.get("ja", ""), args.lang)
                if text:
                    print(f"      {text}")
    print(f"\n{total} results ({min(total, args.limit)} shown)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
