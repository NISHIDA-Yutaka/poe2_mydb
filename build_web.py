"""poe2db: template.html + web_data.json → 単一 HTML（docs/SPEC.md §8）.

    python build_web.py

出力: poe2db.html（doctype 付きの単体ファイル。file:// で開ける）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT / "template.html"
DATA = ROOT / "web_data.json"
OUT = ROOT / "poe2db.html"

HEAD = """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>poe2db {version}</title>
</head>
<body>
"""
FOOT = "\n</body>\n</html>\n"


def main() -> None:
    if not DATA.exists():
        raise SystemExit("web_data.json がありません。先に python export_web.py を実行してください。")
    payload = DATA.read_text(encoding="utf-8")
    version = json.loads(payload).get("version", "")
    body = TEMPLATE.read_text(encoding="utf-8")
    # `</script>` などで閉じられないように `<` をエスケープする
    safe = payload.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    body = body.replace("__DATA__", safe).replace("__VERSION__", version)
    OUT.write_text(HEAD.format(version=version) + body + FOOT, encoding="utf-8")
    size = OUT.stat().st_size / 1e6
    print(f"{OUT} -> {size:.2f} MB (patch {version})", flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
