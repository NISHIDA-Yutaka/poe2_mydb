# poe2db（ローカル版）

Path of Exile 2 のビルド検討用に、ゲームデータを日英で横断検索するローカルツール。
poe2db.tw をブラウザで何百回も引く代わりに、手元の単一 HTML で即座に絞り込む。

- 仕様: [docs/SPEC.md](docs/SPEC.md)
- データの出どころと翻訳の仕組み: [docs/DATA_PIPELINE.md](docs/DATA_PIPELINE.md)
- 対象パッチ: `4.5.5.2`

## 使う

`poe2db.html` をブラウザで開くだけ（サーバ不要・ネット不要・依存なし、8.6MB）。

```bash
start poe2db.html
```

アイコンは同じフォルダの `images/`（約 2,000 枚 / 60MB）を参照する。フォルダごと移動すれば
オフラインのまま表示され、無い場合は `image.ggpk.exposed` に自動でフォールバックする。

### 検索できるもの（8 種・12,552 件）

| kind | 中身 | 件数 |
|---|---|---|
| ユニーク | 装備部位 / ジュエル / チャーム / フラスコ / タリスマンを区別 | 449 |
| ノータブル | 通常ツリーのノータブル | 984 |
| キーストーン | 通常ツリーのキーストーン（ノータブルとは別枠） | 33 |
| アセンダンシー | 23 アセンダンシーのパッシブ | 425 |
| mod | アイテム mod。**どの装備に付くか**を持つ | 9,151 |
| ソケット | ルーン / ソウルコア。**装着先ごとの効果** | 313 |
| ジェム | スキル / サポート / リネージュ / スピリット | 1,120 |
| タイムレス | Heroic Tragedy / Undying Hate の変化パッシブ | 77 |

### 操作

- 入力するたびに絞り込む（Enter 不要）。`/` で検索欄、`Esc` でクリア、`↑↓` 移動、`Enter` で詳細、`1`〜`8` で kind 切り替え
- **結果は省略しない**。効果は全行表示し、件数も切らない（多いときは下へスクロールすると描き足す）
- ユニークは**部位ごとの塊**で並ぶ（兜 → 鎧 → 手袋 → … → 武器）。部位名の小見出しと件数が入る
- **効果は箇条書き、説明文は地の文**で描き分ける。ユニークの implicit（付与スキル名など）は
  ゲームと同じく explicit の上に、区切り線つきで一覧表示する
- **kind / 部位**のチップで絞る。テキスト無しでも一覧できる（例: ソケット × 兜）
- **拳** トグル … 石の拳（Martial Artist の「石拳の道」）で mod がどう変化するかを併記
- **培** トグル … 培養のオーブで置換できる行に ◆、Vaal ユニークにバッジ
- **スキル**タブ … スキルをタグ（アタック / 近接 / ノヴァ / スラム …）の AND で絞る専用ページ
- 日本語 / EN / 両方 を切り替え。訳が無い行は `EN` 印を付けて英語で出す
- **暗 / 明のテーマ**を切り替えられる（選択は次回も保持）
- **⚙ で表示設定**: アイコンの大きさ・文字サイズ・表示幅・主要色 / 操作色を変えられる。
  変更は即反映で次回も保持され、「既定に戻す」で初期値に戻せる
- 検索欄とチップは**普段は畳まれていて、マウスを乗せるか入力を始めると開く**。
  畳んでいる間も今の絞り込みは要約で見える。📌 で開いたまま固定できる
- 状態は URL に入るのでブックマークできる

### クエリ構文

```
憤怒                     部分一致（日英どちらでも）
憤怒 チャージ            空白は AND
-移動                    除外
"maximum life"           フレーズ
kind:mod slot:helmet     kind / 部位
sub:prefix asc:Monk1     sub_kind / アセンダンシー
tag:melee,nova           ジェムタグ（AND）
hw:yes cult:yes          石の拳あり / 培養の対象
origin:vaal jewel:kalguur
```

## コマンドラインから引く

```bash
python search.py 憤怒
python search.py -k notable,ascendancy エナジーシールド
python search.py -k socketable -s helmet
python search.py -k gem --tag melee,nova
python search.py -k mod "chance to Ignite" --json
```

## 作り直す（パッチ更新時）

```bash
python fetch_data.py --force   # 外部から取得。5〜10 分
python build_db.py             # poe2db.sqlite を作る。約 8 秒
python fetch_images.py         # アイコン約 2,000 枚（60MB）。初回のみ数分
python export_web.py           # web_data.json
python build_web.py            # poe2db.html
python -m pytest tests -q      # 受け入れテスト 27 本
```

前提: Python 3.10+（`requests`）、Node 22+（`npx` が使えること）。

## 構成

| ファイル | 役割 |
|---|---|
| `fetch_data.py` | repoe-fork / trade2 API / ggpk.exposed の `.csd` 589 本 / PoB / GGPK 言語別テーブル 24 種を取得 |
| `fetch_images.py` | アイテム・パッシブ・ジェムのアイコンを `images/` に取得（1,991/1,993 枚成功） |
| `parsers.py` | `.csd` パーサと約 50 種の値ハンドラ、PoB 定義パーサ、正規化、クエリ構文 |
| `slots.py` | `item_class` → 装備部位の対応（コードで固定） |
| `build_db.py` | 結合・翻訳・検証をして `poe2db.sqlite` を作る |
| `search.py` | 検索ライブラリ兼 CLI |
| `export_web.py` / `build_web.py` / `template.html` | 単一 HTML の生成 |
| `tests/` | 仕様書 §9 の受け入れテスト |

`data/`・`datexport/`・`images/`・`poe2db.sqlite`・`web_data.json` は生成物。

## 翻訳について

数値と構造はゲームファイル（repoe-fork）、**日本語はゲームに同梱されている訳**（`.csd` と
GGPK の言語別テーブル）を使う。結合は必ず ID で行い、並び順には依存しない。

`.csd` の統合フォールバックは別スキル向けの言い回しを返すことがあるため、**引いたブロックの
英語を同じ値で再レンダリングし、表示する英文と一致したときだけ日本語を採用**する
（DATA_PIPELINE.md §6.4）。一致しなければ英語のまま `EN` 印を付けて出す。

現在のカバー率: ユニーク名 98.4% / mod 94.2% / パッシブ本文 93.7% / ジェム説明 94.7% /
ソケット 97.1% / ジェム効果詳細 79.4%。
