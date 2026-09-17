"""poe2db: SQLite の構築（docs/SPEC.md §5, §6）.

    python build_db.py

毎回 poe2db.sqlite を作り直す。差分更新は無い。
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path

import parsers
import slots as S
from parsers import Variant, normalize, normalize_for_search, strip_markup

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DATEXPORT = ROOT / "datexport" / "tables"
DB_PATH = ROOT / "poe2db.sqlite"

GENERIC_CSD = ["stat_descriptions.csd", "gem_stat_descriptions.csd",
               "passive_skill_stat_descriptions.csd"]

_stats: dict[str, object] = {}


def log(msg: str) -> None:
    print(msg, flush=True)


def jload(path: Path):
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


# ------------------------------------------------------------------ GGPK 表

class Dat:
    """pathofexile-dat の出力（EN/JA は同じ行順）."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._cache: dict[tuple[str, str], list[dict]] = {}

    def table(self, name: str, lang: str = "English") -> list[dict]:
        key = (name, lang)
        if key not in self._cache:
            path = self.root / lang / f"{name}.json"
            self._cache[key] = jload(path) if path.exists() else []
        return self._cache[key]

    def ids(self, name: str, col: str = "Id") -> list:
        return [r.get(col) for r in self.table(name)]

    def pair(self, name: str) -> list[tuple[dict, dict]]:
        en, ja = self.table(name, "English"), self.table(name, "Japanese")
        return list(zip(en, ja))


# ---------------------------------------------------------------- .csd 集合

class CsdSet:
    """全 `.csd` を読み、ファイル指定 → 汎用 → 統合 の順で引く（SPEC §6.0）."""

    def __init__(self, root: Path) -> None:
        self.files: dict[str, dict[tuple[str, ...], dict[str, list[Variant]]]] = {}
        self.includes: dict[str, list[str]] = {}
        self.merged: dict[tuple[str, ...], dict[str, list[Variant]]] = {}
        self.no_desc: set[str] = set()
        paths = sorted(root.rglob("*.csd"))
        for path in paths:
            rel = path.relative_to(root).as_posix().lower()
            blocks, includes, no_desc = parsers.parse_csd(path)
            self.files[rel] = blocks
            self.includes[rel] = [self._norm_include(i) for i in includes]
            self.no_desc |= no_desc
        # 統合は汎用ファイルを優先して埋める
        order = [f for f in GENERIC_CSD if f in self.files]
        order += [f for f in self.files if f not in order]
        for rel in order:
            for ids, langs in self.files[rel].items():
                self.merged.setdefault(ids, langs)
        log(f"  csd: {len(self.files)} files, {len(self.merged)} blocks, "
            f"{len(self.no_desc)} no_description")

    @staticmethod
    def _norm_include(path: str) -> str:
        p = path.replace("\\", "/").lower()
        marker = "statdescriptions/"
        if marker in p:
            p = p[p.index(marker) + len(marker):]
        return p.replace(".txt", ".csd")

    def _chain(self, translation_file: str | None) -> list[str]:
        chain: list[str] = []
        if translation_file:
            rel = self._norm_include(translation_file)
            seen = set()
            stack = [rel]
            while stack:
                cur = stack.pop(0)
                if cur in seen or cur not in self.files:
                    continue
                seen.add(cur)
                chain.append(cur)
                stack += self.includes.get(cur, [])
        chain += [f for f in GENERIC_CSD if f in self.files and f not in chain]
        return chain

    def lookup(self, ids: tuple[str, ...],
               translation_file: str | None = None) -> tuple[dict[str, list[Variant]] | None, str]:
        """(ブロック, 出所) を返す。出所は 'file' / 'generic' / 'merged'."""
        chain = self._chain(translation_file)
        n_specific = len(chain) - sum(1 for f in GENERIC_CSD if f in chain)
        for n, rel in enumerate(chain):
            block = self.files[rel].get(ids)
            if block:
                return block, ("file" if n < n_specific else "generic")
        block = self.merged.get(ids)
        return (block, "merged") if block else (None, "")


# ------------------------------------------------------------------- 翻訳

class Translator:
    """3 系統の翻訳（DATA_PIPELINE §6）."""

    def __init__(self, csd: CsdSet, trade_map: dict[str, str]) -> None:
        self.csd = csd
        self.trade = trade_map
        self.templates: dict[str, tuple[str, str]] = {}
        # 統合（汎用ファイル優先）を先に入れ、そのあと全ファイルの英文を足す。
        # merged は stat ID の組ごとに 1 ブロックしか持たないので、スキル固有ファイルの
        # 言い回しは全ファイルを走査しないと索引に入らない。
        sources = [csd.merged] + list(csd.files.values())
        for blocks in sources:
            for _ids, langs in blocks.items():
                en_list, ja_list = langs.get("English"), langs.get("Japanese")
                if not en_list or not ja_list or len(en_list) != len(ja_list):
                    continue
                for en_v, ja_v in zip(en_list, ja_list):
                    key = parsers.normalize_template(en_v.template)
                    if key and key not in self.templates:
                        self.templates[key] = (en_v.template, ja_v.template)
        log(f"  template index: {len(self.templates)}")

    # --- 系統 A: stat ID → .csd
    def render_stats(self, triples: list[tuple[str, float, float]],
                     translation_file: str | None = None,
                     expected_en: str | None = None) -> tuple[str, str, str]:
        """[(stat_id, min, max)] を EN/JA の本文にする。戻り値 (en, ja, ja_source)."""
        remaining = list(triples)
        en_parts: list[str] = []
        ja_parts: list[str] = []
        source = ""
        ok = True
        while remaining:
            hit = None
            for length in range(len(remaining), 0, -1):
                for start in range(0, len(remaining) - length + 1):
                    window = remaining[start:start + length]
                    block, origin = self.csd.lookup(tuple(w[0] for w in window),
                                                    translation_file)
                    if block:
                        hit = (start, length, block, origin)
                        break
                if hit:
                    break
            if not hit:
                stat_id, lo, _hi = remaining.pop(0)
                if stat_id in self.csd.no_desc or stat_id.endswith("_no_display"):
                    continue
                en_parts.append(f"{stat_id} = {parsers._fmt(lo)}")
                ok = False
                continue
            start, length, block, origin = hit
            window = remaining[start:start + length]
            del remaining[start:start + length]
            mins = [w[1] for w in window]
            maxs = [w[2] for w in window]
            en_v = parsers.pick_variant(block.get("English", []), mins)
            if en_v is None:
                ok = False
                continue
            en_parts.append(parsers.fill_range(en_v, mins, maxs))
            ja_list = block.get("Japanese", [])
            ja_v = parsers.pick_variant(ja_list, mins) if ja_list else None
            if ja_v is None:
                ok = False
            else:
                ja_parts.append(parsers.fill_range(ja_v, mins, maxs))
                source = source or ("csd" if origin != "merged" else "csd_merged")
        en_text = "\n".join(p for p in en_parts if p)
        ja_text = "\n".join(p for p in ja_parts if p) if ok and ja_parts else ""
        if expected_en is not None and ja_text:
            # §6.4: 英語を同じ値で再レンダリングして一致したときだけ採用
            if normalize(en_text) != normalize(expected_en):
                return en_text, "", ""
        return en_text, ja_text, source if ja_text else ""

    # --- 系統 C: 完成英文 → .csd テンプレート索引
    def translate_rendered(self, line: str) -> tuple[str, str]:
        if "\n" in (line or ""):
            # 複数行はテンプレートが行単位なので、全行そろったときだけ採用する
            parts = [p for p in line.split("\n") if p.strip()]
            got = [self.translate_rendered(p) for p in parts]
            if all(ja for ja, _ in got):
                return "\n".join(ja for ja, _ in got), got[0][1]
            return "", ""
        key = normalize(line)
        if not key:
            return "", ""
        pair = self.templates.get(key)
        if pair:
            en_tpl, ja_tpl = pair
            values = _extract_values(en_tpl, line)
            if values is not None:
                return _substitute(ja_tpl, values), "csd"
        ja = self.trade.get(key)
        if ja:
            return ja, "trade"
        return "", ""


_PH = re.compile(r"\{(\d*)(?::[^}]*)?\}")
_VALUE_RE = r"([+-]?\(?[+-]?[\d.]+(?:\s*-\s*[+-]?[\d.]+)?\)?)"


def _extract_values(en_template: str, line: str) -> list[str] | None:
    """英テンプレートと完成英文から `{n}` の値を抜く（DATA_PIPELINE §6.3）."""
    tpl = strip_markup(en_template)
    target = strip_markup(line)
    order: list[int] = []
    counter = {"n": 0}
    parts: list[str] = []
    last = 0
    for m in _PH.finditer(tpl):
        parts.append(re.escape(tpl[last:m.start()]))
        idx = int(m.group(1)) if m.group(1) else counter["n"]
        if not m.group(1):
            counter["n"] += 1
        order.append(idx)
        parts.append(_VALUE_RE)
        last = m.end()
    parts.append(re.escape(tpl[last:]))
    pattern = "".join(parts).replace(r"\ ", r"\s+")
    m = re.fullmatch(pattern, target.strip(), re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    values: dict[int, str] = {}
    for n, idx in enumerate(order):
        values[idx] = m.group(n + 1)
    return [values.get(i, "") for i in range(max(values) + 1)] if values else []


def _substitute(ja_template: str, values: list[str]) -> str:
    counter = {"n": 0}

    def sub(m: re.Match[str]) -> str:
        if m.group(1):
            idx = int(m.group(1))
        else:
            idx = counter["n"]
            counter["n"] += 1
        return values[idx] if idx < len(values) else "?"

    return _PH.sub(sub, ja_template)


# ------------------------------------------------------------------ 共通

def haystack(*parts) -> str:
    """検索用の全文。同じ文字列（mod は name と lines が同一など）は 1 回だけ入れる."""
    chunks: list[str] = []
    for p in parts:
        if not p:
            continue
        if isinstance(p, (list, tuple, set)):
            chunks += [str(x) for x in p if x]
        else:
            chunks.append(str(p))
    seen: set[str] = set()
    uniq: list[str] = []
    for c in chunks:
        n = normalize_for_search(c)
        if n and n not in seen:
            seen.add(n)
            uniq.append(n)
    return " ".join(uniq)


def pick(en: str, ja: str) -> str:
    return ja or en


# =============================================================== ビルド本体

class Builder:
    def __init__(self) -> None:
        self.version = (DATA / "version.txt").read_text(encoding="utf-8").strip()
        log(f"patch {self.version}")
        log("loading sources...")
        self.base_items = jload(DATA / "base_items.json")
        self.item_classes = jload(DATA / "item_classes.json")
        self.mods = jload(DATA / "mods.json")
        self.mods_by_base = jload(DATA / "mods_by_base.json")
        self.uniques = jload(DATA / "uniques.json")
        self.skill_gems = jload(DATA / "skill_gems.json")
        self.gem_tags = jload(DATA / "gem_tags.json")
        self.ascendancies = jload(DATA / "ascendancies.json")
        self.tree = jload(DATA / "passive_skill_trees" / "Default.json")
        self.pob_blocks = jload(DATA / "pob_uniques.json")
        self.dat = Dat(DATEXPORT)
        self.csd = CsdSet(DATA / "statdescriptions")
        self.tr = Translator(self.csd, self._trade_map())
        self.skills = jload(DATA / "skills.json")
        log(f"  skills.json: {len(self.skills)}")
        self.conn = sqlite3.connect(DB_PATH)
        self.conn.execute("PRAGMA journal_mode=OFF")
        self.conn.execute("PRAGMA synchronous=OFF")
        self.docs: list[dict] = []
        self.counts: dict[str, object] = {}

    # ---------------------------------------------------------- 参照データ
    def _trade_map(self) -> dict[str, str]:
        try:
            en = jload(DATA / "trade_stats_en.json")
            ja = jload(DATA / "trade_stats_ja.json")
        except FileNotFoundError:
            return {}
        def flat(payload):
            out = {}
            for group in payload.get("result", []):
                for e in group.get("entries", []):
                    if e.get("id"):
                        out[e["id"]] = e.get("text", "")
            return out
        en_map, ja_map = flat(en), flat(ja)
        out = {}
        for k, en_text in en_map.items():
            ja_text = ja_map.get(k)
            if ja_text:
                out.setdefault(normalize(en_text), ja_text)
        log(f"  trade stats: {len(out)}")
        return out

    def schema(self) -> None:
        self.conn.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))

    # ---------------------------------------------- 名前の日本語（S5 由来）
    def build_names(self) -> None:
        self.class_name = {}   # item_class id -> (en, ja)
        for a, b in self.dat.pair("ItemClasses"):
            self.class_name[a.get("Id")] = (a.get("Name") or "", b.get("Name") or "")
        self.base_ja = {}      # Metadata パス -> JA 名
        for a, b in self.dat.pair("BaseItemTypes"):
            if a.get("Id"):
                self.base_ja[a["Id"]] = b.get("Name") or ""
        # ユニーク名: UniqueStashLayout.WordsKey -> Words.Text/Text2
        words_en = self.dat.table("Words", "English")
        words_ja = self.dat.table("Words", "Japanese")
        self.unique_ja = {}
        for row in self.dat.table("UniqueStashLayout"):
            k = row.get("WordsKey")
            if k is None or k >= len(words_en):
                continue
            en = words_en[k].get("Text") or ""
            ja = (words_ja[k].get("Text2") if k < len(words_ja) else "") or ""
            if en and ja:
                self.unique_ja.setdefault(en, ja)
        # ユニークの起源（SPEC §6.7）
        origins = self.dat.ids("Origin")
        self.unique_origin = {}
        for row in self.dat.table("UniqueOrigins"):
            w, o = row.get("Unique"), row.get("Origin")
            if w is None or o is None or w >= len(words_en):
                continue
            name = words_en[w].get("Text")
            if name:
                self.unique_origin[name] = (origins[o] or "").lower()
        log(f"  names: base_ja={len(self.base_ja)} unique_ja={len(self.unique_ja)} "
            f"origins={len(self.unique_origin)}")

    def slot_label(self, slot: str) -> tuple[str, str]:
        en, ja = S.DEFAULT_LABELS.get(slot, (slot, ""))
        for cls, mapped in S.CLASS_TO_SLOT.items():
            if mapped == slot and cls in self.class_name:
                cen, cja = self.class_name[cls]
                if cja:
                    return (cen or en, cja)
        return (en, ja or en)

    def slot_labels(self, slot_ids) -> list[str]:
        out = []
        for s in slot_ids:
            en, ja = self.slot_label(s)
            out += [en, ja]
        return out

    # ------------------------------------------------------ 1. 参照テーブル
    def build_reference(self) -> None:
        rows_c = []
        for cid, info in self.item_classes.items():
            en, ja = self.class_name.get(cid, (info.get("name", ""), ""))
            rows_c.append((cid, en or info.get("name", ""), ja,
                           info.get("category_id", ""), S.slot_for_class(cid)))
        self.conn.executemany("INSERT OR REPLACE INTO item_classes VALUES (?,?,?,?,?)", rows_c)

        rows_b = []
        self.base_by_name: dict[str, dict] = {}
        for bid, b in self.base_items.items():
            slot = S.slot_for_class(b.get("item_class"))
            rows_b.append((bid, b.get("name", ""), self.base_ja.get(bid, ""),
                           b.get("item_class", ""), slot or "",
                           ",".join(b.get("tags", [])), b.get("drop_level", 0),
                           b.get("release_state", "")))
            info = dict(b, id=bid, slot=slot)
            self.base_by_name.setdefault(b.get("name", ""), info)
        self.conn.executemany("INSERT OR REPLACE INTO base_items VALUES (?,?,?,?,?,?,?,?)", rows_b)
        log(f"  base_items: {len(rows_b)}  item_classes: {len(rows_c)}")

        rows_t = []
        for tag_id, disp in self.gem_tags.items():
            rows_t.append((tag_id, strip_markup(disp), ""))
        for a, b in self.dat.pair("GemTags"):
            if a.get("Id"):
                rows_t.append((a["Id"], strip_markup(a.get("Name") or ""),
                               strip_markup(b.get("Name") or "")))
        merged: dict[str, tuple[str, str]] = {}
        for tid, en, ja in rows_t:
            cur = merged.get(tid, ("", ""))
            merged[tid] = (en or cur[0], ja or cur[1])
        self.gem_tag_label = merged
        self.conn.executemany("INSERT OR REPLACE INTO gem_tags VALUES (?,?,?)",
                              [(k, v[0], v[1]) for k, v in merged.items()])

    # ------------------------------------------------------------ 2. mod
    MOD_SUBKIND = {"prefix": "prefix", "suffix": "suffix", "corrupted": "corrupted",
                   "essence": "essence"}

    def build_mods(self) -> None:
        applies = self._mods_by_base_index()
        rows = []
        self.mod_text: dict[str, tuple[str, str]] = {}
        wanted_domains = {"item", "flask", "desecrated", "misc"}
        n_ja = 0
        for mid, m in self.mods.items():
            gen = m.get("generation_type", "")
            dom = m.get("domain", "")
            text_en = m.get("text") or ""
            if dom not in wanted_domains:
                continue
            triples = [(s["id"], s.get("min", 0), s.get("max", 0)) for s in m.get("stats", [])]
            ja, src = "", ""
            if text_en and triples:
                _en, ja, src = self.tr.render_stats(triples, expected_en=text_en)
            if not ja and text_en:
                ja, src = self.tr.translate_rendered(text_en)
            if ja:
                n_ja += 1
            sub = self.MOD_SUBKIND.get(gen, "")
            if dom == "desecrated":
                sub = "desecrated"
            if mid.startswith("HandWraps"):
                sub = "handwraps"
            elif mid.startswith("UniqueMutatedVaal"):
                sub = "cultivation"
            self.mod_text[mid] = (text_en, ja)
            rows.append({
                "id": mid, "name": m.get("name", ""), "text_en": text_en, "text_ja": ja,
                "ja_source": src, "generation_type": gen, "domain": dom,
                "required_level": m.get("required_level", 0),
                "mod_group": ",".join(m.get("groups", [])),
                "tags": ",".join(m.get("implicit_tags", [])),
                "stat_ids": ",".join(t[0] for t in triples),
                "stats_json": json.dumps([{"id": t[0], "min": t[1], "max": t[2]}
                                          for t in triples], ensure_ascii=False),
                "is_essence_only": int(bool(m.get("is_essence_only"))),
                "spawn_tags": ",".join(w["tag"] for w in m.get("spawn_weights", [])
                                       if w.get("weight", 0) > 0),
                "sub_kind": sub,
            })
        self.mod_rows = {r["id"]: r for r in rows}

        # --- 石の拳（SPEC §6.6）
        hw_linked = 0
        for r in rows:
            if r["sub_kind"] != "handwraps":
                continue
            origin = r["id"][len("HandWraps"):]
            if origin in self.mod_rows:
                r["transforms_from"] = origin
                self.mod_rows[origin]["handwraps_id"] = r["id"]
                hw_linked += 1
            else:
                r["transforms_from"] = ""
        # --- 培養（SPEC §6.7）
        mod_ids = self.dat.ids("Mods")
        orig_rows = self.dat.table("Incursion2MutatedUniqueModsClient")
        self.cultivation_originals: set[str] = set()
        if orig_rows:
            refs = orig_rows[0].get("Mods") or []
            refs = refs if isinstance(refs, list) else [refs]
            self.cultivation_originals = {mod_ids[i] for i in refs
                                          if i is not None and i < len(mod_ids)}
        for mid in self.cultivation_originals:
            if mid in self.mod_rows:
                self.mod_rows[mid]["cultivation_replaceable"] = 1

        self.conn.executemany(
            "INSERT OR REPLACE INTO mods VALUES (:id,:name,:text_en,:text_ja,:ja_source,"
            ":generation_type,:domain,:required_level,:mod_group,:tags,:stat_ids,:stats_json,"
            ":is_essence_only,:spawn_tags,:sub_kind,:transforms_from,:handwraps_id,"
            ":cultivation_replaceable)",
            [{**{"transforms_from": "", "handwraps_id": "", "cultivation_replaceable": 0}, **r}
             for r in rows])

        # --- 付く装備
        at_rows = []
        self.mod_applies = defaultdict(list)
        for mid, entries in applies.items():
            if mid not in self.mod_rows:
                continue
            # クラス単位にまとめる（タグ組は詳細表示用に持つ）。slot を持たない
            # クラス（HiddenItem 等）は装備ではないので落とす。
            by_class: dict[str, dict] = {}
            for cls, tagset, lvl in entries:
                slot = S.slot_for_class(cls)
                if not slot:
                    continue
                cur = by_class.setdefault(cls, {"item_class": cls, "slot": slot,
                                                "tagsets": [], "required_level": lvl})
                cur["required_level"] = min(cur["required_level"], lvl)
                if tagset not in cur["tagsets"]:
                    cur["tagsets"].append(tagset)
            for cls, info in by_class.items():
                self.mod_applies[mid].append(info)
                at_rows.append((mid, cls, info["slot"],
                                ";".join(info["tagsets"]), info["required_level"]))
        # 石の拳 mod は元 mod の付く装備を引き継ぐ
        for r in rows:
            if r["sub_kind"] == "handwraps":
                origin = r.get("transforms_from")
                src_list = self.mod_applies.get(origin) if origin else None
                self.mod_applies[r["id"]] = [dict(e) for e in src_list] if src_list else [
                    {"item_class": "Gloves", "slot": "gloves", "tagsets": [],
                     "required_level": 0}]
                for e in self.mod_applies[r["id"]]:
                    at_rows.append((r["id"], e["item_class"], e["slot"],
                                    ";".join(e["tagsets"]), e["required_level"]))
        self.conn.executemany(
            "INSERT OR REPLACE INTO mod_applies_to VALUES (?,?,?,?,?)", at_rows)

        self.counts["mods"] = len(rows)
        self.counts["mods_ja"] = n_ja
        log(f"  mods: {len(rows)} (ja {n_ja / max(len(rows), 1):.1%}), "
            f"applies_to {len(at_rows)}, handwraps linked {hw_linked}, "
            f"cultivation originals {len(self.cultivation_originals)}")

    def _mods_by_base_index(self) -> dict[str, list[tuple[str, str, int]]]:
        """mods_by_base を mod_id 起点に反転する（SPEC §6.4）."""
        name_to_class = {}
        for cid, info in self.item_classes.items():
            if info.get("name"):
                name_to_class[info["name"]] = cid
        out: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
        unknown = set()
        for cls_name, tagsets in self.mods_by_base.items():
            cls = name_to_class.get(cls_name)
            if not cls:
                if cls_name:
                    unknown.add(cls_name)
                continue
            for tagset, info in tagsets.items():
                for _gen, groups in (info.get("mods") or {}).items():
                    for _group, mods in groups.items():
                        for mid, lvl in mods.items():
                            out[mid].append((cls, tagset, lvl))
        if unknown:
            log(f"      (mods_by_base: unmapped classes {sorted(unknown)[:6]})")
        return out

    # ------------------------------------------------------- 3. ユニーク
    def build_uniques(self) -> None:
        pob = {i["name"]: i for i in parsers.parse_pob_uniques(self.pob_blocks)}
        self.unique_line_index = self._unique_mod_index()
        self.unique_icon = {k: (u.get("visual_identity") or {}).get("dds_file", "")
                            for k, u in self.uniques.items()}
        rows, line_rows = [], []
        n_ja_name = n_stats = 0
        n_lines = n_matched = 0
        for key, u in self.uniques.items():
            name = u.get("name") or u.get("id") or key
            name_ja = self.unique_ja.get(name, "")
            n_ja_name += bool(name_ja)
            block = pob.get(name)
            base_name = block["base_item"] if block else ""
            base = self.base_by_name.get(base_name)
            slot = (base or {}).get("slot") or S.slot_for_class(u.get("item_class"))
            implicits, stats = [], []
            if block:
                n_stats += 1
                for text in block["implicits"]:
                    implicits.append(self._unique_line(text, slot))
                for text in block["stats"]:
                    stats.append(self._unique_line(text, slot))
            origin = self.unique_origin.get(name, "")
            is_vaal = origin == "vaal"
            for n, line in enumerate(implicits + stats):
                n_lines += 1
                n_matched += bool(line["mod_id"])
                line_rows.append((key, n, int(n < len(implicits)), line["en"], line["ja"],
                                  line["mod_id"], line["match_kind"]))
            # 別アート版は名前が重複するので JSON のキーを主キーにする
            rows.append((key, name, name_ja, u.get("item_class", ""), slot or "",
                         base_name, self.base_ja.get((base or {}).get("id", ""), ""),
                         (base or {}).get("id", ""), int(bool(block)),
                         json.dumps(implicits, ensure_ascii=False),
                         json.dumps(stats, ensure_ascii=False),
                         int(bool(u.get("is_alternate_art"))), origin,
                         int(is_vaal), int(is_vaal and any(
                             l.get("cultivation_replaceable") for l in implicits + stats))))
        self.conn.executemany(
            "INSERT OR REPLACE INTO uniques VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        self.conn.executemany(
            "INSERT OR REPLACE INTO unique_lines VALUES (?,?,?,?,?,?,?)", line_rows)
        self.counts["uniques"] = len(rows)
        log(f"  uniques: {len(rows)} (name_ja {n_ja_name / max(len(rows),1):.1%}, "
            f"pob {n_stats}), lines {n_lines} (mod matched {n_matched / max(n_lines,1):.1%})")

    def _unique_mod_index(self) -> dict[str, list[str]]:
        """ユニーク性能行 → mod ID の索引（SPEC §6.1 手順 7）."""
        idx: dict[str, list[str]] = defaultdict(list)
        for mid, m in self.mods.items():
            if m.get("generation_type") != "unique":
                continue
            if m.get("domain") not in ("item", "flask", "misc"):
                continue
            # 変化後 / 置換後の mod はアイテムに印字される行ではないので候補から外す。
            # 残すと元 mod とペアで曖昧一致になり、石の拳の対応が取れなくなる。
            if mid.startswith(("HandWraps", "UniqueMutatedVaal")):
                continue
            text = m.get("text")
            if not text:
                continue
            key = normalize_for_search(text)
            idx[key].append(mid)
        return idx

    def _unique_line(self, text: str, slot: str | None = None) -> dict:
        en = text
        ja, _src = self.tr.translate_rendered(en)
        key = normalize_for_search(en)
        ids = self.unique_line_index.get(key, [])
        if len(ids) > 1 and slot:
            # 同じ文の mod が複数あるときは、そのアイテムの部位に付くものを優先する
            same_slot = [m for m in ids
                         if any(e["slot"] == slot for e in self.mod_applies.get(m, []))]
            if same_slot:
                ids = same_slot
        mod_id = ids[0] if ids else ""
        kind = "exact" if len(ids) == 1 else ("ambiguous" if ids else "none")
        line = {"en": strip_markup(en), "ja": strip_markup(ja),
                "mod_id": mod_id, "match_kind": kind}
        if not ids:
            return line
        # 候補が複数のときは、全候補で同じ結論になるときだけ付加情報を出す（SPEC §6.1 手順 7）
        hw_texts = set()
        for cand in ids:
            row = self.mod_rows.get(cand)
            hw = self.mod_rows.get(row["handwraps_id"]) if row and row.get("handwraps_id") else None
            hw_texts.add((hw["id"], strip_markup(hw["text_en"]), strip_markup(hw["text_ja"]))
                         if hw else None)
        if len(hw_texts) == 1:
            only = next(iter(hw_texts))
            if only:
                line["handwraps"] = {"mod_id": only[0], "en": only[1], "ja": only[2]}
        cult = {cand in self.cultivation_originals for cand in ids}
        if cult == {True}:
            line["cultivation_replaceable"] = True
        return line

    # --------------------------------------------- 4. パッシブ（ノータブル）
    def build_passives(self) -> None:
        ps_en = self.dat.table("PassiveSkills", "English")
        ps_ja = self.dat.table("PassiveSkills", "Japanese")
        by_id = {r.get("Id"): n for n, r in enumerate(ps_en) if r.get("Id")}
        by_hash = {r.get("PassiveSkillGraphId"): n for n, r in enumerate(ps_en)}

        asc_meta = {}
        for aid, a in self.ascendancies.items():
            name = a.get("name") or ""
            if a.get("disabled") or "[DNT" in name:
                continue
            char = a.get("character") or []
            asc_meta[aid] = {"name_en": name,
                             "class_en": char[1] if len(char) > 1 else "",
                             "name_ja": "", "class_ja": ""}
        for a, b in self.dat.pair("Ascendancy"):
            if a.get("Id") in asc_meta:
                asc_meta[a["Id"]]["name_ja"] = b.get("Name") or ""
        char_ja = {a.get("Id"): b.get("Name") or "" for a, b in self.dat.pair("Characters")}
        for meta in asc_meta.values():
            meta["class_ja"] = char_ja.get(meta["class_en"], "")
        self.asc_meta = asc_meta
        self.conn.executemany(
            "INSERT OR REPLACE INTO ascendancies VALUES (?,?,?,?,?,?)",
            [(k, v["name_en"], v["name_ja"], v["class_en"], v["class_ja"], 0)
             for k, v in asc_meta.items()])

        rows = []
        n_ja = n_hit_id = n_hit_hash = total = 0
        skipped_asc = set()
        for h, node in self.tree["passives"].items():
            asc = node.get("ascendancy")
            is_notable = bool(node.get("is_notable"))
            is_key = bool(node.get("is_keystone"))
            if asc and asc not in asc_meta:
                skipped_asc.add(asc)
                continue
            if not asc and not (is_notable or is_key):
                continue
            if node.get("is_jewel_socket") or node.get("is_icon_only") \
                    or node.get("is_multiple_choice") or node.get("is_atlas_root"):
                continue
            name_en = (node.get("name") or "").strip()
            stats = node.get("stats") or {}
            if not name_en and not stats:
                continue
            total += 1
            idx = by_id.get(node.get("id"))
            if idx is not None:
                n_hit_id += 1
            else:
                idx = by_hash.get(int(h))
                if idx is not None:
                    n_hit_hash += 1
            name_ja = (ps_ja[idx].get("Name") or "") if idx is not None else ""
            flavour_ja = (ps_ja[idx].get("FlavourText") or "") if idx is not None else ""
            triples = [(sid, val, val) for sid, val in stats.items()]
            en_text, ja_text, _src = self.tr.render_stats(
                triples, translation_file="passive_skill_stat_descriptions.csd")
            lines = _zip_lines(en_text, ja_text)
            n_ja += bool(ja_text)
            rows.append((int(h), node.get("id", ""), name_en, name_ja,
                         int(is_notable), int(is_key),
                         int(bool(node.get("is_ascendancy_starting_node"))),
                         int(not (is_notable or is_key)), asc or "",
                         json.dumps(stats, ensure_ascii=False),
                         json.dumps(lines, ensure_ascii=False),
                         node.get("flavour_text", ""), flavour_ja,
                         node.get("icon", "")))
        self.conn.executemany(
            "INSERT OR REPLACE INTO passives VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        self.counts["passives"] = len(rows)
        log(f"  passives: {len(rows)} (ja text {n_ja / max(total,1):.1%}; "
            f"name by Id {n_hit_id}, by hash {n_hit_hash}); "
            f"skipped legacy ascendancies: {len(skipped_asc)}")

    # ------------------------------------------------------ 5. socketable
    def build_socketables(self) -> None:
        bit = self.dat.table("BaseItemTypes")
        stats_ids = self.dat.ids("Stats")
        ic = self.dat.table("ItemClasses")
        cs_en = self.dat.table("ClientStrings2", "English")
        cs_ja = self.dat.table("ClientStrings2", "Japanese")
        types = self.dat.pair("SoulCoreTypes")
        limits = self.dat.pair("SoulCoreLimits")
        cats = self.dat.pair("SoulCoreStatCategories")

        cat_info = []
        for a, b in cats:
            tc = a.get("TargetItemClasses") or []
            tc = tc if isinstance(tc, list) else [tc]
            classes = [ic[i]["Id"] for i in tc if i is not None and i < len(ic)]
            cat_info.append({
                "id": a.get("Id", ""),
                "en": strip_markup(a.get("Display") or "") or ", ".join(
                    self.class_name.get(c, (c, ""))[0] for c in classes),
                "ja": strip_markup(b.get("Display") or "") or "・".join(
                    self.class_name.get(c, (c, ""))[1] for c in classes),
                "classes": classes,
                "slots": S.slots_for_category(a.get("Id", ""), classes),
            })

        by_base: dict[str, dict] = {}
        for row in self.dat.table("SoulCores"):
            b = row.get("BaseItemType")
            if b is None or b >= len(bit):
                continue
            by_base[bit[b]["Id"]] = row
        effects: dict[str, list[dict]] = defaultdict(list)
        sc_rows = self.dat.table("SoulCores")
        for row in self.dat.table("SoulCoreStats"):
            sc = row.get("SoulCore")
            if sc is None or sc >= len(sc_rows):
                continue
            b = sc_rows[sc].get("BaseItemType")
            if b is None or b >= len(bit):
                continue
            base_id = bit[b]["Id"]
            cat = row.get("StatCategory")
            info = cat_info[cat] if cat is not None and cat < len(cat_info) else None
            triples = []
            ids = row.get("Stats") or []
            vals = row.get("StatsValues") or []
            for n, sid in enumerate(ids if isinstance(ids, list) else [ids]):
                if sid is None or sid >= len(stats_ids):
                    continue
                v = vals[n] if n < len(vals) else 0
                triples.append((stats_ids[sid], v, v))
            if not triples:
                continue
            en, ja, _ = self.tr.render_stats(triples)
            effects[base_id].append({
                "category_id": (info or {}).get("id", ""),
                "category_en": (info or {}).get("en", ""),
                "category_ja": (info or {}).get("ja", ""),
                "target_classes": (info or {}).get("classes", []),
                "target_slots": (info or {}).get("slots", []),
                "lines": _zip_lines(en, ja),
            })

        rows, eff_rows = [], []
        n_ja = 0
        for bid, b in self.base_items.items():
            if b.get("item_class") != "SoulCore" or b.get("release_state") != "released":
                continue
            core = by_base.get(bid, {})
            t = core.get("Type")
            type_en = types[t][0].get("Name", "") if t is not None and t < len(types) else ""
            type_ja = types[t][1].get("Name", "") if t is not None and t < len(types) else ""
            lim = core.get("Limit")
            lim_en = limits[lim][0].get("Text", "") if lim is not None and lim < len(limits) else ""
            lim_ja = limits[lim][1].get("Text", "") if lim is not None and lim < len(limits) else ""
            d = core.get("Description")
            desc_en = cs_en[d].get("Text", "") if d is not None and d < len(cs_en) else ""
            desc_ja = cs_ja[d].get("Text", "") if d is not None and d < len(cs_ja) else ""
            name_en = b.get("name", "")
            tier = next((t for t in ("Lesser", "Greater", "Perfect")
                         if name_en.startswith(t + " ")), "Normal")
            flags = {"socket_bound": bool(core.get("IsSocketBound")),
                     "unique_items": bool(core.get("CanSocketInUniqueItems")),
                     "jewellery": bool(core.get("CanSocketInJewellery")),
                     "martial_artist": bool(core.get("CanSocketInMartialArtistSlots")),
                     "corrupted_sanctified": bool(core.get("CanSocketInCorruptedSanctified"))}
            eff = effects.get(bid, [])
            if any(any(l["ja"] for l in e["lines"]) for e in eff):
                n_ja += 1
            rows.append((bid, name_en, self.base_ja.get(bid, ""),
                         strip_markup(type_en), strip_markup(type_ja), tier,
                         core.get("RequiredLevel", 0) or b.get("drop_level", 0),
                         strip_markup(lim_en), strip_markup(lim_ja),
                         strip_markup(desc_en), strip_markup(desc_ja),
                         json.dumps(flags), ",".join(b.get("tags", []))))
            for e in eff:
                eff_rows.append((bid, e["category_id"], e["category_en"], e["category_ja"],
                                 ",".join(e["target_classes"]), ",".join(e["target_slots"]),
                                 json.dumps(e["lines"], ensure_ascii=False)))
        self.conn.executemany(
            "INSERT OR REPLACE INTO socketables VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        self.conn.executemany(
            "INSERT OR REPLACE INTO socketable_effects VALUES (?,?,?,?,?,?,?)", eff_rows)
        self.socketable_effects = effects
        self.counts["socketables"] = len(rows)
        log(f"  socketables: {len(rows)} (ja {n_ja / max(len(rows),1):.1%}), "
            f"effects {len(eff_rows)}")

    # ---------------------------------------------------------- 6. ジェム
    def build_gems(self) -> None:
        act_en = {r.get("Id"): r for r in self.dat.table("ActiveSkills", "English")}
        act_ja = {}
        for a, b in self.dat.pair("ActiveSkills"):
            if a.get("Id"):
                act_ja[a["Id"]] = b
        gem_eff = {}
        for a, b in self.dat.pair("GemEffects"):
            key = normalize_for_search(a.get("SupportText") or "")
            if key:
                gem_eff.setdefault(key, b.get("SupportText") or "")
        rows = []
        self.gem_icon = {}
        n_ja = n_detail = n_detail_ja = 0
        for gid, g in self.skill_gems.items():
            base = g.get("base_item") or {}
            name_en = base.get("display_name") or ""
            if not name_en or name_en.startswith("[DNT"):
                continue
            tags = list(g.get("tags") or [])
            gem_type = g.get("gem_type", "")
            is_lineage = bool(g.get("is_lineage")) or "lineage" in tags
            sub = ("spirit" if gem_type == "spirit"
                   else "lineage" if is_lineage
                   else gem_type)
            skill_key = (g.get("grants_skills") or [None])[0]
            skill = self.skills.get(skill_key) if skill_key else None
            active = (skill or {}).get("active_skill") or {}
            skill_id = active.get("id", "")
            a_en, a_ja = act_en.get(skill_id, {}), act_ja.get(skill_id, {})
            summary_en = strip_markup(a_en.get("ShortDescription") or "")
            summary_ja = strip_markup(a_ja.get("ShortDescription") or "")
            desc_en = strip_markup(a_en.get("Description") or "")
            desc_ja = strip_markup(a_ja.get("Description") or "")
            if not desc_en:
                # サポートの説明は repoe 側 support_text ↔ GemEffects 英文完全一致
                raw = g.get("support_text") or ""
                desc_en = strip_markup(raw)
                desc_ja = strip_markup(gem_eff.get(normalize_for_search(raw), ""))
            self.gem_icon[gid] = g.get("icon_dds_file", "")
            detail = self._gem_detail(skill)
            n_detail += len(detail)
            n_detail_ja += sum(1 for d in detail if d.get("ja"))
            if summary_ja or desc_ja:
                n_ja += 1
            req = g.get("requirement_weights") or {}
            rows.append((gid, name_en, self.base_ja.get(gid, ""), gem_type,
                         int(is_lineage), g.get("color", ""),
                         ",".join(tags), ",".join(active.get("types") or []),
                         req.get("str", 0), req.get("dex", 0), req.get("int", 0),
                         g.get("crafting_level", 0) or 0,
                         (skill or {}).get("cast_time", 0) or 0,
                         skill_id, summary_en, summary_ja, desc_en, desc_ja,
                         json.dumps(detail, ensure_ascii=False),
                         ",".join(self.base_items.get(s, {}).get("name", "")
                                  for s in (g.get("recommended_supports") or [])),
                         ",".join(active.get("weapon_restrictions") or [])))
        self.conn.executemany(
            "INSERT OR REPLACE INTO gems VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        self.counts["gems"] = len(rows)
        log(f"  gems: {len(rows)} (desc ja {n_ja / max(len(rows),1):.1%}; "
            f"detail {n_detail}, ja {n_detail_ja / max(n_detail,1):.1%})")

    def _gem_detail(self, skill) -> list[dict]:
        if not skill:
            return []
        out: list[dict] = []
        for ss in skill.get("stat_sets") or []:
            static = ss.get("static") or {}
            for _ids, text in (static.get("stat_text") or {}).items():
                if not text:
                    continue
                ja, _ = self.tr.translate_rendered(text)
                out.append({"en": strip_markup(text), "ja": strip_markup(ja)})
            per = ss.get("per_level") or {}
            levels = []
            available = sorted(int(k) for k in per if str(k).isdigit())
            top = 20 if 20 in available else (available[-1] if available else 0)
            for lv in dict.fromkeys(str(x) for x in (1, top) if x):
                info = per.get(lv) or {}
                for _ids, text in (info.get("stat_text") or {}).items():
                    if not text:
                        continue
                    ja, _ = self.tr.translate_rendered(text)
                    levels.append({"lv": int(lv), "en": strip_markup(text),
                                   "ja": strip_markup(ja)})
            if levels:
                out.append({"levels": levels})
        return out

    # ----------------------------------------------------- 7. タイムレス
    TIMELESS = {"Kalguuran": ("kalguur", "Heroic Tragedy"),
                "Abyss": ("abyss", "Undying Hate")}

    def build_timeless(self) -> None:
        versions = self.dat.table("AlternateTreeVersions")
        stats_ids = self.dat.ids("Stats")
        keep = {n: self.TIMELESS[v.get("ConquerorType")]
                for n, v in enumerate(versions) if v.get("ConquerorType") in self.TIMELESS}
        rows = []
        for table, is_add in (("AlternatePassiveSkills", False),
                              ("AlternatePassiveAdditions", True)):
            pairs = self.dat.pair(table)
            for a, b in pairs:
                ver = a.get("AlternateTreeVersion")
                if ver not in keep:
                    continue
                jewel, jewel_name = keep[ver]
                ptypes = a.get("PassiveType") or []
                ptypes = ptypes if isinstance(ptypes, list) else [ptypes]
                if is_add:
                    kind = "addition"
                elif 4 in ptypes:
                    kind = "keystone"
                elif 3 in ptypes:
                    kind = "notable"
                else:
                    kind = "small"
                ids = a.get("Stats") or []
                ids = ids if isinstance(ids, list) else [ids]
                triples = []
                for n, sid in enumerate(ids):
                    if sid is None or sid >= len(stats_ids):
                        continue
                    rng = a.get(f"Stat{n + 1}") or [0, 0]
                    lo, hi = (rng + [0, 0])[:2] if isinstance(rng, list) else (rng, rng)
                    triples.append((stats_ids[sid], lo, hi))
                en, ja, _ = self.tr.render_stats(
                    triples, translation_file="passive_skill_stat_descriptions.csd")
                rows.append((f"{jewel}:{a.get('Id','')}", jewel,
                             a.get("Name") or "", b.get("Name") or "", kind,
                             a.get("ConquerorIndex", 0) or 0, a.get("SpawnWeight", 0) or 0,
                             json.dumps({t[0]: [t[1], t[2]] for t in triples},
                                        ensure_ascii=False),
                             json.dumps(_zip_lines(en, ja), ensure_ascii=False),
                             a.get("FlavourText") or "", b.get("FlavourText") or "",
                             a.get("DDSIcon") or "", jewel_name))
        self.conn.executemany(
            "INSERT OR REPLACE INTO timeless_passives VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        self.counts["timeless"] = len(rows)
        log(f"  timeless: {len(rows)}")

    # ------------------------------------------------- 8. search_docs 射影
    def build_search_docs(self) -> None:
        cur = self.conn.cursor()
        docs: list[tuple] = []

        def add(doc_id, kind, sub, slot_ids, name_en, name_ja, group_en, group_ja,
                lines, meta, extra_hay=(), sort_key=0, slot_hay=True, icon=""):
            slot_ids = list(slot_ids or [])
            # mod は付く装備が多く、部位ラベルを haystack に入れると容量が跳ねる。
            # 部位での絞り込みは slot チップ（slots 配列）が担当する。
            hay = haystack(name_en, name_ja, group_en, group_ja, sub,
                           [l.get("en") for l in lines], [l.get("ja") for l in lines],
                           self.slot_labels(slot_ids) if slot_hay else (), extra_hay)
            docs.append((doc_id, kind, sub, ",".join(slot_ids), name_en, name_ja,
                         group_en, group_ja, json.dumps(lines, ensure_ascii=False),
                         json.dumps(meta, ensure_ascii=False), hay, sort_key,
                         _art_path(icon)))

        # unique
        for r in cur.execute("SELECT * FROM uniques ORDER BY name_en"):
            (uid, name_en, name_ja, item_class, slot, base_en, base_ja, base_id,
             has_stats, impl_json, stats_json, alt_art, origin, is_vaal, cult) = r
            implicits = json.loads(impl_json)
            stats = json.loads(stats_json)
            meta = {"base_item_en": base_en, "base_item_ja": base_ja,
                    "item_class": item_class, "implicits": implicits,
                    "has_stats": bool(has_stats), "is_alternate_art": bool(alt_art),
                    "origin": origin, "is_vaal_unique": bool(is_vaal),
                    "cultivation_target": bool(cult)}
            hw = [l["handwraps"]["en"] for l in implicits + stats if l.get("handwraps")]
            hw += [l["handwraps"]["ja"] for l in implicits + stats if l.get("handwraps")]
            # 部位ごとに塊で並ぶように、slot の表示順を sort_key にする
            slot_rank = (S.SLOT_ORDER.index(slot)
                         if slot in S.SLOT_ORDER else len(S.SLOT_ORDER))
            add(f"unique:{uid}", "unique", "", S.with_parents([slot]) if slot else [],
                name_en, name_ja, base_en, base_ja, stats, meta,
                extra_hay=[l.get("en") for l in implicits] +
                          [l.get("ja") for l in implicits] + hw + [origin],
                sort_key=slot_rank, icon=self.unique_icon.get(uid, ""))

        # notable / ascendancy
        for r in cur.execute("SELECT * FROM passives"):
            (h, node_id, name_en, name_ja, is_notable, is_key, is_start, is_small,
             asc, stats_json, lines_json, flav_en, flav_ja, icon) = r
            lines = json.loads(lines_json)
            if asc:
                meta_asc = self.asc_meta.get(asc, {})
                meta = {"ascendancy_id": asc, "ascendancy_en": meta_asc.get("name_en", ""),
                        "ascendancy_ja": meta_asc.get("name_ja", ""),
                        "class_en": meta_asc.get("class_en", ""),
                        "class_ja": meta_asc.get("class_ja", ""),
                        "is_notable": bool(is_notable), "is_start": bool(is_start),
                        "hash": h, "stats": json.loads(stats_json)}
                sub = "start" if is_start else ("notable" if is_notable else "small")
                add(f"ascendancy:{h}", "ascendancy", sub, [], name_en, name_ja,
                    meta_asc.get("name_en", ""), meta_asc.get("name_ja", ""), lines, meta,
                    extra_hay=[asc, meta_asc.get("class_en"), meta_asc.get("class_ja"),
                               flav_en, flav_ja], icon=icon)
            else:
                # キーストーンはノータブルと別 kind にして、チップで切り分けられるようにする
                kind = "keystone" if is_key else "notable"
                meta = {"hash": h, "node_id": node_id, "is_keystone": bool(is_key),
                        "stats": json.loads(stats_json),
                        "flavour_en": flav_en, "flavour_ja": flav_ja}
                add(f"{kind}:{h}", kind, kind, [], name_en, name_ja, "", "", lines, meta,
                    extra_hay=[flav_en, flav_ja], icon=icon)

        # mod
        for r in cur.execute("SELECT * FROM mods"):
            (mid, name, text_en, text_ja, ja_src, gen, dom, lvl, group, tags, stat_ids,
             stats_json, ess, spawn, sub, tfrom, hwid, cult) = r
            if not text_en:
                continue  # 表示文が無い mod（DummyStatDisplayNothing 等）は載せない
            applies = self.mod_applies.get(mid, [])
            for e in applies:
                en, ja = self.slot_label(e["slot"]) if e["slot"] else ("", "")
                e["item_class_en"], e["item_class_ja"] = (
                    self.class_name.get(e["item_class"], (e["item_class"], "")))
                e.setdefault("slot_en", en)
                e.setdefault("slot_ja", ja)
            meta = {"mod_id": mid, "generation_type": gen, "domain": dom,
                    "required_level": lvl, "mod_group": group, "tags": tags,
                    "stat_ids": stat_ids, "stat_ranges": json.loads(stats_json),
                    "is_essence_only": bool(ess), "applies_to": applies,
                    "cultivation_replaceable": bool(cult)}
            extra = [spawn]
            if tfrom and tfrom in self.mod_rows:
                src = self.mod_rows[tfrom]
                meta["transforms_from"] = {"mod_id": tfrom,
                                           "en": strip_markup(src["text_en"]),
                                           "ja": strip_markup(src["text_ja"])}
                extra += [src["text_en"], src["text_ja"]]
            if hwid and hwid in self.mod_rows:
                hw = self.mod_rows[hwid]
                meta["handwraps"] = {"mod_id": hwid, "en": strip_markup(hw["text_en"]),
                                     "ja": strip_markup(hw["text_ja"])}
            slot_ids = S.with_parents([e["slot"] for e in applies if e["slot"]])
            text_ja = strip_markup(text_ja)
            add(f"mod:{mid}", "mod", sub or gen, slot_ids, strip_markup(text_en), text_ja,
                name or "", "", [{"en": strip_markup(text_en), "ja": text_ja}], meta,
                extra_hay=extra, sort_key=lvl or 0, slot_hay=False)

        # socketable
        for r in cur.execute("SELECT * FROM socketables ORDER BY name_en"):
            (bid, name_en, name_ja, type_en, type_ja, tier, lvl, lim_en, lim_ja,
             desc_en, desc_ja, flags, tags) = r
            eff = self.socketable_effects.get(bid, [])
            lines = []
            for e in eff:
                for l in e["lines"]:
                    lines.append({
                        "en": f"{e['category_en']}: {l['en']}" if e["category_en"] else l["en"],
                        "ja": f"{e['category_ja']}: {l['ja']}" if e["category_ja"] and l["ja"]
                              else l["ja"]})
            slot_ids = S.with_parents({s for e in eff for s in e["target_slots"]})
            meta = {"base_id": bid, "type_en": type_en, "type_ja": type_ja, "tier": tier,
                    "required_level": lvl, "limit_en": lim_en, "limit_ja": lim_ja,
                    "flags": json.loads(flags), "effects": eff,
                    "description_en": desc_en, "description_ja": desc_ja}
            add(f"socketable:{bid}", "socketable", type_en.lower() or "rune", slot_ids,
                name_en, name_ja, type_en, type_ja, lines, meta,
                extra_hay=[desc_en, desc_ja, tier], sort_key=lvl or 0,
                icon=(self.base_items.get(bid, {}).get("visual_identity") or {}).get("dds_file", ""))

        # gem
        for r in cur.execute("SELECT * FROM gems ORDER BY name_en"):
            (gid, name_en, name_ja, gem_type, is_lin, color, tags, skill_types,
             rs, rd, ri, lvl, cast, skill_id, sum_en, sum_ja, d_en, d_ja,
             detail_json, rec, weap) = r
            detail = json.loads(detail_json)
            tag_list = [t for t in tags.split(",") if t]
            shown_tags = [t for t in tag_list if t not in HIDDEN_GEM_TAGS]
            sub = "lineage" if is_lin else gem_type
            lines = [{"en": sum_en, "ja": sum_ja}] if sum_en else []
            if d_en:
                lines.append({"en": d_en, "ja": d_ja})
            flat = []
            for d in detail:
                if "levels" in d:
                    flat += [{"en": x["en"], "ja": x["ja"]} for x in d["levels"]
                             if x["lv"] == 20]
                else:
                    flat.append(d)
            lines += flat
            label_en = [self.gem_tag_label.get(t, (t, ""))[0] for t in shown_tags]
            label_ja = [self.gem_tag_label.get(t, ("", ""))[1] for t in shown_tags]
            meta = {"gem_id": gid, "gem_type": gem_type, "is_lineage": bool(is_lin),
                    "color": color, "tags": shown_tags,
                    "tags_en": label_en, "tags_ja": label_ja,
                    "skill_types": [t for t in skill_types.split(",") if t],
                    "req_str": rs, "req_dex": rd, "req_int": ri, "level_req": lvl,
                    "cast_time": cast, "summary_en": sum_en, "summary_ja": sum_ja,
                    "desc_en": d_en, "desc_ja": d_ja, "detail": detail,
                    "recommended_supports": [x for x in rec.split(",") if x],
                    "weapon_restrictions": [x for x in weap.split(",") if x]}
            add(f"gem:{gid}", "gem", sub, [], name_en, name_ja,
                GEM_GROUP[sub][0], GEM_GROUP[sub][1], lines, meta,
                extra_hay=label_en + label_ja + shown_tags, sort_key=lvl or 0,
                icon=self.gem_icon.get(gid, ""))

        # timeless
        for r in cur.execute("SELECT * FROM timeless_passives"):
            (tid, jewel, name_en, name_ja, kind, ci, weight, stats_json, lines_json,
             flav_en, flav_ja, icon, jewel_name) = r
            lines = json.loads(lines_json)
            jewel_ja = self.unique_ja.get(jewel_name, "")
            meta = {"jewel": jewel, "jewel_name_en": jewel_name, "jewel_name_ja": jewel_ja,
                    "node_id": tid.split(":", 1)[1], "passive_type": kind,
                    "conqueror_index": ci, "spawn_weight": weight,
                    "stats": json.loads(stats_json),
                    "flavour_en": flav_en, "flavour_ja": flav_ja}
            add(f"timeless:{tid}", "timeless", kind, [], name_en, name_ja,
                jewel_name, jewel_ja, lines, meta, extra_hay=[flav_en, flav_ja, jewel],
                icon=icon)

        self.conn.executemany(
            "INSERT OR REPLACE INTO search_docs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", docs)
        self.conn.executemany(
            "INSERT INTO search_fts (id, haystack) VALUES (?,?)",
            [(d[0], d[10]) for d in docs])
        by_kind = defaultdict(int)
        for d in docs:
            by_kind[d[1]] += 1
        self.counts["search_docs"] = dict(by_kind)
        log(f"  search_docs: {len(docs)} {dict(by_kind)}")

    def finish(self) -> None:
        self.conn.executemany(
            "INSERT OR REPLACE INTO meta VALUES (?,?)",
            [("patch_version", self.version),
             ("built_at", time.strftime("%Y-%m-%dT%H:%M:%S")),
             ("source_counts", json.dumps(self.counts, ensure_ascii=False))])
        self.conn.commit()
        if parsers.UNKNOWN_HANDLERS:
            log(f"  !! unknown csd handlers: {sorted(parsers.UNKNOWN_HANDLERS)}")


HIDDEN_GEM_TAGS = {"grants_active_skill", "support", "meta", "low_max_level",
                   "exceptional", "awakened", "vaal", "link"}
GEM_GROUP = {"active": ("Skill", "スキル"), "support": ("Support", "サポート"),
             "lineage": ("Lineage Support", "リネージュサポート"),
             "spirit": ("Spirit", "スピリット")}


def _art_path(dds: str) -> str:
    """`Art/2DItems/…/X.dds` → `2DItems/…/X.dds`（先頭の `Art/` を落とす）."""
    p = (dds or "").replace("\\", "/")
    return p[4:] if p.startswith("Art/") else p


def _zip_lines(en_text: str, ja_text: str) -> list[dict]:
    """改行区切りの EN/JA を行ごとの [{en, ja}] にする."""
    en_lines = [l for l in (en_text or "").split("\n") if l]
    ja_lines = [l for l in (ja_text or "").split("\n") if l]
    if len(ja_lines) != len(en_lines):
        ja_lines = ja_lines + [""] * (len(en_lines) - len(ja_lines))
    return [{"en": strip_markup(e), "ja": strip_markup(j)}
            for e, j in zip(en_lines, ja_lines)]


def main() -> None:
    if DB_PATH.exists():
        DB_PATH.unlink()
    t0 = time.time()
    b = Builder()
    b.schema()
    b.build_names()
    b.build_reference()
    b.build_mods()
    b.build_uniques()
    b.build_passives()
    b.build_socketables()
    b.build_gems()
    b.build_timeless()
    b.build_search_docs()
    b.finish()
    log(f"done in {time.time() - t0:.1f}s -> {DB_PATH}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
