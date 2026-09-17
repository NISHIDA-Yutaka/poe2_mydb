"""poe2db: アイコン画像の取得（任意）.

    python fetch_images.py          # 未取得のものだけ
    python fetch_images.py --force  # 取り直す

**実行しなくても UI は動く**。images/ が無ければ同じ画像をリモートから読むので、
オフラインで使いたい / 表示を速くしたいときだけ実行すればよい。
アイコンの一覧は poe2db.sqlite があればそこから、無ければ poe2db.html から読むので、
clone 直後（ビルド前）でも実行できる。

`image.ggpk.exposed` が `.dds` を PNG に変換して返す。poe2db.sqlite に記録されている
アイコンパスの分だけ落として `images/` に置く（約 2,000 枚 / 30MB 程度）。
HTML はここを相対パスで参照し、無ければ同じ URL にフォールバックする。
"""
from __future__ import annotations

import argparse
import json
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


def icon_paths_from_db() -> list[str] | None:
    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT DISTINCT icon FROM search_docs WHERE icon != ''")
        return sorted({r[0] for r in rows})
    finally:
        conn.close()


def icon_paths_from_html() -> list[str] | None:
    """poe2db.html / web_data.json に埋まっている `icons` 配列を読む.

    clone しただけで poe2db.html しか無い環境でも、ビルドせずに画像を揃えられるようにする。
    """
    for path in (ROOT / "web_data.json", ROOT / "poe2db.html"):
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        key = '"icons":['
        start = text.find(key)
        if start < 0:
            continue
        end = text.find("]", start + len(key))
        if end < 0:
            continue
        try:
            paths = json.loads(text[start + len(key) - 1:end + 1])
        except json.JSONDecodeError:
            continue
        if paths:
            print(f"  ({path.name} からアイコン一覧を読みました)", flush=True)
            return sorted({p for p in paths if isinstance(p, str) and p})
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    # DB があればそこから、無ければ poe2db.html に埋まっている一覧から読む
    paths = icon_paths_from_db() or icon_paths_from_html()
    if not paths:
        raise SystemExit(
            "アイコンの一覧が見つかりません。\n"
            "poe2db.html か poe2db.sqlite のどちらかが必要です。\n"
            "clone 直後なら poe2db.html があるはずなので、このスクリプトだけで揃います。")
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
