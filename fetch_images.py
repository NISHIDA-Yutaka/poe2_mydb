"""poe2db: アイコン画像の取得.

    python fetch_images.py          # 未取得のものだけ
    python fetch_images.py --force  # 取り直す

`image.ggpk.exposed` が `.dds` を PNG に変換して返す。poe2db.sqlite に記録されている
アイコンパスの分だけ落として `images/` に置く（約 2,000 枚 / 30MB 程度）。
HTML はここを相対パスで参照し、無ければ同じ URL にフォールバックする。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

import requests

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "poe2db.sqlite"
IMAGES = ROOT / "images"
BASE = "https://image.ggpk.exposed/poe2/Art/{path}?format=png"
UA = {"User-Agent": "poe2db-local/1.0 (personal build planning tool)"}

session = requests.Session()
session.headers.update(UA)


def local_name(art_path: str) -> str:
    """`2DItems/Amulets/Uniques/Astramentis.dds` → `2DItems_Amulets_Uniques_Astramentis.png`.

    UI 側（template.html の `iconUrl`）と同じ規則。変えるときは両方直す。
    """
    stem = art_path[:-4] if art_path.lower().endswith(".dds") else art_path
    return stem.replace("/", "_") + ".png"


def icon_paths(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT DISTINCT icon FROM search_docs WHERE icon != ''")
    return sorted({r[0] for r in rows})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    if not DB_PATH.exists():
        raise SystemExit("poe2db.sqlite が無い。先に python build_db.py")
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    paths = icon_paths(conn)
    IMAGES.mkdir(exist_ok=True)
    print(f"{len(paths)} icons", flush=True)

    failed: list[str] = []

    def one(art_path: str) -> int:
        out = IMAGES / local_name(art_path)
        if out.exists() and out.stat().st_size > 0 and not args.force:
            return 0
        try:
            # 空白入りのパスがある（例 `.../Storm Weaver.dds`）ので必ずエンコードする
            r = session.get(BASE.format(path=quote(art_path)), timeout=60)
            if r.status_code != 200 or not r.content:
                failed.append(art_path)
                return 0
            out.write_bytes(r.content)
            return 1
        except Exception:  # noqa: BLE001 - 1 枚落ちても止めない
            failed.append(art_path)
            return 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        done = sum(pool.map(one, paths))

    total_mb = sum(p.stat().st_size for p in IMAGES.glob("*.png")) / 1e6
    print(f"downloaded {done}, skipped {len(paths) - done - len(failed)}, "
          f"failed {len(failed)} -> {IMAGES} ({total_mb:.1f} MB)", flush=True)
    if failed:
        print("  failed sample:", failed[:5], flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
