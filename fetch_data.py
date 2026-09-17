"""poe2db: 外部ソースからの取得（docs/SPEC.md §3, docs/DATA_PIPELINE.md §3）.

    python fetch_data.py           # 未取得のものだけ
    python fetch_data.py --force   # 全部取り直し（パッチ更新時）
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DATEXPORT = ROOT / "datexport"

REPOE = "https://repoe-fork.github.io/poe2"
POB = "https://repoe-fork.github.io/pob-data/poe2/Uniques"
GGPK = "https://ggpk.exposed/files"
TRADE = "https://{host}.pathofexile.com/api/trade2/data/{kind}"
UA = {"User-Agent": "poe2db-local/1.0 (personal build planning tool)"}

# S1: repoe-fork。値は出力ファイル名
REPOE_FILES = [
    "base_items", "mods", "mods_by_base", "uniques", "item_classes", "tags",
    "skill_gems", "skills", "gem_tags", "active_skill_types", "keywords", "ascendancies",
]
PASSIVE_TREES = ["Default"]

# S4: Path of Building のユニーク定義
POB_CATEGORIES = [
    "amulet", "axe", "belt", "body", "boots", "bow", "claw", "crossbow", "dagger",
    "fishing", "flail", "flask", "focus", "gloves", "helmet", "incursionlimb", "jewel",
    "mace", "quiver", "ring", "sceptre", "shield", "soulcore", "spear", "staff", "sword",
    "talisman", "tincture", "traptool", "wand",
    "Special/Generated", "Special/New",
]

# S5: pathofexile-dat で取る言語別テーブル（SPEC §3.2）
DAT_TABLES = [
    ("BaseItemTypes", ["Id", "Name"]),
    ("ItemClasses", ["Id", "Name"]),
    ("Words", ["Text", "Text2"]),
    ("UniqueStashLayout", ["WordsKey", "ItemVisualIdentityKey"]),
    ("UniqueOrigins", ["Unique", "Origin"]),
    ("Origin", ["Id"]),
    ("ActiveSkills", ["Id", "DisplayedName", "ShortDescription", "Description"]),
    ("GemEffects", ["Id", "Name", "SupportName", "SupportText"]),
    ("GemTags", ["Id", "Name"]),
    ("PassiveSkills", ["Id", "Name", "FlavourText", "PassiveSkillGraphId",
                       "IsNotable", "IsKeystone", "Ascendancy"]),
    ("Ascendancy", ["Id", "Name", "FlavourText", "Character", "Disabled"]),
    ("Characters", ["Id", "Name"]),
    ("Stats", ["Id"]),
    ("Mods", ["Id"]),
    ("ClientStrings2", ["Id", "Text"]),
    ("SoulCores", ["BaseItemType", "RequiredLevel", "Limit", "Description", "Type",
                   "TierHigher", "IsSocketBound", "CanSocketInMartialArtistSlots",
                   "CanSocketInUniqueItems", "CanSocketInJewellery", "ExtraDescription",
                   "CanSocketInCorruptedSanctified"]),
    ("SoulCoreStats", ["SoulCore", "StatCategory", "Stats", "StatsValues",
                       "BondedStats", "BondedStatsValues"]),
    ("SoulCoreStatCategories", ["Id", "TargetItemClasses", "Display"]),
    ("SoulCoreTypes", ["Id", "EffectStat", "Name", "SocketedStat"]),
    ("SoulCoreLimits", ["Id", "Limit", "Text"]),
    ("Incursion2MutatedUniqueModsClient", ["Id", "Mods"]),
    ("AlternateTreeVersions", ["ConquerorType", "SmallAttributeReplaced",
                               "SmallNormalPassiveReplaced", "NotableReplacementSpawnWeight"]),
    ("AlternatePassiveSkills", ["Id", "AlternateTreeVersion", "Name", "PassiveType", "Stats",
                                "Stat1", "Stat2", "Stat3", "Stat4", "Stat5", "Stat6",
                                "SpawnWeight", "ConquerorIndex", "FlavourText", "DDSIcon"]),
    ("AlternatePassiveAdditions", ["Id", "AlternateTreeVersion", "SpawnWeight", "Stats",
                                   "Stat1", "Stat2", "Stat3", "PassiveType"]),
]

session = requests.Session()
session.headers.update(UA)


def log(msg: str) -> None:
    print(msg, flush=True)


def get(url: str, *, tries: int = 3) -> requests.Response:
    last = None
    for n in range(tries):
        try:
            r = session.get(url, timeout=120)
            if r.status_code == 200:
                return r
            last = RuntimeError(f"HTTP {r.status_code} for {url}")
        except Exception as exc:  # noqa: BLE001 - ネットワークは何でも来る
            last = exc
        time.sleep(1 + n)
    raise last  # type: ignore[misc]


def save(path: Path, content: bytes, *, force: bool) -> bool:
    """書き込んだら True、既にあってスキップしたら False."""
    if path.exists() and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return True


def fetch_version() -> str:
    return get(f"{REPOE}/version.txt").text.strip()


def fetch_repoe(force: bool) -> None:
    log("[1/6] repoe-fork")
    for name in REPOE_FILES:
        out = DATA / f"{name}.json"
        if out.exists() and not force:
            continue
        save(out, get(f"{REPOE}/{name}.min.json").content, force=True)
        log(f"      {name}.json")
    for tree in PASSIVE_TREES:
        out = DATA / "passive_skill_trees" / f"{tree}.json"
        if out.exists() and not force:
            continue
        save(out, get(f"{REPOE}/passive_skill_trees/{tree}.min.json").content, force=True)
        log(f"      passive_skill_trees/{tree}.json")


def fetch_trade(force: bool) -> None:
    log("[2/6] trade2 API (EN/JA)")
    for kind in ("stats", "items"):
        for host, lang in (("www", "en"), ("jp", "ja")):
            out = DATA / f"trade_{kind}_{lang}.json"
            if out.exists() and not force:
                continue
            save(out, get(TRADE.format(host=host, kind=kind)).content, force=True)
            log(f"      trade_{kind}_{lang}.json")


def ggpk_index(path: str) -> list[dict]:
    r = get(f"{GGPK}?q=index&adapter=poe2&path={path}")
    return r.json().get("files", [])


def walk_statdescriptions(path: str) -> list[str]:
    """statdescriptions 以下の .csd を再帰的に列挙する."""
    found: list[str] = []
    for entry in ggpk_index(path):
        if entry["type"] == "dir":
            found += walk_statdescriptions(entry["path"] + "/")
        elif entry.get("extension") == "csd":
            found.append(entry["path"])
    return found


def fetch_csd(force: bool) -> None:
    log("[3/6] stat descriptions (.csd)")
    base = "poe2://data/statdescriptions/"
    paths = walk_statdescriptions(base)
    log(f"      {len(paths)} files in index")

    def one(p: str) -> int:
        rel = p[len(base):]
        out = DATA / "statdescriptions" / rel
        if out.exists() and not force:
            return 0
        save(out, get(f"{GGPK}?q=download&adapter=poe2&path={p}").content, force=True)
        return 1

    with ThreadPoolExecutor(max_workers=8) as pool:
        done = sum(pool.map(one, paths))
    log(f"      downloaded {done}, skipped {len(paths) - done}")


def fetch_pob(force: bool) -> None:
    log("[4/6] Path of Building uniques")
    out = DATA / "pob_uniques.json"
    if out.exists() and not force:
        return
    blocks: list[str] = []
    for cat in POB_CATEGORIES:
        try:
            payload = get(f"{POB}/{cat}.json").json()
        except Exception as exc:  # noqa: BLE001
            log(f"      !! {cat}: {exc}")
            continue
        items = payload if isinstance(payload, list) else []
        blocks += [b for b in items if isinstance(b, str)]
    save(out, json.dumps(blocks, ensure_ascii=False).encode("utf-8"), force=True)
    log(f"      {len(blocks)} unique blocks")


def fetch_dat(version: str, force: bool) -> None:
    log("[5/6] pathofexile-dat (GGPK language tables)")
    marker = DATEXPORT / "tables" / "Japanese" / "BaseItemTypes.json"
    if marker.exists() and not force:
        log("      up to date")
        return
    DATEXPORT.mkdir(parents=True, exist_ok=True)
    cfg = {
        "patch": version,
        "translations": ["English", "Japanese"],
        "tables": [{"name": n, "columns": c} for n, c in DAT_TABLES],
    }
    (DATEXPORT / "config.json").write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    npx = "npx.cmd" if os.name == "nt" else "npx"
    proc = subprocess.run([npx, "--yes", "pathofexile-dat"], cwd=DATEXPORT,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout + "\n" + proc.stderr + "\n")
        raise SystemExit("pathofexile-dat failed")
    log(f"      {len(DAT_TABLES)} tables x 2 languages")


def fetch_schema(force: bool) -> None:
    log("[6/6] dat-schema (reference)")
    out = DATA / "dat_schema.json"
    if out.exists() and not force:
        return
    url = ("https://github.com/poe-tool-dev/dat-schema/releases/download/latest/schema.min.json")
    save(out, get(url).content, force=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="全て取り直す")
    ap.add_argument("--skip-dat", action="store_true", help="pathofexile-dat を飛ばす")
    args = ap.parse_args()

    DATA.mkdir(exist_ok=True)
    version = fetch_version()
    log(f"patch {version}")
    (DATA / "version.txt").write_text(version, encoding="utf-8")

    fetch_repoe(args.force)
    fetch_trade(args.force)
    fetch_csd(args.force)
    fetch_pob(args.force)
    if not args.skip_dat:
        fetch_dat(version, args.force)
    fetch_schema(args.force)
    log("done.")


if __name__ == "__main__":
    main()
