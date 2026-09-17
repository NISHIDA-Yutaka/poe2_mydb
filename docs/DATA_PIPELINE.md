# poe2db データ収集・統合 仕様書

対象: Path of Exile 2 のゲーム内情報（mod / スキルジェム / ベースアイテム / ユニーク）を
ローカルで日英横断検索するためのデータ基盤。
本書は **データをどこから・どうやって集め・どう結合し・どう訳しているか** を、
UI を作り直す際に前提として読めるようにまとめたもの。

- 対象パッチ: `4.5.4.11`（`https://repoe-fork.github.io/poe2/version.txt` の値）
- 最終ビルド: 2026-09-17
- 作業ディレクトリ: `C:\Users\PC_User\poe2db`

---

## 1. 全体像

```
[外部ソース]                       [取得]              [統合]              [出力]

repoe-fork (ゲームデータ JSON) ─┐
公式 trade2 API (EN/JA) ────────┤
ggpk.exposed (.csd 560本) ──────┼─ fetch_data.py ─→ data/ ──┐
Path of Building (ユニーク) ────┤                            ├─ build_db.py ─→ poe2db.sqlite
GGG パッチ鯖 (言語別テーブル) ──┴─ pathofexile-dat ─→ datexport/ ─┘        │
                                                                          ├─ export_web.py ─→ web_data.json
                                                                          └─ build_web.py  ─→ poe2db_search.html / artifact.html
```

基本方針:

1. **数値・構造はゲームファイル由来**（repoe-fork）を正とする
2. **日本語はゲームファイルに同梱されている**ものを最優先で使う（`.csd` と言語別テーブル）
3. **結合は ID で行い、並び順や位置には依存しない**（過去にこれで誤訳が出た）
4. ID で引けないものだけ、英文の正規化照合で補う。照合結果は英文で答え合わせして採用する

---

## 2. データソース一覧

| # | ソース | 取れるもの | 言語 | 取得方法 | 備考 |
|---|---|---|---|---|---|
| S1 | `https://repoe-fork.github.io/poe2/*.min.json` | mod / ベース / ユニーク名 / ジェム / スキルの数値と構造 | 英 | HTTP GET | ゲームファイルの自動エクスポート。ほぼ毎日更新 |
| S2 | `https://{www,jp}.pathofexile.com/api/trade2/data/{stats,items}` | トレード検索用の mod 文 / アイテム名 | 英・日 | HTTP GET（要 User-Agent） | 数値レンジは無い。`stats` は `id` で言語間結合できる。**`items` は結合不能**（§6.2） |
| S3 | `https://ggpk.exposed/files?q=download&adapter=poe2&path=poe2://data/statdescriptions/…` | stat 説明文テンプレート（`.csd`） | **全言語同梱** | HTTP GET | パスは小文字。SPA が何でも 200 を返すので `q=index` で一覧を取る |
| S4 | `https://repoe-fork.github.io/pob-data/poe2/Uniques/*.json` | ユニークの implicit / 性能 | 英 | HTTP GET | Path of Building チームの手動管理。**ゲームファイルにはユニーク性能が無い** |
| S5 | GGG パッチサーバ（`pathofexile-dat` 経由） | 言語別の `.datc64` テーブル | 英・日 | `npx pathofexile-dat` | ゲーム未インストールで可。アイテム名・スキル説明文の正規ソース |

### 2.1 S1: repoe-fork で使うファイル

| ファイル | 件数 | 用途 |
|---|---|---|
| `mods.min.json` | 16,679 | mod 本体。`stats[].id/min/max`、`generation_type`、`domain`、`required_level`、`spawn_weights` |
| `base_items.min.json` | 5,476 | ベースアイテム。キーは Metadata パス（例 `Metadata/Items/Amulets/Amulet5`） |
| `uniques.min.json` | 449 | ユニーク名とクラス。**性能は入っていない** |
| `skill_gems.min.json` | 1,191 | ジェム。`grants_skills[0]` で skills.json と結合。`support_text` にサポートジェムの説明 |
| `skills.min.json` | 8,347 | スキル本体。`active_skill.description`、`stat_sets[]`（§5.3） |
| `item_classes.min.json`, `tags.min.json` | — | 補助 |

英文には `[Resistances|Cold Resistance]` のような内部リンク記法が残る。DB には原文のまま保持し、表示時に `strip_markup()` で落とす。

### 2.2 S3: `.csd` の取り方

トップレベルの 27 本に加え、`skills.json` の各 `stat_sets[].translation_file` が参照するファイルを全て取る。参照値は `.txt` 表記だが実ファイルは `.csd`。

```
data/statdescriptions/
├── stat_descriptions.csd                    ← item mod の主要ファイル（19.5 MB）
├── skill_stat_descriptions.csd              ← 6,267 stat_set が参照
├── gem_stat_descriptions.csd
├── passive_skill_stat_descriptions.csd
└── specific_skill_stat_descriptions/
    ├── fireball.csd                         ← スキル固有（376 本）
    └── ball_lightning/statset_0.csd         ← サブディレクトリ持ち（76 個）
```

合計 560 本 / 31.4 MB。`fetch_stat_description_files()` が 8 並列で落とす。

### 2.3 S5: pathofexile-dat で取るテーブル

`datexport/config.json`:

```json
{
  "patch": "4.5.4.11",
  "translations": ["English", "Japanese"],
  "tables": [
    {"name": "BaseItemTypes",     "columns": ["Id", "Name"]},
    {"name": "Words",             "columns": ["Text", "Text2"]},
    {"name": "UniqueStashLayout", "columns": ["WordsKey", "ItemVisualIdentityKey"]},
    {"name": "ActiveSkills",      "columns": ["Id", "DisplayedName", "ShortDescription", "Description"]},
    {"name": "GemEffects",        "columns": ["Id", "Name", "SupportName", "SupportText"]}
  ]
}
```

出力は `datexport/tables/{English,Japanese}/<Table>.json`。**同じテーブルは言語間で行数・行順が完全に一致する**（`_index` で対応）。PoE2 の言語別テーブルはゲームファイル上 `Data/Balance/<Language>/` にあり、これを `pathofexile-dat` が解決する。

スキーマ（列名の確認用）: `https://github.com/poe-tool-dev/dat-schema/releases/download/latest/schema.min.json`（PoE2 は `validFor: 2`）

---

## 3. 取得手順（`fetch_data.py`）

```
python fetch_data.py           # 未取得のものだけ
python fetch_data.py --force   # 全部取り直し（パッチ更新時）
```

| 段 | 内容 | 出力先 |
|---|---|---|
| 1/6 | S1 を取得。`.min.json` → `.json` にリネーム | `data/*.json` |
| 2/6 | S2 を EN/JA 両方取得 | `data/trade_{stats,items}_{en,ja}.json` |
| 3/6 | トップレベル `.csd` 3 本 | `data/*.csd` |
| 4/6 | S4 を 27 カテゴリ + `Special/Generated` + `Special/New` を 1 つにマージ | `data/pob_uniques.json` |
| 5/6 | `skills.json` を読み、参照されている `.csd` を全て取得 | `data/statdescriptions/**` |
| 6/6 | `pathofexile-dat` を実行 | `datexport/tables/**` |

前提: Python 3.10+、Node 22+（`npx` が使えること）、ネット接続。

---

## 4. 結合キー一覧（最重要）

| 結合したいもの | 左 | 右 | キー | 信頼度 |
|---|---|---|---|---|
| ベースアイテムの日本語名 | `base_items.json` のキー | `BaseItemTypes.Id` | **Metadata パス完全一致** | 確実 |
| ユニークの日本語名 | `uniques.json[].name` | `Words.Text` | `UniqueStashLayout.WordsKey` → `Words[k]`。`Text`=英 / `Text2`=訳 | 確実 |
| ユニークの性能 | `uniques.json[].name` | PoB ブロック 1 行目 | 英語名完全一致 | 確実（PoB 側に無ければ空） |
| ジェムの日本語名 | `skill_gems.json` のキー | `BaseItemTypes.Id` | Metadata パス完全一致 | 確実 |
| ジェム → スキル | `skill_gems[].grants_skills[0]` | `skills.json` のキー | 文字列一致 | 確実 |
| アクティブスキルの説明文 | `skills[].active_skill.id`（例 `fireball`） | `ActiveSkills.Id` | 文字列一致 | 確実 |
| サポートジェムの説明文 | `skill_gems[].support_text` | `GemEffects.SupportText` | **英文の完全一致** | 高い（英文が同一ソース） |
| mod の日本語 | `mods[].stats[].id` の組 | `.csd` ブロックの stat ID 組 | タプル完全一致 | 確実 |
| ジェム効果の日本語 | `stat_text` のキー（`"id\nid"`） | `.csd` ブロック | タプル完全一致 + `translation_file` でファイル選択 | 確実（§6.4 の検証つき） |
| trade2 の英↔日 | `stats[].id` | 同 | `id` 完全一致 | 確実 |
| PoB 性能行の日本語 | 完成した英文 | `.csd` 英語テンプレート | 正規化文字列一致 → 数値を正規表現で抜き戻す | 高い |

**やってはいけない結合**: `trade2/data/items` の EN/JA を位置で対応付けること（§6.2）。

---

## 5. 入力フォーマット仕様

### 5.1 `.csd`（stat 説明文）

プレーンな UTF-8 テキスト。`description` ブロックの繰り返し。

```
description
	1 active_skill_base_area_of_effect_radius          ← <ID 個数> <ID …>
	2                                                   ← バリアント数
		10 "Explosion radius is {0} metre" divide_by_ten_1dp_if_required 1
		# "Explosion radius is {0} metres" divide_by_ten_1dp_if_required 1
	lang "Japanese"                                     ← 以降、言語ブロック
	2
		10 "爆発の半径は{0}m" divide_by_ten_1dp_if_required 1
		# "爆発の半径は{0}m" divide_by_ten_1dp_if_required 1
	lang "Simplified Chinese"
	…
```

各バリアント行 = `<条件…> "<テンプレート>" [<ハンドラ> <添字…> …]`

- **条件**: stat 1 つにつき 1 トークン。`#`=任意 / `10`=一致 / `1|#`=下限 / `#|-1`=上限。**加工前の生の値で判定**する
- **テンプレート**: `{0}` `{1}` … が値。`{0:+d}` は符号付き表示
- **ハンドラ**: 値の単位変換。添字は 1 始まり。例 `divide_by_ten_1dp_if_required 1`、`milliseconds_to_seconds 1`、`negate 2`。**適用しないと `1.7m` が `17m` になる**（`parsers.HANDLERS` に約 40 種実装済み）
- テンプレート内の `\n` `\"` はエスケープされている。パーサで戻す
- 言語ブロックはバリアント数が英語と一致する前提で位置対応

パーサ: `parsers.parse_csd(path)` → `{(id,…): {言語名: [(条件, テンプレート, ハンドラ), …]}}`

日本語カバー率: `stat_descriptions.csd` で 10,733 ブロック中 10,721（99.9%）。**訳が無いのではなく、読むファイルが足りないケースが大半**だった。

### 5.2 Path of Building ユニーク定義

1 アイテム = 1 テキストブロック。

```
Astramentis                                  ← 1 行目: 名前
Stellar Amulet                               ← 2 行目: ベース
Variant: Pre 0.2.0                           ← 版の宣言（複数可）
Variant: Current
Implicits: 1                                 ← 直後の N 行が implicit
{tags:attribute}+(5-7) to all Attributes
{variant:1}{tags:attribute}+(80-100) to all Attributes   ← 版 1 のみ
{variant:2}{tags:attribute}+(50-100) to all Attributes   ← 版 2 のみ
{tags:physical,attack}-4 Physical Damage taken from Attack Hits
```

- `{variant:N[,M]}` … **最後に宣言された `Variant:` が現行**。それ以外は旧版なので捨てる
- `{tags:…}` などの先頭 `{…}` は全て剥がす（複数連結あり）
- `LevelReq:` は PoE2 データでは常に無い
- パーサ: `parsers.parse_pob_uniques(blob)` → `[{name, base_item, category, implicits[], stats[]}]`

### 5.3 `skills.json` の `stat_sets`

```json
"stat_sets": [{
  "id": "FireballPlayer",
  "translation_file": "specific_skill_stat_descriptions/fireball.csd",
  "static": {
    "stats": [{"id": "spell_minimum_base_fire_damage", "type": "float"},
              {"id": "active_skill_base_area_of_effect_radius", "type": "constant", "value": 17}, …],
    "stat_text": {"active_skill_base_area_of_effect_radius": "Explosion radius is 1.7 metres"},
    "quality_stats": [{"stat": "{id}% chance to …", "stats": {"id": 500}}]
  },
  "per_level": {
    "1":  {"stat_text": {"spell_minimum_base_fire_damage\nspell_maximum_base_fire_damage": "Deals 8 to 12 [Fire] Damage"},
           "stats": [{"value": 8}, {"value": 12}, null, …]},
    "20": {…}
  }
}]
```

- `static.stats` の並びが **`per_level[L].stats` の添字**に対応する
- `stat_text` のキーは `\n` 区切りの stat ID → そのまま `.csd` のブロック ID 組
- `static.stat_text` はレベル非依存（サポートジェムはほぼここ）、`per_level` はレベル依存
- レベルは 1〜40 まであるが表示は `SHOW_LEVELS = (1, 20)`
- `quality_stats` は値が生（500 = 5%）でハンドラ未適用。**現在は表示していない**
- 英文はエクスポート時点でハンドラ適用済み。日本語側は自前で適用する

---

## 6. 翻訳の仕組み（3 系統）

### 6.1 系統 A: stat ID → `.csd`（mod、ジェム効果）

1. stat ID の組で `.csd` ブロックを引く
2. 条件に合うバリアントを選ぶ（`parsers.pick_variant`。生の値で判定）
3. ハンドラを適用して値を整形し、`{n}` に差し込む（`parsers.fill`）

ファイルの優先順（`build_db.CsdSet.lookup`）:

1. `translation_file` で指定されたファイル
2. 汎用 3 本（`stat_descriptions` / `gem_stat_descriptions` / `passive_skill_stat_descriptions`）
3. 落とした全 `.csd` の統合テーブル（最終フォールバック）

mod は `mods[].stats` に ID があるので直接。まず全 ID の組で引き、無ければ ID ごとに引いて改行で連結。

### 6.2 系統 B: trade2 英文照合（mod の穴埋め、旧・ユニーク）

`trade_stats_en.json` と `_ja.json` を `id` で結合し、**英文を正規化した文字列 → 日本語** の辞書を作る。

正規化（`build_db.normalize`）:

1. `[Key|表示]` → `表示`、`[表示]` → `表示`
2. 数値・レンジ（`(41-45)`、`+30`、`1.7`）→ `#`
3. `+#` → `#`（trade2 は加算系の先頭 `+` を落とすことがある）
4. 空白圧縮、小文字化

複数行 mod は行ごとに引き、**全行そろった時だけ**採用。値は `#` のままになる。

**`trade2/data/items` は名前の対訳に使えない。** 理由:
- 項目に ID が無い
- 日本語版では未翻訳項目が丸ごと落ちる（Armour: EN 1552 / JA 1545）
- ユニークは言語ごとに名前順ソート（EN は "Ab Aeterno" 順、JA は「アルファの遠吠え」順）
→ 位置合わせすると `Couture of Crimson` に「ペールキングの王冠」が付く。**名前は S5 の言語別テーブルを使う**。

### 6.3 系統 C: 完成英文 → `.csd` テンプレート索引（ユニーク性能）

PoB の行は stat ID を持たないので:

1. 全 `.csd` ブロックの英語テンプレートを `normalize_template()`（`{n}` → `#` して A と同じ正規化）でキー化し、`{正規化英文: (英テンプレ, 日テンプレ)}` を作る（20,107 件）
2. PoB の行を `normalize()` して引く
3. 英テンプレートから正規表現を組み（`{n}` を値捕捉 `VALUE` に置換、リテラルはエスケープ）、元の英文から値を抜く
4. 日テンプレートの `{n}` に、抜いた文字列をそのまま差す（`(60-100)` など。ハンドラ不要）

`build_db.translate_rendered(line, index)`。引けなければ系統 B にフォールバック。

### 6.4 誤訳を防ぐ検証

系統 A の最終フォールバック（全ファイル統合）は、**同じ stat ID でも別スキル向けの言い回し**を返すことがある（例: `Fires 12 Projectiles` に「シャード投射物を毎秒12個放つ」）。

対策: 引いたブロックの**英語テンプレートを同じ値でレンダリングし、表示する英文と `normalize()` 後に一致するときだけ**日本語を採用する。一致しなければ訳なし扱い（英語表示）。これで効果詳細の訳率は 94.1% → 88.4% に下がったが、下がった分は誤訳だった。

### 6.5 名前の翻訳（S5）

| 対象 | 方法 |
|---|---|
| ベースアイテム・ジェム | `BaseItemTypes` を `Id` で引き、JA 側の `Name` |
| ユニーク | `UniqueStashLayout[i].WordsKey` → `Words[k]`。`Text`=英名、`Text2`=訳。英名で辞書化 |
| アクティブスキル説明 | `ActiveSkills.Id` == `active_skill.id`。`ShortDescription`=概要、`Description`=詳細 |
| サポートジェム説明 | `GemEffects.SupportText`（英）== `skill_gems[].support_text` → JA 側の `SupportText` |

---

## 7. SQLite スキーマ（`poe2db.sqlite`）

### `base_items`（5,476）

| 列 | 意味 |
|---|---|
| `id` | Metadata パス（PK） |
| `name_en` / `name_ja` | 名前。JA は S5 由来（92% 埋まる。残りはゲーム側でも未翻訳） |
| `item_class` | `Amulet`, `Body Armour`, `Active Skill Gem` … |
| `drop_level`, `width`, `height` | |
| `tags` | カンマ区切り |
| `release_state` | `released` / `unreleased` など。Web は `released` のみ |

### `mods`（16,679）

| 列 | 意味 |
|---|---|
| `id` | mod の内部 ID（PK） |
| `name` | 接辞名（`of Haast` 等）。ユニーク固有 mod は空 |
| `text_en` | repoe の英文（マークアップ付き） |
| `text_ja` | 訳。`ja_source` が `csd` なら値レンジ入り、`trade` なら `#` |
| `ja_source` | `csd` / `trade` / NULL |
| `generation_type` | `prefix` / `suffix` / `unique` / `corrupted` … |
| `domain` | `item` / `monster` / `area` … **Web は `item` のみ** |
| `required_level` | |
| `mod_group`, `tags`, `stat_ids` | カンマ区切り |
| `stat_min`, `stat_max` | 全 stat の min の最小 / max の最大 |
| `spawn_tags` | weight > 0 のタグ |
| `matched` | 訳あり=1 |

### `uniques`（449）

| 列 | 意味 |
|---|---|
| `id` | uniques.json のキー（= 名前） |
| `name_en` / `name_ja` | JA は Words 経由（98%） |
| `item_class`, `base_item`, `base_item_ja` | ベースは PoB 由来 |
| `level_req` | 常に NULL（PoB PoE2 データに無い） |
| `implicits`, `stats` | 改行区切りの英文（検索・後方互換用） |
| `stats_ja` | 改行区切りの訳（同上） |
| `has_stats` | PoB に定義あり=1（97%） |
| `stats_json` | **正** `{"implicits":[{en,ja}…], "stats":[{en,ja}…]}` |

### `skill_gems`（1,191）

| 列 | 意味 |
|---|---|
| `id` | Metadata パス（PK） |
| `name_en` / `name_ja` | |
| `gem_type` | `active` / `support` / `spirit` |
| `color`, `tags` | |
| `req_str` / `req_dex` / `req_int` | **属性要求の配分率（%）。必要値ではない**。合計 100 |
| `skill_id` | `active_skill.id`（サポートは NULL） |
| `summary_en` / `summary_ja` | 概要（`ShortDescription`）。サポートは NULL |
| `desc_en` / `desc_ja` | 説明（`Description` または `support_text`） |
| `skill_types` | カンマ区切り |
| `cast_time` | ms |
| `detail_en` / `detail_ja` | 改行区切り（検索用） |
| `detail_json` | **正** `[{en,ja} | {levels:[{lv,en,ja}…]}]` |

### `trade_stats`（8,258）、`item_name_map`

trade2 の対訳表と、名前対訳の一覧（`category_en` が `base` / `unique`）。参照用。

---

## 8. Web 用 JSON（`web_data.json`）

`export_web.py` が SQLite から抽出し、マークアップを剥がして書き出す。5.7 MB。

```jsonc
{
  "bases":   [{name_en, name_ja, item_class, drop_level, width, height, tags}],
  "mods":    [{name, text_en, text_ja, ja_source, generation_type, required_level, tags, spawn_tags}],
  "uniques": [{name_en, name_ja, item_class, base_item, base_item_ja,
               implicits: [{en, ja}], stats: [{en, ja}], haystack}],
  "gems":    [{name_en, name_ja, gem_type, color, tags, req_str, req_dex, req_int,
               summary_en, summary_ja, desc_en, desc_ja, skill_types,
               detail: [{en, ja} | {levels: [{lv, en, ja}]}], haystack}]
}
```

- `bases` はジェム系クラス（`Active Skill Gem`, `Support Skill Gem`, `Meta Skill Gem`, `SkillGemToken`）を除外（3,849 件）
- `mods` は `domain='item'` かつ英文ありのみ（7,778 件）
- `haystack` は検索用に全文（EN/JA/詳細）を小文字連結したもの。ジェムとユニークのみ事前計算。mod / ベースは UI 側で `searchKeys` から組み立てている

`build_web.py` が `template.html` の `__DATA__` に埋め込み（`<` を `\u003c` にエスケープ）、`__VERSION__` にパッチ番号を入れて 2 本出力する:

- `artifact.html` … `<html>/<head>` なしの本文のみ（Artifact 公開用）
- `poe2db_search.html` … doctype + `<meta charset>` 付きの単体ファイル（ローカル用）

---

## 9. 現在のカバー率

| データ | 件数 | 日本語 |
|---|---|---|
| ベースアイテム名 | 5,476 | 92%（Web 掲載分は 100%） |
| item mod | 8,150 | 94.9%（Web 掲載分は 99%） |
| ユニーク名 | 449 | 98% |
| ユニーク性能行 | 2,283 | 94.3%（実数値入り） |
| ジェム説明文 | 1,191 | 94% |
| ジェム効果詳細行 | 3,379 | 88.4%（検証で弾いた分を含む） |

---

## 10. 既知の落とし穴（今回踏んだもの）

| 症状 | 原因 | 対処 |
|---|---|---|
| ユニーク名が別物になる | `trade2/data/items` を位置で結合 | S5 の Words 経由に変更（§6.2） |
| `爆発の半径は17m`（正: 1.7m） | `.csd` ハンドラ未適用 | `parsers.HANDLERS` 実装（§5.1） |
| `Fires 12 Projectiles` に別スキルの訳 | 統合フォールバックが別ブロックを返す | 英文レンダリング一致で検証（§6.4） |
| 効果詳細の訳が付かない | `translation_file` を無視し 3 本しか読んでいなかった | 参照される 560 本を取得しファイル指定で引く |
| ユニークに旧版の数値が混ざる | PoB `{variant:N}` を無視 | 最後の `Variant:` のみ採用（§5.2） |
| 訳文中に `\n` が文字のまま | `.csd` のエスケープ未処理 | パーサで戻す |
| `+(5-8) to Strength` が照合できない | trade2 は先頭 `+` を落とす | 正規化で `+#`→`#` |
| 複数行 mod が照合できない | 1 mod に複数 stat | 行ごとに照合し全行一致で採用 |
| `INT 100` が要求値に見える | `requirement_weights` は配分率 | 列名を「属性配分」、表示に `%` |
| 品質ボーナスが `500%` | 生の値でハンドラ未適用 | 表示から除外（未解決） |
| サポートジェムに説明が無い | `ActiveSkills` には無い | `GemEffects.SupportText` を追加取得 |
| `ggpk.exposed` が何でも 200 | SPA フォールバック | `files?q=index` で JSON 一覧を使う。パスは小文字 |

開発環境の注意: Windows の Git Bash ヒアドキュメント経由で Python に `\1` `\n` を渡すと制御文字に化ける。ファイル編集は Edit ツールか、`chr(10)` 等で組み立てる。

---

## 11. 再生成手順

```powershell
cd C:\Users\PC_User\poe2db
python fetch_data.py --force   # パッチ更新時。数分（npx 初回はもう少し）
python build_db.py             # 30 秒程度。全 .csd を読むため
python export_web.py
python build_web.py
```

`build_db.py` は毎回 `poe2db.sqlite` を削除して作り直す。差分更新は無い。

---

## 12. 未着手・改善余地

- **品質ボーナス行**: `quality_stats` にハンドラを適用して表示する
- **ジェム効果詳細の残り 392 行**: 「検証で弾いた」と「どの csd にも無い」を切り分けるログを出す
- **ユニーク性能の残り 130 行**: 同上。多くは PoB 独自表記（`Has (1-3) Charm Slot` 等）と思われる
- **mod の残り 5%**: `advanced_mod_stat_descriptions.csd` など未読の csd を試す
- **`mods` の domain != item**（monster / area 等）は Web に出していない
- **ジェムのレベル別数値**: 1 と 20 のみ。全 40 レベルを持つなら JSON が数倍になる
- **パッシブツリー・アセンダンシー**: repoe に `passive_skill_trees/`、`ascendancies.min.json` があるが未使用
- **画像**: `visual_identity.dds_file` を `image.ggpk.exposed/poe2/<path>?format=png` で引ける。未使用

---

## 13. UI 作り直しに向けたメモ

現行 UI（`template.html`）はタブごとに独立した配列を検索している。横断検索にするなら:

- 全レコードを `{kind, key, title_ja, title_en, sub, haystack, payload}` の 1 配列に正規化し、`haystack` は **ビルド時に**作る（今はジェム・ユニークのみ事前計算、mod・ベースは UI 側）
- `kind` でフィルタ、`haystack` で全文、`payload` で詳細描画、が分離していると差し替えやすい
- 表示規則「訳があれば日本語のみ、無ければ `EN` 印付き英語」は `effectText()` / `stack()` に集約してある。同じ規則を全 kind に適用する
- 5.7 MB を毎回パースしているので、SQLite を WASM（`sql.js`）で直接引く、または kind ごとに遅延ロードする選択肢もある
- `req_*` は必ず「配分率」として扱う。必要値を出すならジェムレベル依存の別データが要る
