"""poe2db: SQLite → Web 用 JSON（docs/SPEC.md §8）.

    python export_web.py

UI が実際に表示するものだけを、配列中心の圧縮した形で書き出す。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import slots as S

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "poe2db.sqlite"
OUT = ROOT / "web_data.json"


def compact_lines(lines: list[dict]) -> list:
    """[{en,ja,handwraps,cultivation_replaceable}] → [en, ja, [hwEn, hwJa], cult]."""
    out = []
    for l in lines:
        row = [l.get("en", ""), l.get("ja", "")]
        hw = l.get("handwraps")
        cult = 1 if l.get("cultivation_replaceable") else 0
        if hw or cult:
            row.append([hw.get("en", ""), hw.get("ja", "")] if hw else 0)
        if cult:
            row.append(1)
        out.append(row)
    return out


def trim_meta(kind: str, meta: dict, slot_label) -> dict:
    """UI が使う項目だけ残す（mod の tagsets / stat_ranges は容量が大きいので落とす）."""
    if kind == "mod":
        out = {
            "gen": meta.get("generation_type", ""),
            "lvl": meta.get("required_level", 0),
            "grp": meta.get("mod_group", "").split(",")[0],
            # 付く装備は slot ID と必要レベルだけ（ラベルは UI が slots から引く）
            "ap": [[a.get("slot", ""), a.get("required_level", 0)]
                   for a in meta.get("applies_to", [])],
        }
        if meta.get("tags"):
            out["tags"] = meta["tags"]
        if meta.get("transforms_from"):
            t = meta["transforms_from"]
            out["from"] = [t.get("en", ""), t.get("ja", "")]
        if meta.get("handwraps"):
            h = meta["handwraps"]
            out["hw"] = [h.get("en", ""), h.get("ja", "")]
        if meta.get("cultivation_replaceable"):
            out["cult"] = 1
        if meta.get("is_essence_only"):
            out["ess"] = 1
        if meta.get("on_uniques"):
            out["uq"] = [[u["id"], u["ja"] or u["en"]] for u in meta["on_uniques"]]
        if meta.get("orphan"):
            out["orphan"] = 1
        return out

    if kind == "unique":
        out = {
            "base": [meta.get("base_item_en", ""), meta.get("base_item_ja", "")],
            "cls": meta.get("item_class", ""),
            "imp": compact_lines(meta.get("implicits") or []),
        }
        if meta.get("origin"):
            out["origin"] = meta["origin"]
        if meta.get("is_vaal_unique"):
            out["vaal"] = 1
        if meta.get("cultivation_target"):
            out["cult"] = 1
        if meta.get("is_alternate_art"):
            out["alt"] = 1
        if not meta.get("has_stats"):
            out["nostats"] = 1
        return out

    if kind == "gem":
        out = {
            "type": meta.get("gem_type", ""),
            "color": meta.get("color", ""),
            "tags": meta.get("tags") or [],
            "tja": meta.get("tags_ja") or [],
            "req": [meta.get("req_str", 0), meta.get("req_dex", 0), meta.get("req_int", 0)],
            "lvl": meta.get("level_req", 0),
        }
        if meta.get("is_lineage"):
            out["lin"] = 1
        if meta.get("cast_time"):
            out["cast"] = meta["cast_time"]
        if meta.get("summary_en") or meta.get("summary_ja"):
            out["sum"] = [meta.get("summary_en", ""), meta.get("summary_ja", "")]
        if meta.get("desc_en") or meta.get("desc_ja"):
            out["desc"] = [meta.get("desc_en", ""), meta.get("desc_ja", "")]
        detail = []
        for d in meta.get("detail") or []:
            if "levels" in d:
                detail.append({"lv": [[x.get("lv"), x.get("en", ""), x.get("ja", "")]
                                      for x in d["levels"]]})
            elif d.get("en"):
                detail.append([d.get("en", ""), d.get("ja", "")])
        if detail:
            out["det"] = detail
        if meta.get("recommended_supports"):
            out["rec"] = meta["recommended_supports"]
        if meta.get("weapon_restrictions"):
            out["weap"] = meta["weapon_restrictions"]
        if meta.get("skill_types"):
            out["types"] = meta["skill_types"]
        return out

    if kind == "socketable":
        return {
            "type": [meta.get("type_en", ""), meta.get("type_ja", "")],
            "tier": meta.get("tier", ""),
            "lvl": meta.get("required_level", 0),
            "limit": [meta.get("limit_en", ""), meta.get("limit_ja", "")],
            "desc": [meta.get("description_en", ""), meta.get("description_ja", "")],
            "eff": [{"c": [e.get("category_en", ""), e.get("category_ja", "")],
                     "s": e.get("target_slots", []),
                     "l": compact_lines(e.get("lines", []))}
                    for e in meta.get("effects") or []],
            "flags": {k: 1 for k, v in (meta.get("flags") or {}).items() if v},
        }

    if kind == "ascendancy":
        return {
            "asc": meta.get("ascendancy_id", ""),
            "an": [meta.get("ascendancy_en", ""), meta.get("ascendancy_ja", "")],
            "cl": [meta.get("class_en", ""), meta.get("class_ja", "")],
        }

    if kind == "notable":
        out = {}
        if meta.get("is_keystone"):
            out["key"] = 1
        if meta.get("flavour_en") or meta.get("flavour_ja"):
            out["flav"] = [meta.get("flavour_en", ""), meta.get("flavour_ja", "")]
        return out

    if kind == "timeless":
        out = {
            "jewel": meta.get("jewel", ""),
            "jn": [meta.get("jewel_name_en", ""), meta.get("jewel_name_ja", "")],
            "w": meta.get("spawn_weight", 0),
        }
        if meta.get("conqueror_index"):
            out["ci"] = meta["conqueror_index"]
        return out

    if kind == "keyword":
        return {}

    return {}


def load_overrides() -> dict:
    """`overrides.json`（UI の「手直し」の書き出し）を取り込む.

    形: `{"unique:151": {"n": "日本語名", "u": "URL", "l": {"0": "1 行目の訳"}}}`
    ゲームデータ側は触らず、表示に重ねるだけ。パッチを取り直しても残る。
    """
    path = ROOT / "overrides.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"overrides.json が読めません: {exc}")
    if not isinstance(data, dict):
        raise SystemExit("overrides.json は {doc_id: {...}} の形である必要があります。")
    print(f"  overrides.json: {len(data)} 件", flush=True)
    return data


def main() -> None:
    if not DB_PATH.exists():
        raise SystemExit("poe2db.sqlite がありません。先に python build_db.py を実行してください。")
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    meta_rows = {r["key"]: r["value"] for r in conn.execute("SELECT * FROM meta")}

    class_ja = {r["id"]: (r["name_en"], r["name_ja"])
                for r in conn.execute("SELECT * FROM item_classes")}

    def slot_label(slot: str) -> tuple[str, str]:
        en, ja = S.DEFAULT_LABELS.get(slot, (slot, slot))
        for cls, mapped in S.CLASS_TO_SLOT.items():
            if mapped == slot and cls in class_ja and class_ja[cls][1]:
                return (class_ja[cls][0] or en, class_ja[cls][1])
        return (en, ja)

    docs = []
    used_slots: Counter[str] = Counter()
    tag_counts: dict[str, Counter] = {}
    icons: dict[str, int] = {}   # `Art/` を除いた .dds パス → 添字
    for r in conn.execute("SELECT * FROM search_docs ORDER BY kind, sort_key, name_en"):
        kind = r["kind"]
        meta = json.loads(r["meta_json"])
        slot_ids = [s for s in (r["slots"] or "").split(",") if s]
        used_slots.update(slot_ids)
        if kind == "gem":
            bucket = tag_counts.setdefault(meta.get("gem_type", ""), Counter())
            bucket.update(meta.get("tags") or [])
            if meta.get("is_lineage"):
                tag_counts.setdefault("lineage", Counter()).update(meta.get("tags") or [])
        # mod は本文＝名前、gem は本文が meta 側にあるので lines を重複させない
        lines = ([] if kind in ("mod", "gem")
                 else compact_lines(json.loads(r["lines_json"])))
        icon = r["icon"] or ""
        if icon and icon not in icons:
            icons[icon] = len(icons)
        docs.append([
            r["id"], kind, r["sub_kind"], slot_ids,
            r["name_en"], r["name_ja"], r["group_en"], r["group_ja"],
            lines, trim_meta(kind, meta, slot_label),
            r["haystack"], r["sort_key"],
            icons[icon] if icon else -1,
        ])

    gem_tags = {r["id"]: (r["name_en"], r["name_ja"])
                for r in conn.execute("SELECT * FROM gem_tags")}
    ascendancies = [
        {"id": r["id"], "en": r["name_en"], "ja": r["name_ja"],
         "cls_en": r["class_en"], "cls_ja": r["class_ja"]}
        for r in conn.execute("SELECT * FROM ascendancies ORDER BY class_en, id")]

    payload = {
        "version": meta_rows.get("patch_version", ""),
        "built_at": meta_rows.get("built_at", ""),
        "slots": [{"id": s, "en": slot_label(s)[0], "ja": slot_label(s)[1]}
                  for s in S.SLOT_ORDER if used_slots.get(s)],
        "gem_tags": {t: {"en": gem_tags.get(t, (t, ""))[0],
                         "ja": gem_tags.get(t, ("", ""))[1],
                         "n": {k: c[t] for k, c in tag_counts.items() if c[t]}}
                     for t in sorted({t for c in tag_counts.values() for t in c})},
        "ascendancies": ascendancies,
        # UI の「手直し」を焼き込む。無ければ空（§ overrides.json）
        "overrides": load_overrides(),
        # docs[12] がこの配列の添字。UI が images/<path の / を _ に>.png を組み立てる
        "icons": [p for p, _ in sorted(icons.items(), key=lambda kv: kv[1])],
        "docs": docs,
    }
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    OUT.write_text(text, encoding="utf-8")
    print(f"{OUT} -> {len(text.encode()) / 1e6:.2f} MB, {len(docs)} docs", flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
