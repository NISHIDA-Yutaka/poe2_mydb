# poe2db 横断検索 実装仕様書

対象読者: 実装担当（Claude Opus）。本書だけで実装を始められることを目標にする。
前提資料: [DATA_PIPELINE.md](DATA_PIPELINE.md)（データ取得・結合・翻訳の知見）。
**旧プロトタイプのコードは手元に無い。** DATA_PIPELINE.md に書かれている `fetch_data.py` / `build_db.py` / `parsers.py` 等は
「こういう設計で動いていた」という記録であり、**全てゼロから再実装する**。

作成日: 2026-09-17　対象パッチ: `4.5.5.2`（本書の数値は全てこのパッチの実データで確認済み）

---

## 0. 決定事項（TL;DR）

| 項目 | 決定 |
|---|---|
| 目的 | poe2db.tw の代替。ビルド検討時に 1 日数百回引く検索を、ローカルで即時・自分好みの絞り込みで行う |
| データ種別（kind） | `unique` / `notable` / `ascendancy` / `mod` / `socketable` / `gem`（スキル・サポート・リネージュサポート・スピリット）/ `timeless`（タイムレスジュエルの変化パッシブ）の 7 種。**石の拳（Hand Wraps）変化**と**培養のオーブ（Vaal Cultivation Orb）**は `mod` の sub_kind と `unique` への付加情報として扱う（§6.7, §6.8） |
| 言語 | 日英両方を保持し、両方で検索できる。表示は「訳があれば日本語、無ければ `EN` 印つき英語」 |
| 真実の置き場 | `poe2db.sqlite`（正規化テーブル + 検索用 `search_docs`） |
| 検索 UI | 単一 HTML ファイル（`poe2db.html`）。データは HTML に埋め込み、`file://` で開けてブラウザ内メモリ検索。サーバ不要 |
| CLI | `search.py`（SQLite を直接引く。動作確認・スクリプト用途） |
| パイプライン | Python 3.11、外部依存は `requests` のみ。`npx pathofexile-dat` で GGPK テーブル取得（Node 22） |
| 更新 | パッチごとに全再生成（差分更新はしない） |

---

## 1. スコープ

### 1.1 収録するデータ（kind）

| kind | 内容 | 件数目安 | 主ソース |
|---|---|---|---|
| `unique` | ユニークアイテム。**装備部位（兜・鎧・…）/ ジュエル / チャーム / フラスコ / タリスマン が区別できる** | 449 | repoe `uniques.json` + PoB + 言語テーブル |
| `notable` | パッシブツリーのノータブル（通常ツリー） | 984 | repoe `passive_skill_trees/Default.json` |
| `keystone` | 通常ツリーのキーストーン。**ノータブルとは別 kind**（チップで切り分けられる） | 33 | 同上（`is_keystone`） |
| `ascendancy` | アセンダンシーのパッシブ（ノータブル・小ノード含む） | 321 ノータブル / 669 ノード | 同上 + `ascendancies.json` |
| `mod` | アイテム mod（prefix / suffix / corrupted implicit / essence / desecrated）。**どの装備クラスに付き得るか**を持つ | ≈8,500 | repoe `mods.json` + `mods_by_base.json` |
| `socketable` | ルーン・ソウルコア等、ソケットに装着するもの。**装着先クラスごとの効果**を持つ | 313 | repoe `base_items.json`(`item_class=SoulCore`) + GGPK `SoulCores*` テーブル |
| `gem` | スキルジェム。`sub_kind` = `active`（スキル）/ `support`（サポート）/ `lineage`（**リネージュサポート**。`skill_gems[].tags ∋ lineage`）/ `spirit`（スピリットジェム = `gem_type=spirit`）。スキルは**タグ（attack / melee / nova …）で絞り込める専用ページ**を持つ（§8.5） | 1,191（active 505 / support 642 うち lineage 85 / spirit 44） | repoe `skill_gems.json` + `skills.json` + `gem_tags.json` + 言語テーブル |
| `timeless` | **タイムレスジュエルの変化パッシブ**（Heroic Tragedy = Kalguuran、Undying Hate = Abyss）。ノータブル・キーストーンの置換候補 | Kalguuran 40 / Abyss 36 | GGPK `AlternatePassiveSkills` + `AlternateTreeVersions` |
| `mod` の sub_kind `handwraps` | **石の拳**（Monk アセンダンシー Martial Artist のノード "Way of the Stonefist"）で手袋が Hand Wraps 化したときの**変化後 mod**。元 mod との対応を持つ。ユニーク手袋の各行にも変化後を付ける | 598（prefix 213 / suffix 198 / unique 187） | repoe `mods.json` の ID 接頭辞 `HandWraps` |
| `mod` の sub_kind `cultivation` | **培養のオーブ**（Vaal Cultivation Orb）でユニークの mod を置き換える**専用 mod プール**。ユニーク側には「この行は置換対象」の印を付ける | 204 + 置換対象元 mod 242 | repoe `mods.json` の ID 接頭辞 `UniqueMutatedVaal` + GGPK `Incursion2MutatedUniqueModsClient` |

### 1.2 非スコープ（今回作らない）

- ジェムのレベル別数値の全レベル表示（Lv1 と最大レベルのみ。全 40 レベルは JSON が数倍になる）
- ベースアイテム一覧（mod の「付く装備」表示に必要な分だけ内部テーブルとして持つ）
- パッシブツリーの描画・経路探索（ノードの `hash` と座標は保持するが UI では使わない）
- トレードサイト連携

### 1.3 満たすべきユースケース（受け入れ条件は §9）

1. `憤怒` で **全 kind 横断**検索 → ユニーク・ノータブル・アセンダンシー・mod・ソケット可能アイテムすべてから「憤怒」を含むものを一覧表示
2. `エナジーシールド` を **kind=notable, ascendancy に絞って**検索
3. 兜のソケットが空いている → **kind=socketable を装着先 `helmet` で絞る**（テキスト検索無しでも一覧できる）
4. 欲しい mod の文言は分かっている → **kind=mod をテキストで検索し、ヒットした mod から「付く装備クラス」を逆引き**（兜・ジュエル…）
5. Martial Artist で石の拳を取る前提 → **ユニーク手袋の性能が Hand Wraps 化でどう変わるか**を一覧で見る。また **手袋に付く通常 mod（prefix/suffix）が何に変化するか**を元 mod ↔ 変化後 mod の対で検索する
6. 培養のオーブでユニークを弄る前提 → **置換候補になる専用 mod プール**をテキストで検索し、**手持ちのユニークのどの行が置換対象か**を見る
7. `憤怒` の横断検索に**スキル・サポート・リネージュサポート**も含まれる（ジェムの説明文・効果詳細・サポート説明が対象）
8. **スキル専用ページ**: スキル（active）だけを、タグ（`attack` `melee` `nova` `slam` `projectile` `fire` …）の AND 絞り込みと属性色・必要レベルで一覧し、「近接のノヴァ系スキルを全部見る」ができる。サポート・リネージュも同じページでジェム種別を切り替えて絞れる
9. タイムレスジュエル（Heroic Tragedy / Undying Hate）を使う前提 → **半径内のノータブルが何に置き換わり得るか**をテキスト検索で一覧する

---

## 2. 全体アーキテクチャ

```
[取得]                                  [構築]                    [出力]
fetch_data.py ──→ data/               build_db.py ──→ poe2db.sqlite ──→ export_web.py ──→ poe2db.html
  ├ repoe-fork  *.json                   │                                └→ search_index.json（デバッグ用）
  ├ ggpk.exposed *.csd                   │
  ├ PoB uniques                          └ parsers.py（csd / PoB / 正規化 / ハンドラ）
  └ pathofexile-dat → datexport/tables/{English,Japanese}/*.json
                                       search.py（SQLite を直接検索する CLI）
```

ディレクトリ構成（新規作成）:

```
poe2db/
├── docs/            DATA_PIPELINE.md, SPEC.md（本書）
├── fetch_data.py
├── build_db.py
├── export_web.py
├── search.py
├── parsers.py       .csd パーサ / ハンドラ / PoB パーサ / 正規化
├── slots.py         item_class → slot の対応表（§4.2）。**データではなくコードで持つ**
├── template.html    UI テンプレート（`__DATA__` `__VERSION__` を置換）
├── tests/           pytest（§9）
├── data/            取得物（git 管理外）
├── datexport/       pathofexile-dat 出力（git 管理外）
├── poe2db.sqlite    生成物（git 管理外）
└── poe2db.html      生成物
```

---

## 3. データソース

DATA_PIPELINE.md §2 の S1〜S5 をそのまま使う。本節は**今回新たに使うもの**と、**実データで確認した構造**を書く。

### 3.1 repoe-fork（S1）で新たに使うファイル

| ファイル | 用途 | 確認した構造 |
|---|---|---|
| `passive_skill_trees/Default.min.json` | notable / ascendancy | `{title, roots[6], groups{}, passives{hash: node}, orbit_radii, skills_per_orbit, art}`。`passives` は 5,152 件 |
| `ascendancies.min.json` | アセンダンシー名・所属クラス | 37 件。キー例 `Warrior3`。値に `name`("Smith of Kitava" 等) / `character`(配列。`[1]` がクラス名 "Druid" 等) / `disabled` / `flavour_text` |
| `mods_by_base.min.json` | mod → 付く装備の逆引き | `{クラス表示名: {タグ組(カンマ区切り): {bases: [Metadata…], mods: {prefix|suffix|corrupted|unique: {mod_group: {mod_id: required_level}}}}}}` |
| `item_classes.min.json` | クラス分類 | `{item_class: {category, category_id, name}}` 118 件 |
| `stat_translations/*.json` | **英語のみ**（`English` と `trade_stats` キーしか無い） | 日本語は従来どおり `.csd` から取る。英語の答え合わせ用に使ってよい |
| `skill_gems.min.json` | gem | 1,191 件（キー = Metadata パス）。`base_item.{display_name,id,release_state}`, `color`(r/g/b/w), `gem_type`(active 505 / support 642 / spirit 44), `grants_skills[]`, `tags[]`, `requirement_weights`, `recommended_supports[]`, `icon_dds_file`。**`tags ∋ 'lineage'` がリネージュサポート（85 件、全て support）** |
| `skills.min.json` | gem のスキル本体 | 8,349 件（14 MB）。`active_skill.{id, display_name, description, types[], weapon_restrictions[]}`, `cast_time`, `is_support`, `stat_sets[]`（DATA_PIPELINE.md §5.3）, `support_gem`（サポートのとき）。`active_skill.types` は `Attack / Spell / Melee / Projectile / Area / Duration / Minion / Totemable / …`（290 種の内部型。**表示用タグは `skill_gems.tags` を使い、`types` は絞り込みの補助**） |
| `gem_tags.min.json` | タグの表示名 | 67 件 `{tag_id: "[Fire]"}`。日本語は GGPK `GemTags.Name`（`[Fire|火]` 形式。マークアップを剥がす）。実測でジェムに付くタグ: support 633, area 380, attack 354, duration 249, buff 194, persistent 193, melee 191, spell 186, projectile 165, physical 157, trigger 134, fire 127, lightning 109, cold 108, minion 93, strike 87, lineage 85, chaos 71, slam 47, meta 44, channelling 37, warcry 25, shapeshift 25, totem 23, **nova 21**, curse 20, chaining 18, aura 17, travel 16, companion 15, mark 13, grenade 12, herald 10, bear 8, wolf 7, wyvern 6, orb 5, banner 4 … |
| `mods.json` の ID 接頭辞 `HandWraps…` | 石の拳の変化後 mod | 598 件。`HandWraps` を剥がした ID が元 mod（588/598 で存在）。`spawn_weights` は空。§6.6 |
| `mods.json` の ID 接頭辞 `UniqueMutatedVaal…` | 培養のオーブの置換プール | 204 件、全て `generation_type=unique`、`implicit_tags` に `mutatedunique_vaal`。`spawn_weights` は空。§6.7 |

PoB ユニーク定義（S4）の実フォーマット: `https://repoe-fork.github.io/pob-data/poe2/Uniques/<category>.json` は **「1 アイテム = 1 文字列」の JSON 配列**（DATA_PIPELINE.md §5.2 のテキストブロックが配列要素になっている）。カテゴリは `amulet, axe, belt, body, boots, bow, claw, crossbow, dagger, fishing, flail, flask, focus, gloves, helmet, incursionlimb, jewel, mace, quiver, ring, sceptre, shield, soulcore, spear, staff, sword, talisman, tincture, traptool, wand` + `Special/`。`soulcore.json`（ユニークルーン）は §6.5 の socketable と名前で突き合わせて性能行を補う。

`passives` のノード構造（実物）:

```jsonc
"338": {
  "hash": 338, "id": "triggers19", "name": "Invocated Limit",
  "icon": "Art/2DArt/SkillIcons/passives/auraeffect.dds",
  "is_notable": true, "is_keystone": false, "is_jewel_socket": false,
  "is_ascendancy_starting_node": false, "is_multiple_choice": false, "is_multiple_choice_option": false,
  "is_icon_only": false, "is_free": false, "is_atlas_root": false,
  "stats": {"invocation_skill_maximum_energy_+%": 30},       // stat_id → 値（1 ノード複数可）
  "flavour_text": "", "reminder_text": [], "skill_points": 0, "weapon_set_points": 0,
  "ascendancy": "Warrior3"                                    // アセンダンシーのノードのみ存在
  // "granted_skill": … が 54 件、"buff_definitions" が 3 件にある
}
```

ノード分類（実測）: `is_notable` 1,305（うち `ascendancy` あり 321）、`is_keystone` 33、`ascendancy` あり 669、`is_jewel_socket` 19。

**注意**: `ascendancies.json` には PoE1 由来の `Marauder1..3` `Duelist` `Templar` `Shadow` などが残っており、`Default.json` にもそのノードが 17 件ずつ入っている。**`disabled == true` のものは除外**し、残ったものについてもノード名が空 / stats が空のものは除外する。除外したアセンダンシー ID をログに出すこと。`Witch3b` のような枝番付きは `character` と `name` で判断する。

### 3.2 GGPK 言語別テーブル（S5）で新たに取るテーブル

`datexport/config.json` の `tables` に追加する（`translations: ["English","Japanese"]` は従来どおり）:

| テーブル | 列 | 用途 |
|---|---|---|
| `PassiveSkills` | `Id, Name, FlavourText, PassiveSkillGraphId, IsNotable, IsKeystone, Ascendancy` | ノータブル / アセンダンシーの**日本語名**。`Id` は repoe ノードの `id`（例 `triggers19`）、`PassiveSkillGraphId` は `hash`。**実測: 対象 1,686 ノードが `Id` でも `hash` でも 100% 一致**。`Id` を主、`hash` を検証に使う |
| `Incursion2MutatedUniqueModsClient` | `Id, Mods` | 培養のオーブで**置換され得る元 mod** の一覧。1 行（`Id="OriginalMods"`）に `Mods` 配列 242 件（→ `Mods.Id` で mod ID に戻す）。§6.7 |
| `Mods` | `Id` | 上記の foreignrow を mod ID 文字列に戻すため |
| `UniqueOrigins` | `Unique, Origin` | **ユニークの起源（文化）**。`Unique` → `Words.Text`（英名）、`Origin` → `Origin.Id`。実測 126 行（Ezomyte 64 / Vaal 48 / Kalguuran 14）、repoe の 441 ユニーク中 114 件に起源あり。**`Origin == 'Vaal'` が「Vaal Unique」（培養のオーブで mod 置換できるユニーク）の定義**。§6.7 |
| `Origin` | `Id` | `Kalguuran` / `Ezomyte` / `Vaal` の 3 行 |
| `ActiveSkills` | `Id, DisplayedName, ShortDescription, Description` | スキルの日本語説明（DATA_PIPELINE.md §6.5 のとおり `Id == active_skill.id`） |
| `GemEffects` | `Id, Name, SupportName, SupportText` | サポートジェムの日本語説明（英文 `SupportText` == `skills[].support_gem` の説明文で結合） |
| `GemTags` | `Id, Name` | ジェムタグの日本語（`[Fire|火]` → `火`）。67 行 |
| `AlternateTreeVersions` | `ConquerorType` | タイムレスジュエルの種別。8 行（PoE1 レガシー含む）。**PoE2 で使うのは `Kalguuran`（index 6）と `Abyss`（index 7）のみ** |
| `AlternatePassiveSkills` | `Id, AlternateTreeVersion, Name, PassiveType, Stats, Stat1..Stat6, SpawnWeight, ConquerorIndex, FlavourText, DDSIcon` | **タイムレスジュエルの変化後パッシブ**。231 行中 Kalguuran 40 / Abyss 36（残りは PoE1 レガシー: Vaal 78 / Eternal 50 / Templar 19 …）。`Name` は言語別（例 `Scorched Earth` / `焦げた大地`）。`Stats` は行番号配列 → `Stats.Id`、`StatN` は `[min, max]` |
| `AlternatePassiveAdditions` | `Id, AlternateTreeVersion, Stats, Stat1..Stat3, SpawnWeight, PassiveType` | 小ノードへの**追加**効果。PoE2 分は Abyss 1 行のみ（Kalguuran 0）。初版は収録するが UI では `timeless` の `sub_kind=addition` |
| `Ascendancy` | `Id, Name, FlavourText, Character, Disabled` | アセンダンシーの日本語名。`Id` は repoe の `Warrior3` 等 |
| `Characters` | `Id, Name` | クラス名の日本語（Warrior→ウォリアー） |
| `ItemClasses` | `Id, Name` | **装備クラス名の日本語**。slot ラベルに使う。実測: `UtilityFlask` の Name は EN "Charms" / JA "チャーム"、`SoulCore` は "Augment" / "オーグメント"、`Warstaff` は "Quarterstaves"、`Focus` は "Foci" |
| `SoulCores` | `BaseItemType, RequiredLevel, Limit, Description, Type, TierHigher, IsSocketBound, CanSocketInMartialArtistSlots, CanSocketInUniqueItems, CanSocketInJewellery, ExtraDescription, CanSocketInCorruptedSanctified` | ソケット可能アイテム本体 |
| `SoulCoreStats` | `SoulCore, StatCategory, Stats, StatsValues, BondedStats, BondedStatsValues` | **装着先カテゴリごとの効果**（1 ルーン = 複数行） |
| `SoulCoreStatCategories` | `Id, TargetItemClasses, Display` | カテゴリ → 対象 `ItemClasses`（配列）。`Display` が「Martial Weapons」等の表示名（言語別） |
| `SoulCoreTypes` | `Id, Name` | ルーン / ソウルコア / … の種別名 |
| `SoulCoreLimits` | `Id, Limit, Text` | 装着数制限の説明文 |
| `Stats` | `Id` | `SoulCoreStats.Stats` 等の **foreignrow（行番号）を stat ID 文字列に戻す**ために必須 |
| `ClientStrings2` | `Id, Text` | `SoulCores.Description` の解決先 |
| `BaseItemTypes`, `Words`, `UniqueStashLayout` | 従来どおり | 名前の日本語化 |

`pathofexile-dat` の出力では **foreignrow は参照先テーブルの行番号（整数 or null）、配列列は整数配列**で出る。参照先テーブルも同時に export し、行番号で引く。`_index` で EN/JA を対応付ける（同一テーブルは言語間で行数・順序が一致する。DATA_PIPELINE.md §2.3）。

スキーマ確認用: `https://github.com/poe-tool-dev/dat-schema/releases/download/latest/schema.min.json` の `tables[]` で `validFor` が `2`（PoE2）または `3`（両方）のもの。上記列名はこのスキーマで確認済み。列が無い・名前が違う場合はスキーマを見て直し、本書を更新する。

### 3.3 `.csd`（S3）で追加で確実に読むもの

- `passive_skill_stat_descriptions.csd` … notable / ascendancy の stat 文
- `stat_descriptions.csd` … mod、ソケット可能アイテム、ユニーク
- `passive_skill_aura_stat_descriptions.csd`, `passive_skill_variant_stat_descriptions.csd` … パッシブで引けなかったときのフォールバック
- `advanced_mod_stat_descriptions.csd` … mod の残り 5% 用フォールバック

---

## 4. ドメインモデル

### 4.1 統一検索レコード `SearchDoc`

全 kind をこの 1 形に正規化する。UI と CLI はこれだけを見る。

```jsonc
{
  "id":       "unique:Astramentis",         // "<kind>:<内部キー>"。全体で一意
  "kind":     "unique",                      // unique | notable | keystone | ascendancy | mod | socketable | gem | timeless
  "sub_kind": "",                            // mod: "prefix"|"suffix"|"corrupted"|"essence"|"desecrated" / socketable: SoulCoreTypes.Name / ascendancy: "notable"|"small"|"start"
  "slots":    ["amulet"],                    // §4.2 の slot ID。unique=装備部位(1つ) / mod=付き得るクラス(複数) / socketable=装着先(複数) / notable,ascendancy=[]
  "name_en":  "Astramentis",
  "name_ja":  "アストラメンティス",            // 無ければ ""
  "group_en": "Amulet",                      // 見出し補助。unique=ベース名 / notable=""（将来: クラスタ名）/ ascendancy=アセンダンシー名 / mod=接辞名(of Haast) / socketable=種別
  "group_ja": "アミュレット",
  "lines":    [ {"en": "+(80-100) to all Attributes", "ja": "全ての属性 +(80-100)"} ],   // 本文行。表示順
  "meta":     { ... },                       // kind 固有の付加情報（§4.3）。表示・フィルタ用
  "haystack": "astramentis アストラメンティス amulet アミュレット +(80-100) to all attributes 全ての属性 +(80-100) …"
}
```

`haystack` の作り方（`parsers.normalize_for_search`）:

1. `name_en, name_ja, group_en, group_ja, lines[*].en, lines[*].ja, slot ラベル(EN/JA), sub_kind` を空白連結
2. `[Key|Text]` → `Text`、`[Text]` → `Text`（英文マークアップ除去）
3. Unicode NFKC 正規化（全角英数→半角、全角空白→半角）
4. 小文字化
5. 連続空白を 1 つに

検索側でクエリにも同じ正規化を掛けてから部分一致する。**大文字小文字・全角半角を区別しない**。

### 4.2 slot（装備部位）の定義 — `slots.py`

`item_class`（repoe / GGPK の英語 ID）から slot ID への対応表。**コードで固定**する（データから自動導出しない）。

| slot ID | 含む item_class | 表示 JA / EN |
|---|---|---|
| `helmet` | Helmet | 兜 / Helmet |
| `body_armour` | Body Armour | 鎧 / Body Armour |
| `gloves` | Gloves | 手袋 / Gloves |
| `boots` | Boots | 靴 / Boots |
| `shield` | Shield, Buckler | 盾 / Shield |
| `focus` | Focus | フォーカス / Focus |
| `quiver` | Quiver | 矢筒 / Quiver |
| `amulet` | Amulet | アミュレット / Amulet |
| `ring` | Ring | リング / Ring |
| `belt` | Belt | ベルト / Belt |
| `jewel` | Jewel, AbyssJewel | ジュエル / Jewel |
| `charm` | UtilityFlask（`tags` に `utility_flask`。名前が `… Charm`） | チャーム / Charm |
| `flask` | LifeFlask, ManaFlask | フラスコ / Flask |
| `talisman` | Talisman | タリスマン / Talisman |
| `one_hand_mace` … `crossbow` | 武器クラスは item_class ごとに 1 slot（One Hand Mace, Two Hand Mace, Sceptre, Wand, Staff, Warstaff, Spear, Bow, Crossbow, Claw, Dagger, Flail, One Hand Sword, Two Hand Sword, One Hand Axe, Two Hand Axe …） | JA は `ItemClasses.Name`（S5）から |
| `weapon` | （検索用の親 slot。武器 slot はすべて `weapon` も持つ） | 武器 / Weapon |
| `armour` | （親 slot。helmet/body_armour/gloves/boots/shield/focus） | 防具 / Armour |
| `jewellery` | （親 slot。amulet/ring/belt） | 装飾品 / Jewellery |

- slot ラベルの日本語は **S5 `ItemClasses.Name`（JA）を優先**し、無いものだけ表に書いたものを使う。実際の訳語（「兜」か「ヘルメット」か）はゲームに合わせる
- `uniques.json` の `item_class` は base_items と表記が違う（`Mace`, `Focii`, `Flask`, `Charm`）。**ユニークの slot は PoB のベース名 → `base_items` の `item_class` から決める**。base が引けないときだけ `uniques.json.item_class` を `slots.py` の別名表（`Mace→one_hand_mace|two_hand_mace` は判定不能なので `weapon` のみ、`Focii→focus`, `Flask→flask`, `Charm→charm`）で救う
- 親 slot（`weapon` / `armour` / `jewellery`）は検索フィルタでまとめて絞るためのもの。`slots[]` に子と一緒に入れる

### 4.3 kind 別 `meta`

| kind | meta |
|---|---|
| `unique` | `base_item_en/ja`, `item_class`, `implicits: [{en,ja}]`（`lines` は explicit のみ。implicit は別枠表示）, `has_stats`(PoB に定義あり), `is_alternate_art`, `origin`（`vaal` / `ezomyte` / `kalguuran` / `""`。§6.7）, `is_vaal_unique`（`origin == 'vaal'`）, `cultivation_target`（Vaal かつ置換対象行あり）。さらに `lines[*]` に `mod_id`（§6.1 手順 7 で引けたとき）, `handwraps: {en, ja, mod_id}`（手袋のみ。§6.6）, `cultivation_replaceable: true`（§6.7）を付ける |
| `notable` | `hash`, `node_id`, `is_keystone`, `stats: {stat_id: value}`, `flavour_text_en/ja`, `granted_skill`（あれば） |
| `ascendancy` | `ascendancy_id`("Warrior3"), `ascendancy_en/ja`("Smith of Kitava"), `class_en/ja`("Warrior"), `is_notable`, `is_start`, `hash`, `stats` |
| `mod` | `mod_id`, `generation_type`, `domain`, `required_level`, `mod_group`, `tags`, `stat_ids`, `stat_ranges: [{id,min,max}]`, `is_essence_only`, `applies_to: [{slot, item_class_en, item_class_ja, tagset, required_level}]`（§6.4）。石の拳・培養用: `transforms_from: {mod_id, en, ja}`（`sub_kind=handwraps` のとき。元 mod）, `handwraps: {mod_id, en, ja}`（通常 mod / cultivation mod に Hand Wraps 版があるとき）, `cultivation_replaceable: true`（元 mod として置換対象のとき） |
| `gem` | `gem_id`(Metadata パス), `gem_type`(active/support/spirit), `is_lineage`, `color`, `tags[]`（タグ ID）, `tags_ja[]`, `skill_types[]`（`active_skill.types`）, `req_str/dex/int`（**属性配分率 %**。必要値ではない）, `level_req`（`per_level["1"]` の `required_level` があれば）, `cast_time`, `summary_en/ja`（ShortDescription）, `desc_en/ja`（Description / support_text）, `detail: [{en,ja} | {levels:[{lv,en,ja}]}]`（Lv1/Lv20 の効果詳細。DATA_PIPELINE.md §5.3）, `recommended_supports[]`（名前 EN/JA）, `weapon_restrictions[]` |
| `timeless` | `jewel`(`kalguur`/`abyss`), `jewel_name_en/ja`（Heroic Tragedy / Undying Hate）, `node_id`, `passive_type`（notable / keystone / small / addition。`PassiveType` 配列から: 4=keystone, 3=notable, 1/2=small）, `conqueror_index`（キーストーンがどの variant 名（Vorana / Medved / Olroth …）に対応するか。`ConquerorIndex` 1..N ↔ PoB `Variant:` の順）, `spawn_weight`, `stats: {stat_id: [min,max]}`, `flavour_en/ja` |
| `socketable` | `base_id`, `type_en/ja`(ルーン/ソウルコア…), `tier`(Lesser/Normal/Greater/Perfect を名前から推定。無理なら `""`), `required_level`, `limit_text_en/ja`, `flags: {socket_bound, unique_items, jewellery, martial_artist, corrupted_sanctified}`, `effects: [{category_id, category_en/ja, target_slots: [...], target_classes_en/ja: [...], lines: [{en,ja}]}]`, `description_en/ja` |

`lines` の中身:

- `unique` … explicit の性能行（PoB 由来。現行 variant のみ）
- `notable` / `ascendancy` … `stats` を `.csd` で文にしたもの（1 ノード複数行）。`reminder_text` は入れない
- `mod` … mod 本文（1 行または複数行）
- `socketable` … `effects[*].lines` を **「対象カテゴリ名: 効果」**の形で平坦化したもの（例 `Martial Weapons: Adds 7 to 11 Fire Damage`）。検索で `兜` と効果文の両方に引っかかるようにする

---

## 5. SQLite スキーマ

正規化テーブル（kind ごと）と、それを射影した `search_docs` の 2 層。UI/CLI は `search_docs` だけ読む。正規化テーブルは再集計・デバッグ・将来の kind 追加のために持つ。

```sql
-- 共通
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);   -- patch_version, built_at, source_counts(json)

-- 参照
CREATE TABLE item_classes (id TEXT PRIMARY KEY, name_en TEXT, name_ja TEXT, category TEXT, slot TEXT);
CREATE TABLE base_items (id TEXT PRIMARY KEY, name_en TEXT, name_ja TEXT, item_class TEXT, slot TEXT,
                         tags TEXT, drop_level INT, release_state TEXT);

-- kind: unique
CREATE TABLE uniques (id TEXT PRIMARY KEY, name_en TEXT, name_ja TEXT, item_class TEXT, slot TEXT,
                      base_item_en TEXT, base_item_ja TEXT, base_item_id TEXT, has_stats INT,
                      implicits_json TEXT, stats_json TEXT, is_alternate_art INT);

-- kind: notable / ascendancy（同じテーブル。ascendancy_id が NULL なら通常ツリー）
CREATE TABLE passives (hash INT PRIMARY KEY, node_id TEXT, name_en TEXT, name_ja TEXT,
                       is_notable INT, is_keystone INT, is_start INT, is_small INT,
                       ascendancy_id TEXT, stats_json TEXT, lines_json TEXT,
                       flavour_en TEXT, flavour_ja TEXT, icon TEXT);
CREATE TABLE ascendancies (id TEXT PRIMARY KEY, name_en TEXT, name_ja TEXT, class_en TEXT, class_ja TEXT, disabled INT);

-- kind: mod
CREATE TABLE mods (id TEXT PRIMARY KEY, name TEXT, text_en TEXT, text_ja TEXT, ja_source TEXT,
                   generation_type TEXT, domain TEXT, required_level INT, mod_group TEXT,
                   tags TEXT, stat_ids TEXT, stats_json TEXT, is_essence_only INT, spawn_tags TEXT,
                   sub_kind TEXT,                 -- prefix|suffix|corrupted|essence|desecrated|handwraps|cultivation
                   transforms_from TEXT,          -- sub_kind=handwraps のとき元 mod の id（§6.6）
                   handwraps_id TEXT,             -- この mod の Hand Wraps 版 mod の id（あれば）
                   cultivation_replaceable INT);  -- Incursion2MutatedUniqueModsClient に含まれる=1（§6.7）
-- ユニークの性能行と mod の対応（§6.1 手順 7）。1 行 = ユニーク 1 行
CREATE TABLE unique_lines (unique_id TEXT, line_no INT, is_implicit INT, text_en TEXT, text_ja TEXT,
                           mod_id TEXT, match_kind TEXT,        -- exact|ambiguous|none
                           PRIMARY KEY (unique_id, line_no));
CREATE TABLE mod_applies_to (mod_id TEXT, item_class TEXT, slot TEXT, tagset TEXT, required_level INT,
                             PRIMARY KEY (mod_id, item_class, tagset));

-- kind: socketable
CREATE TABLE socketables (base_id TEXT PRIMARY KEY, name_en TEXT, name_ja TEXT, type_en TEXT, type_ja TEXT,
                          tier TEXT, required_level INT, limit_en TEXT, limit_ja TEXT,
                          description_en TEXT, description_ja TEXT, flags_json TEXT, tags TEXT);
CREATE TABLE socketable_effects (base_id TEXT, category_id TEXT, category_en TEXT, category_ja TEXT,
                                 target_classes TEXT, target_slots TEXT, lines_json TEXT,
                                 PRIMARY KEY (base_id, category_id));

-- kind: gem
CREATE TABLE gems (id TEXT PRIMARY KEY, name_en TEXT, name_ja TEXT, gem_type TEXT, is_lineage INT, color TEXT,
                   tags TEXT, skill_types TEXT, req_str INT, req_dex INT, req_int INT, level_req INT, cast_time INT,
                   skill_id TEXT, summary_en TEXT, summary_ja TEXT, desc_en TEXT, desc_ja TEXT,
                   detail_json TEXT, recommended_supports TEXT, weapon_restrictions TEXT);
CREATE TABLE gem_tags (id TEXT PRIMARY KEY, name_en TEXT, name_ja TEXT);

-- kind: timeless
CREATE TABLE timeless_passives (id TEXT PRIMARY KEY, jewel TEXT, name_en TEXT, name_ja TEXT, passive_type TEXT,
                                conqueror_index INT, spawn_weight INT, stats_json TEXT, lines_json TEXT,
                                flavour_en TEXT, flavour_ja TEXT, icon TEXT);

-- 検索層
CREATE TABLE search_docs (id TEXT PRIMARY KEY, kind TEXT, sub_kind TEXT, slots TEXT,   -- slots はカンマ区切り
                          name_en TEXT, name_ja TEXT, group_en TEXT, group_ja TEXT,
                          lines_json TEXT, meta_json TEXT, haystack TEXT, sort_key INT);
CREATE INDEX idx_search_kind ON search_docs(kind);
CREATE VIRTUAL TABLE search_fts USING fts5(id UNINDEXED, haystack, tokenize='trigram');
```

**FTS5 trigram の落とし穴（確認済み）**: 3 文字未満のクエリ（`憤怒` など 2 文字）は **0 件を返す**。`search.py` は「正規化後 3 文字以上なら `search_fts MATCH`、未満なら `search_docs.haystack LIKE '%q%'`」と分岐する。件数が 2 万程度なので LIKE でも十分速い。

---

## 6. 構築ルール（`build_db.py`）

### 6.0 共通

- 実行のたびに `poe2db.sqlite` を削除して作り直す
- 各段で**件数と訳率をログに出す**（`INFO unique: 449 件, name_ja 98.2%, stats_ja 94.3%`）。訳率が前回より落ちたら気づけるように `meta.source_counts` に残す
- 翻訳の 3 系統（A: stat ID → `.csd` / B: trade2 英文照合 / C: 完成英文 → `.csd` テンプレ索引）と、誤訳防止の「英語再レンダリング一致検証」は DATA_PIPELINE.md §6 のとおり実装する。**§6.4 の検証は必須**
- **系統 B の `#` 復元**: trade2 の訳は数値が `#` に潰れている（DATA_PIPELINE §6.2）。
  `build_db._restore_values` が英文から値を戻すが、**`#` の数と英文の値の数が一致するときだけ**埋める。
  数が合わないものは順序の対応が保証できないので `#` のまま残す。
  これで `スキルを付与: レベル#` → `スキルを付与: レベル(1-20)` になる（実測: 残る `#` は 77 行 → 1 行）
- `.csd` のハンドラ（`divide_by_ten_1dp_if_required` 等 ≈40 種）は `parsers.HANDLERS` として実装。未知のハンドラは例外にせず、**警告ログを出して値をそのまま使う**

### 6.1 `unique`

1. `uniques.json` の全件を対象（`is_alternate_art` は保持。UI で既定非表示）
2. 名前 JA: `UniqueStashLayout.WordsKey → Words.Text/Text2`（英名で辞書化）
3. 性能: PoB `Uniques/*.json`（27 カテゴリ + `Special/Generated` + `Special/New`）を `parse_pob_uniques` で読み、**英名完全一致**で結合。`{variant:N}` は最後の `Variant:` のみ採用
4. slot: PoB の 2 行目（ベース名）→ `base_items.name`（英）で引いて `item_class` → `slots.py`。引けなければ §4.2 の別名表
5. 行の翻訳: 系統 C → B の順。implicit も同様
6. `lines` = explicit、`meta.implicits` = implicit
7. **行 → mod ID の対応付け**（`unique_lines`。石の拳・培養で必要）:
   - `mods.json` の `generation_type == 'unique'` かつ `domain in ('item','flask','misc')` の mod について、`text` を「マークアップ除去 → 空白圧縮 → 小文字化」した文字列をキーに `{正規化文: [mod_id…]}` の索引を作る（レンジ `(15-20)` は**そのまま残す**。PoB の行もレンジ付きなので完全一致できる）
   - ユニークの各行（現行 variant のみ）を同じ正規化で引く。候補 1 件 → `exact`、複数 → `ambiguous`（全候補を保持し、`handwraps` の変化後文が全候補で同じなら採用、違えば全部表示）、0 件 → `none`
   - 実測（手袋 37 件）: 217 行中 192 行がヒット。ミスは旧 variant 行（variant 絞りで消える）と PoB 独自表記
   - ヒット率をログに出す。`none` の行は `mod_id NULL` のまま残す（石の拳・培養の付加情報が付かないだけで、表示・検索には影響しない）

### 6.2 `notable`（通常ツリー）

1. `Default.json.passives` から `ascendancy` キーが**無い**ノードで `is_notable or is_keystone` のもの
2. `is_jewel_socket`, `is_icon_only`, `is_multiple_choice*`, `is_atlas_root` は除外
3. 名前 JA: `PassiveSkills`（S5）を `Id == node.id` で引く。0 件なら `PassiveSkillGraphId == hash` で再試行。どちらで当たったかを集計してログ
4. 本文: `stats {id: value}` を `.csd` で文にする
   - まず `passive_skill_stat_descriptions.csd` で、ノードの stat ID 集合の**部分集合をキーに持つブロック**を探す。複数 ID を 1 文にするブロック（例 `{0}% increased X and {1}% increased Y`）があるので、**ID 数が多いブロックから貪欲に**当てて消費し、残った ID を単独で引く
   - 見つからない ID は `passive_skill_aura_…` → `passive_skill_variant_…` → `stat_descriptions.csd` → 全 `.csd` 統合の順で引き、統合フォールバック時は §6.4 検証を掛ける
   - どうしても引けない ID は `lines` に `{"en": "<stat_id> = <value>", "ja": ""}` として残す（隠さない）
   - `_no_display` で終わる stat ID（例 `base_physical_damage_reduction_rating_no_display`）は本文に出さない（`meta.stats` には残す）
5. `sub_kind`: `keystone` / `notable`

### 6.3 `ascendancy`

1. `passives` から `ascendancy` キーが**ある**ノード全部（小ノードも。`sub_kind` = `start` / `notable` / `small`）
2. `ascendancies.json[ascendancy]` から `name`（EN）、`character[1]`（クラス名 EN）、`disabled`
3. `disabled == true` **または `name` に `[DNT-UNUSED]` を含む**アセンダンシーは丸ごと除外（実測: Marauder / Duelist / Templar / Shadow 系は `disabled=false` のまま名前だけ `[DNT-UNUSED] Bait Fisher` 等になっている）。残りでも「ノード名が全て空」「stats が全て空」のものは除外し、ID をログ。残るのは 8 クラス 23 アセンダンシー（§13）
4. 名前 JA: §6.2 と同じ。アセンダンシー名 JA は `Ascendancy.Name`（S5）を `Id` で、クラス名 JA は `Characters.Name` を `Id` で
5. 本文: §6.2 と同じ手順
6. `group_en/ja` = アセンダンシー名。`meta.class_*` にクラス名。UI ではアセンダンシー単位のフィルタも出す

### 6.4 `mod`

1. `mods.json` から `domain in ('item','flask','desecrated')` かつ `generation_type in ('prefix','suffix','corrupted','essence')` かつ `text` が空でないもの
   - `unique` generation_type（10,447 件）は**除外**（ユニークの性能は PoB 側で持つ）
   - `desecrated` ドメイン（415 件）はボーンクラフト系。**含める**が `sub_kind = desecrated` で区別
2. 本文 JA: 系統 A（`stats[].id` の組で `.csd`）→ B。DATA_PIPELINE.md §6.1〜6.2 のとおり
3. **付く装備（`mod_applies_to`）**: `mods_by_base.json` を走査し、`{クラス表示名 → タグ組 → gen_type → mod_group → mod_id → required_level}` を **mod_id をキーに反転**する
   - クラス表示名（`Helmets`, `Body Armours`, `Charms`, `Jewels`, `Talismans` …）は `item_classes.json[*].name` と一致するので、それで `item_class` ID に戻し、`slots.py` で slot に落とす
   - タグ組（例 `str_armour,helmet,armour,default`）はそのまま `tagset` に保存。UI の詳細で「STR 兜のみ」のような補足に使う。**同じクラスに複数タグ組があれば、そのクラスに付く mod は和集合**
   - `applies_to` が空の mod（どのベースにも付かない）は `sub_kind` に関わらず収録するが、UI で「付く装備なし」と表示
4. `meta.stat_ranges` に `stats[].{id,min,max}` を保持（tier 比較に使える）

`spawn_weights` から自前で計算するのではなく **`mods_by_base` を正とする**（repoe 側が `base_items.tags` との照合を済ませている）。ただし検証として、`spawn_weights` に `weight > 0` のタグを持つのに `mods_by_base` に一度も現れない item ドメイン mod の件数をログに出す。

### 6.5 `socketable`

1. `base_items.json` から `item_class == 'SoulCore'` かつ `release_state == 'released'`（313 件。ルーンもソウルコアもこのクラス）
2. 名前 JA: `BaseItemTypes`（S5）を Metadata パスで
3. 本体情報: `SoulCores`（S5）を `BaseItemType`（→ `BaseItemTypes` 行番号 → `Id` = Metadata パス）で引く
   - `Type` → `SoulCoreTypes.Name`（EN/JA）を `type_*` に
   - `Limit` → `SoulCoreLimits.Text` を `limit_*` に
   - `Description` / `ExtraDescription` → `ClientStrings2.Text`
   - bool 列は `flags_json` に
4. **効果**: `SoulCoreStats` を `SoulCore` 行番号で全行取り、行ごとに
   - `StatCategory` → `SoulCoreStatCategories`：`Display`（EN/JA）、`TargetItemClasses`（行番号配列 → `ItemClasses.Id`）→ `slots.py` で `target_slots`
   - **実測の注意**: `TargetItemClasses` が**空**のカテゴリがある（`All`=全装備, `Martial Weapon`, `Armour`）。これらは `slots.py` に「カテゴリ ID → slot 集合」の固定表を持って展開する（`Armour` → helmet/body_armour/gloves/boots/shield/focus、`Martial Weapon` → 武器 slot のうち wand/staff/sceptre 以外、`All` → 全 slot）。`Display` が空のカテゴリ（`Helmet`, `Gloves` 等）は `TargetItemClasses` があるのでクラス名を表示名にする。実測 31 カテゴリ（`Caster Weapon` = Wand/Staff/Sceptre、`Two Handed Weapon` = Warstaff/Two Hand Mace/Talisman/Crossbow/Bow/Staff 等）
   - `Stats`（行番号配列 → `Stats.Id`）と `StatsValues` を組にして `.csd`（`stat_descriptions.csd`）で EN/JA の文にする。`BondedStats` があれば `lines` の末尾に「（Bonded）」付きで追加
5. `tier`: 名前の先頭が `Lesser ` / `Greater ` / `Perfect ` ならそれ、無ければ `Normal`。`Rune of …`（Expedition 系固有ルーン）は名前に prefix が無いので `Normal` でよい
6. `lines` = 全 effects を `"<category_en>: <line_en>"` / `"<category_ja>: <line_ja>"` に平坦化
7. `slots` = 全 effects の `target_slots` の和集合（親 slot 含む）

**このデータは旧プロトに無かった。** `SoulCoreStats` の構造は dat-schema で確認しているが実値は未確認なので、最初に 1 ルーン（`Metadata/Items/SoulCores/RuneFire` = Desert Rune）を手で追って、ゲーム内表示（武器: 火ダメージ追加 / 防具: 火耐性）と一致することを確認してから全件処理する。

### 6.6 石の拳（Hand Wraps）変化 — `mod.sub_kind = handwraps` + ユニーク手袋への付加

背景: Monk のアセンダンシー **Martial Artist**（`Monk1`）のノード **"Way of the Stonefist"**（JA: 石拳の道。stats `ascendancy_hand_wraps: 1`, `ignore_attribute_requirements_for_gloves: 1`）を取ると、装備中の手袋が Hand Wraps 扱いになり、手袋の mod が別の mod に置き換わる。ゲームデータ上は**変化後 mod が独立した mod として `mods.json` に入っている**。

1. `mods.json` から ID が `HandWraps` で始まる mod（598 件）を取り、`sub_kind = 'handwraps'` で `mods` に入れる。本文 JA は §6.4 と同じ系統 A → B
2. **元 mod との対応**: ID から `HandWraps` を剥がした文字列が元 mod の ID（例 `HandWrapsStrength1` → `Strength1`、`HandWrapsUniqueIncreasedLife9` → `UniqueIncreasedLife9`）。実測 588/598 が存在。存在しない 10 件（`HandWrapsImplicit…` 3 件、`HandWrapsLocal…7` 系など）は `transforms_from = NULL` のまま収録し、ID をログに出す
3. 元 mod 側に `handwraps_id` を書く（逆参照）。元 mod が `sub_kind=cultivation` のもの（`HandWrapsUniqueMutatedVaal…` 28 件）も同様に対応付ける
4. `SearchDoc` への射影:
   - handwraps mod の `lines` = 変化後本文、`meta.transforms_from` = 元 mod の `{mod_id, en, ja}`。`haystack` には**元 mod の本文も含める**（「Strength で検索 → Hand Wraps 化で AoE になる」が引ける）
   - 通常 mod の `meta.handwraps` = `{mod_id, en, ja}`。`haystack` には変化後本文を**含めない**（通常 mod 検索が汚れないように。UI では詳細に「石の拳: …」を出す）
   - `applies_to`: handwraps mod は `spawn_weights` が空なので `mods_by_base` に現れない。**元 mod の `applies_to` をそのままコピー**する（元が手袋に付くなら変化後も手袋）。元が無い 10 件は `[{slot: gloves}]` 固定
5. **ユニーク手袋**: §6.1 手順 7 で `mod_id` が付いた行のうち、その mod に `handwraps_id` があるものに `lines[*].handwraps = {mod_id, en, ja}` を付ける。実測 177 行。ユニーク側の `haystack` には変化後本文を**含める**（ユースケース 5 は「変化後の性能で探す」が主）が、UI では変化後本文でヒットした行に「石の拳」バッジを出して区別する
6. slot: handwraps mod の `slots` は元 mod と同じ（実質 `gloves`, `armour`）

### 6.7 培養のオーブ（Vaal Cultivation Orb）— `mod.sub_kind = cultivation` + ユニークへの付加

背景: カレンシー `Vaal Cultivation Orb`（`Metadata/Items/Currency/CurrencyIncursionMutateUnique`。説明文 "Replaces up to 2 modifiers on a Corrupted Vaal Unique / Replaces other Uniques with a Corrupted Unique of the same Item Class"）。

1. **置換プール**: `mods.json` から ID が `UniqueMutatedVaal` で始まる mod（204 件。全て `generation_type=unique`、`implicit_tags` に `mutatedunique_vaal`）を `sub_kind = 'cultivation'` で `mods` に入れる。本文 JA は系統 A → B
2. **置換対象の元 mod**: GGPK `Incursion2MutatedUniqueModsClient`（1 行、`Mods` 配列 242 件 → `Mods.Id`）に含まれる mod に `cultivation_replaceable = 1` を立てる。全 242 件が `mods.json` に存在することを確認済み
3. **Vaal Unique の判定**: GGPK `UniqueOrigins`（§3.2）で `Origin == 'Vaal'` のユニーク（英名で結合。実測 40 件: Atziri's Acuity, Atziri's Step, Coward's Legacy, Drillneck, Idle Hands, Dream Fragments, Glimpse of Chaos, Hateforge, Doryani's Prototype …）を `meta.is_vaal_unique = true`。`origin` は他の値（`ezomyte` / `kalguuran`）も保持し、無ければ `""`。**ベースの `vaal_basetype` タグは無関係**（実測で相関なし。使わない）
4. ユニーク側の置換対象印: **`is_vaal_unique` のユニークに限り**、§6.1 手順 7 で `mod_id` が付いた行のうち `cultivation_replaceable` なものに `lines[*].cultivation_replaceable = true`。ユニーク単位で「Vaal かつ置換対象行が 1 つ以上」を `meta.cultivation_target = true`（実測 37/40。残り 3 件 Arakaali's Gift / Flesh Crucible / The Adorned は行照合の漏れなのでログで追う）。非 Vaal のユニークにも `OriginalMods` の mod は載っている（159 件）が、オーブは「同クラスの Corrupted Unique に置き換える」挙動なので印を付けない
5. **元 mod → 置換先の対応は存在しない**（プールから抽選と解釈）。元 mod の `groups` と cultivation mod の `groups` の重なりは 38/126 しか無く、グループ一致ルールではない。よって UI は「プール全体を検索できる」「Vaal ユニークのどの行が置換され得るかが分かる」まで。アイテムクラスごとの抽選制限があるかは §11 Q9
6. `SearchDoc`: cultivation mod の `slots` は空（付く先はユニーク依存）。`meta.handwraps` があれば §6.6 と同じく詳細に出す

### 6.8 `gem`（スキル・サポート・リネージュ・スピリット）

旧プロトの実装知見は DATA_PIPELINE.md §4（結合キー）、§5.3（`stat_sets`）、§6.4（誤訳防止）、§6.5（説明文の日本語）にそのまま書いてある。それに従う。

1. `skill_gems.json` の全件（1,191。全て `release_state=released`）。`base_item.display_name` を英名、`BaseItemTypes`（S5）を Metadata パスで引いて日本語名
2. `sub_kind`: `gem_type == 'spirit'` → `spirit` / `gem_type == 'support'` かつ `tags ∋ lineage` → `lineage` / `support` → `support` / `active` → `active`
3. スキル本体: `grants_skills[0]` で `skills.json` を引く。`active_skill.types` を `skill_types` に、`weapon_restrictions` を保持
4. 説明文: active は `ActiveSkills`（`Id == active_skill.id`）の `ShortDescription` / `Description`（EN/JA）。support は `skills[].support_gem` の英文（旧プロトでは `skill_gems[].support_text`。**repoe の現行構造では `skills.json` 側の `support_gem` にある**ので実データで位置を確認する）を `GemEffects.SupportText` 英文完全一致で JA へ
5. 効果詳細（`detail`）: `stat_sets[].static.stat_text` と `per_level["1"|"20"].stat_text` を `.csd`（`translation_file` 指定 → 汎用 3 本 → 統合）で JA 化。**§6.4 の英語再レンダリング検証を必ず掛ける**（旧プロトで誤訳が出た箇所）。`quality_stats` は初版も非表示
6. タグ: `skill_gems[].tags` をそのまま `tags`。表示名は `gem_tags.json`（EN）/ `GemTags.Name`（JA）からマークアップを剥がして `gem_tags` テーブルに。`grants_active_skill` `support` `meta` `low_max_level` `exceptional` `awakened` `vaal` `link` は**内部タグなので UI のチップからは除外**（検索 haystack にも入れない）
7. `recommended_supports[]` は Metadata パス → 名前 EN/JA に解決して保持（スキル詳細で「推奨サポート」を出す）
8. `SearchDoc`: `name` = ジェム名、`group` = sub_kind の表示名（スキル / サポート / リネージュ / スピリット）、`lines` = `[summary, desc, detail 行…]`（Lv20 側の文。Lv1 は詳細のみ）、`slots` = `[]`、`haystack` にタグ表示名（EN/JA）も入れる（「ノヴァ」で検索してもヒットする）

### 6.9 `timeless`（タイムレスジュエルの変化パッシブ）

背景: PoE2 のタイムレスジュエルは **Heroic Tragedy**（"Passives in radius are Conquered by the Kalguur"。variant Vorana / Medved / Olroth）と **Undying Hate**（"…Conquered by the Abyssals"。variant Amanamu / Kulemak / Kurgal / Tecrod / Ulaman）の 2 種（PoB `jewel.json` で確認）。半径内のノータブルが置き換わり得る候補は GGPK `AlternatePassiveSkills` にある。

1. `AlternateTreeVersions` で `ConquerorType in ('Kalguuran','Abyss')` の行番号を取り、`AlternatePassiveSkills` をその `AlternateTreeVersion` で絞る（Kalguuran 40 / Abyss 36。**他の 155 行は PoE1 レガシーなので捨てる**）
2. `jewel`: Kalguuran → `kalguur`（Heroic Tragedy）、Abyss → `abyss`（Undying Hate）。ジュエル名 EN/JA は `uniques` テーブル（§6.1）から引く
3. `passive_type`: `PassiveType` 配列に 4 → `keystone`、3 → `notable`、1 or 2 → `small`。Kalguuran 実測: keystone 3 / notable 37
4. `conqueror_index`: キーストーン 3 件が 1..3 で、PoB の `Variant:` の宣言順（Vorana=1, Medved=2, Olroth=3）に対応すると仮定する。UI では「Vorana のとき: Black Scythe Training」のように variant 名を併記。対応が違えば §11 Q11
5. 本文: `Stats`（行番号 → `Stats.Id`）と `Stat1..Stat6` の `[min,max]` を組にして `passive_skill_stat_descriptions.csd` で EN/JA 化（§6.2 手順 4 と同じ貪欲マッチ + 検証）。min ≠ max のときは `(min-max)` レンジ表示
6. `AlternatePassiveAdditions`（Abyss 1 行）は `passive_type = 'addition'` で同テーブルに入れる
7. `SearchDoc`: `name` = ノード名、`group` = ジュエル名、`lines` = 効果行、`meta.spawn_weight`（抽選重み。表示は参考値）

### 6.10 `search_docs` への射影

各正規化テーブルから §4.1 の形に変換して INSERT。`sort_key` は kind 内の既定順（unique: 名前順 / notable: 名前順 / ascendancy: クラス→アセンダンシー→start,notable,small / mod: generation_type→mod_group→required_level / socketable: type→tier→名前 / gem: sub_kind→level_req→名前 / timeless: jewel→passive_type(keystone, notable, small, addition)→名前）。

**ユニークは部位ごとの塊にする**（実装済み）。`sort_key` に `slots.SLOT_ORDER` の添字を入れ、
検索時も**関連度より先に部位順を効かせる**ので、検索結果でも同じ部位がまとまる。
UI はユニークのグループ内で部位が変わる位置に小見出し（部位名 + 件数）を挟む。
順序は装備欄に近い並び（兜 → 鎧 → 手袋 → 靴 → 盾 → フォーカス → 矢筒 → アミュレット →
指輪 → ベルト → ジュエル → チャーム → フラスコ → タリスマン → 武器各種）。

---

## 7. 検索仕様

### 7.1 クエリ構文（UI・CLI 共通、`parsers.parse_query`）

| 記法 | 意味 |
|---|---|
| `憤怒` | 部分一致（正規化後）。日英どちらのテキストにも当たる |
| `憤怒 チャージ` | 空白区切りは **AND**（すべての語が haystack に含まれる） |
| `-移動` | 除外 |
| `"maximum life"` | フレーズ（空白込みで一致） |
| `kind:mod` `kind:notable,ascendancy` | kind 絞り込み（UI ではチップで同じことをする） |
| `slot:helmet` `slot:armour` | slot 絞り込み（親 slot 可） |
| `sub:prefix` `sub:keystone` `sub:handwraps` `sub:cultivation` | sub_kind 絞り込み |
| `asc:Warrior3` または `asc:"Smith of Kitava"` | アセンダンシー絞り込み |
| `hw:yes` | Hand Wraps 版がある mod / Hand Wraps 変化行を持つユニークだけ（ユースケース 5） |
| `cult:yes` | 培養の置換対象行を持つ Vaal ユニーク（`cultivation_target`）/ 置換対象の元 mod だけ（ユースケース 6） |
| `origin:vaal` `origin:ezomyte` `origin:kalguuran` | ユニークの起源で絞り込み |
| `tag:melee` `tag:melee,nova` | ジェムのタグで絞り込み（カンマは **AND**。`tag:melee tag:nova` も同じ） |
| `gem:active` `gem:support` `gem:lineage` `gem:spirit` | ジェム種別（`kind:gem sub:lineage` の短縮） |
| `type:Totemable` | `active_skill.types` の内部型で絞り込み（上級者向け。UI には出さない） |
| `color:r` `color:g` `color:b` | ジェムの属性色 |
| `jewel:kalguur` `jewel:abyss` | タイムレスジュエルの種別（`kind:timeless` を含意） |

- テキスト条件が 0 個でも kind/slot 条件だけで一覧できる（ユースケース 3）
- 正規表現は初版では非対応（将来 `/…/` を予約）

### 7.2 マッチング

1. クエリ語を `normalize_for_search` に掛ける
2. 各語について `haystack` に部分一致（trigram FTS or LIKE。UI は JS の `includes`）
3. AND / NOT を適用
4. kind / slot / sub / asc フィルタを適用

### 7.3 ランキング（同点は `sort_key`）

| 優先 | 条件 |
|---|---|
| 1 | `name_ja` または `name_en` が語に**完全一致** |
| 2 | 名前に**前方一致** |
| 3 | 名前に部分一致 |
| 4 | `group_*` に一致 |
| 5 | 本文（`lines`）に一致 |

複数語のときは各語のスコア和。kind をまたいで混ぜて出さず、**kind ごとにグループ表示**（§8）。

### 7.4 CLI `search.py`

```
python search.py 憤怒
python search.py -k notable,ascendancy エナジーシールド
python search.py -k socketable -s helmet
python search.py -k mod "chance to Ignite" --json
```

- 出力は `kind / 名前(JA [EN]) / slots / 本文 1 行目` の表。`--json` で `SearchDoc` 配列
- `--lang en|ja|both`（既定 both: JA があれば JA、無ければ EN に `[EN]` 印）
- SQLite を直接引く（§5 の FTS/LIKE 分岐）

---

## 8. UI 仕様（`template.html` → `poe2db.html`）

### 8.1 方針

**最優先は見やすさ。スタイリッシュさは二の次。** 迷ったら「情報が速く正確に読めるか」で決める。
見た目を整えるために情報を削る・隠す・詰め込みすぎる、という判断はしない。具体的には:

- **省略しない**（§12-B）。効果行は全行出し、結果の件数も切らない
- **種類の違う情報は見た目で区別する**（§8.6）。効果は箇条書き、説明は地の文、補助情報はバッジ
- **同じものは同じ位置・同じ規則で出す**。kind が違っても名前・バッジ・本文の並びは変えない
- 装飾（角丸・影・アニメーション・グラデーション）は情報の区別に使うときだけ入れる。
  ホバーやトランジションで内容が動くことはしない
- コントラストを確保する。本文は `#c4c8d8` 以上、補助情報でも `--dim2`(#6e7489) までに留める
- 密度を優先するが、行間は 1.5 以上を確保して詰めすぎない
- 反応速度も見やすさのうち。入力から描画まで体感で待たせない

その他の前提:

- **単一 HTML、依存ライブラリ無し、`file://` で動く**。データは `__DATA__` に JSON 埋め込み（`<` は `<` にエスケープ）
- 起動時に `search_docs` 相当の配列をメモリに持ち、入力のたびに全件走査（実測 2〜8ms）
- ダークテーマ固定。フォントはシステム標準

### 8.2 画面構成

```
┌──────────────────────────────────────────────────────────────┐
│ [検索ボックス ────────────────────────────]  JA|EN|両方  v4.5.5.2 │
│ kind: [全て] [ユニーク] [ノータブル] [アセンダンシー] [mod] [ソケット] [ジェム] [タイムレス] │
│ slot: [兜][鎧][手袋][靴][盾][…][ジュエル][チャーム][フラスコ] [武器▾]  │
│ (kind=ascendancy 時) クラス/アセンダンシーのチップ                │
│ (kind=mod 時) [prefix][suffix][corrupted][essence][desecrated]    │
├──────────────────────────────────────────────────────────────┤
│ ▼ ユニーク (12)                                                  │
│   アストラメンティス  [Amulet / アミュレット]                       │
│     全ての属性 +(80-100) …                                       │
│ ▼ ノータブル (8)                                                 │
│   …                                                            │
│ ▼ mod (43)                                                      │
│   +(10-19) to maximum Life   prefix Lv1                          │
│     付く装備: 鎧 兜 手袋 靴 盾 ベルト アミュレット リング             │
│ ▼ ソケット (5)                                                   │
│   Desert Rune  [武器 / 防具]                                     │
│     Martial Weapons: … / Armour: …                              │
└──────────────────────────────────────────────────────────────┘
```

- 結果は **kind ごとの折りたたみグループ**。見出しに件数。既定は全グループ展開、各グループ最大 50 件 + 「さらに表示」
- 各行: 名前（JA。無ければ EN に `EN` バッジ）、右に slot バッジ、下に本文。**ヒット語をハイライト**
- 行クリックで詳細展開（implicit、`meta`、EN/JA 併記、mod なら `applies_to` の tagset と required_level、socketable なら effects をカテゴリ別に）
- 「付く装備」の slot バッジをクリックすると、その slot で絞り込み（ユースケース 4 → 3 への接続）
- 表示言語トグル: `JA`（訳があれば JA、無ければ EN）/ `EN` / `両方`（並記）
- **石の拳トグル**（ヘッダに `拳` スイッチ）: ON にすると
  - ユニーク手袋の行のうち `handwraps` を持つものは、元の行の下に `→ 変化後本文` を常時表示（OFF 時は詳細展開でのみ）
  - kind=mod のフィルタに `handwraps` チップが現れ、変化後 mod は「`元本文` → `変化後本文`」の 2 段で表示
  - 変化後本文でヒットした結果には「石の拳」バッジ
- **培養トグル**（`培` スイッチ）: ON にすると
  - Vaal ユニークは名前横に「Vaal」バッジ、その各行に置換対象なら `◆` 印。非 Vaal ユニークには「オーブ使用 → 同クラスの Corrupted Vaal Unique に置換」の注記のみ
  - kind=mod のフィルタに `cultivation` チップ。プール mod は slot 無しで一覧

### 8.3 操作

- 入力は 50ms デバウンス。**Enter 不要**
- `/` で検索ボックスにフォーカス、`Esc` でクリア、`↑↓` で行移動、`Enter` で詳細展開、`1`〜`5` で kind トグル
- 状態（クエリ・kind・slot・言語）を URL ハッシュに保持 → ブラウザの戻る/進む・ブックマークが効く
- 検索ボックス右に「N 件 / 12ms」を出す（性能劣化に気づくため）

### 8.4 表示規則

- 「訳があれば日本語のみ、無ければ `EN` 印付き英語」は 1 関数（`pickText(line, lang)`）に集約し、全 kind で同じ規則
- 英文マークアップ `[Key|Text]` は表示前に剥がす（`haystack` だけでなく `lines` にも）。剥がす処理は **ビルド時に済ませて** `lines` には剥がした文を入れる。元文が要るなら `meta` に

---

### 8.5 スキル専用ページ（ユースケース 8）

ヘッダのタブで「横断検索」と「スキル」を切り替える（同じ HTML 内。URL ハッシュ `#skills`）。

```
┌──────────────────────────────────────────────────────────────┐
│ [横断検索] [スキル]                                             │
│ 種別: [スキル] [サポート] [リネージュ] [スピリット]   色: [赤][緑][青]   │
│ タグ: [attack][melee][strike][slam][nova][projectile][area][spell]  │
│       [fire][cold][lightning][chaos][physical][minion][totem]     │
│       [duration][buff][trigger][warcry][curse][mark][herald]…     │
│       （選択中は強調。複数選択は AND。右端に「クリア」）              │
│ 絞り込みテキスト: [________]   並び: [名前|必要Lv|属性]   N 件      │
├──────────────────────────────────────────────────────────────┤
│ 名前(JA [EN])   色  Lv  タグ                概要                │
│ ボーンシャッター  赤  1   attack melee slam   …                   │
│   ▸ 展開: 説明 / Lv1・Lv20 効果詳細 / 推奨サポート / 武器制限       │
└──────────────────────────────────────────────────────────────┘
```

- タグチップは **選択中の種別に実在するタグだけ**を、件数付きで出す（例 `nova (21)`）。ゼロ件のタグは出さない
- 種別 = `sub_kind`。既定は「スキル」のみ ON。リネージュはサポートとは別チップ（リネージュだけ見たいケースが多い）
- 一覧は表形式（横断検索のカード形式ではない）。1 行 = 1 ジェム。クリックで展開
- 展開内容: `summary` / `desc` / `detail`（Lv1 と Lv20 を並べる）/ 推奨サポート（クリックでそのサポートに飛ぶ）/ 武器制限 / 属性配分率
- 絞り込みテキストはこのページ内のジェムに対する部分一致（§7 と同じ正規化）。空でもタグだけで一覧できる
- 状態（種別・タグ・テキスト・並び）は URL ハッシュに入れる。「近接ノヴァ」のような組み合わせをブックマークできる
- サポート / リネージュを選んだときはタグ集合が変わる（サポートには `support` 以外に効果対象タグが付いている）。同じ UI で扱う

### 8.6 本文の描き分け（効果リスト vs 説明文）

同じ本文でも「**効果**（1 つ 1 項目で数えられるもの）」と「**説明文**（地の文）」は別物なので、
見た目で区別する。ユニークの mod とスキルの効果を、説明の散文と同じ見た目で並べない。

| 種類 | 何が入るか | 描き方 |
|---|---|---|
| 効果（stat） | ユニークの mod / implicit、mod 本文、パッシブ・アセンダンシー・タイムレスの効果、ソケットの装着先別効果、ジェムの効果詳細 | `<ul class="stats">` の箇条書き。行頭に小さな四角、本文色 `#c4c8d8` |
| implicit | ユニークの implicit（付与スキル名など） | 同じ箇条書きだが**一覧にも出す**。ゲームと同じく explicit の上に置き、丸印 + 青みがかった色 + 点線の区切りで分ける |
| 説明文（prose） | ジェムの概要（`ShortDescription`）と説明（`Description` / サポート説明）、ソケット可能アイテムの説明 | `<p class="prose">`。左に細い罫線、1 段落として流す。色は `--dim`、やや小さめ |

- **ユニークの implicit は詳細を開かなくても一覧に出す**。付与スキル名（`スキルを付与: レベル(1-20) …`）は
  ビルドを決める情報なので、隠さない
- 実装は `bodyLines(doc)` が `{en, ja, prose?, imp?}` を返し、`rowHTML` が `prose` の有無で
  `<ul>` と `<p>` を切り替える。詳細パネルは `pairs(title, arr, prose)` の第 3 引数で同じ切り替えをする
- 効果に付く印（石の拳の `→ 変化後`、培養の `◆`、`EN` 印）は箇条書きの項目内に置く
- 新しい kind を足すときは、その本文がどちらなのかを必ず決めてから `bodyLines` に足す

## 9. 受け入れテスト（`tests/`、pytest）

ビルド後の `poe2db.sqlite` に対して `search.py` の関数を呼ぶ形で書く。数値は「その値以上」で書き、パッチで変わる部分は緩める。

| # | テスト | 期待 |
|---|---|---|
| T1 | `search("憤怒")` | 結果の `kind` 集合が `{unique, notable, ascendancy, mod, gem}` を全て含む（socketable / timeless は憤怒系が無い可能性があるので必須にしない） |
| T2 | `search("エナジーシールド", kinds=["notable","ascendancy"])` | 結果の kind がその 2 種のみ。各 10 件以上 |
| T3 | `search("", kinds=["socketable"], slots=["helmet"])` | 1 件以上。すべての結果の `slots` に `helmet` を含む。`effects` に `target_slots ∋ helmet` のカテゴリがある |
| T4 | `search("maximum life", kinds=["mod"])` | `IncreasedLife1`（Hale）を含み、その `applies_to` の slot 集合 ⊇ `{body_armour, shield, helmet, gloves, boots, belt, amulet, ring}` |
| T5 | ユニーク slot | `Astramentis` の `slots == ["amulet","jewellery"]`。`item_class=Charm` のユニークが 12 件で全て `slots ∋ charm`、`Flask` が 6 件、`Jewel` が 15 件 |
| T6 | 大小・全半角 | `search("RAGE")` と `search("rage")` と `search("ｒａｇｅ")` が同じ結果 |
| T7 | 2 文字クエリ | `search("憤怒")` が空でない（FTS trigram の落とし穴） |
| T8 | 訳率 | `unique.name_ja` ≥ 95%、`mod.text_ja` ≥ 90%、`notable.name_ja` ≥ 90%（初回計測後に閾値を本書に追記） |
| T9 | 誤訳ガード | `.csd` 統合フォールバックで採用した訳は、英語再レンダリングが `normalize()` 後に元英文と一致している（サンプリング 100 件） |
| T10 | 除外 | `search("")` の ascendancy に `disabled` なアセンダンシー、名前空ノードが含まれない |
| T11 | ハンドラ | `.csd` の `divide_by_ten_1dp_if_required` が効いている（`17m` ではなく `1.7m` になる例を 1 つ固定） |
| T12 | 石の拳 mod | `mods` に `sub_kind='handwraps'` が 590 件以上。`HandWrapsStrength1.transforms_from == 'Strength1'`、`Strength1.handwraps_id == 'HandWrapsStrength1'`。`transforms_from` 非 NULL が 580 件以上 |
| T13 | 石の拳ユニーク | 手袋ユニーク（`hw:yes`）が 30 件以上、`handwraps` を持つ行が合計 95 行以上。**実測 35 件 / 101 行** |
| T14 | 培養プール | `sub_kind='cultivation'` が 200 件以上、全て `generation_type='unique'`。`cultivation_replaceable=1` が 242 件。`is_vaal_unique` が 35 件以上で `Atziri's Acuity` と `Drillneck` を含む。`search("", kinds=["unique"], cult=True)` の結果は全て `is_vaal_unique` かつ `cultivation_replaceable` 行を持ち、30 件以上。**実測 プール 204 / Vaal 40 / 置換対象 34** |
| T15 | 行→mod 対応 | `unique_lines` で `mod_id` が付く率 80% 以上（**実測 86.9%**）。`match_kind='exact'` は 45% 以上（**実測 48.8%**）。同じ英文の mod が複数ある行は `ambiguous` になるが mod_id は付く。付加情報（石の拳・培養）は**全候補が一致したときだけ**出す |
| T16 | ジェム | `gems` が 1,100 件以上。`sub_kind` が active ≥ 450 / support+lineage ≥ 600 / lineage ≥ 80 / spirit ≥ 38。lineage は全て `gem_type='support'`。`search("憤怒")` の結果に `kind='gem'` が含まれる。**実測 1,120 件（active 456 / support 545 / lineage 80 / spirit 39）**。`[DNT]` 開発用 71 件を除いた数 |
| T17 | スキルページ | `search("", kinds=["gem"], sub=["active"], tags=["melee","nova"])` が 1 件以上で、全結果の `tags` が両方を含む。`tags=["nova"]` が 15 件以上。`gem_tags` に `nova` の JA 名がある |
| T18 | タイムレス | `timeless_passives` が Kalguuran 38〜42 件・Abyss 34〜38 件で、PoE1 レガシー（Vaal / Eternal / Templar …）を含まない。Kalguuran に `passive_type='keystone'` が 3 件、`Scorched Earth`（JA `焦げた大地`）を含む。`search("", kinds=["timeless"], jewel="kalguur")` が 38 件以上 |
| T19 | 誤訳ガード（ジェム） | `detail` の JA は英語再レンダリング一致検証を通ったものだけ（T9 と同じ方式でサンプリング 100 行） |

---

## 10. 実装順序（マイルストーン）

各 M の終わりに「動くもの」があること。M2 の時点で既に使える。

| M | 内容 | 完了条件 |
|---|---|---|
| M0 | `fetch_data.py`（S1〜S5。S5 の `config.json` は §3.2 の全テーブル）、`parsers.py`（csd / ハンドラ / PoB / normalize） | `data/` `datexport/` が揃う。`parse_csd` の単体テストが通る |
| M1 | `build_db.py` で `unique` + `mod`（+ `mod_applies_to`）+ `search_docs` + `search.py` | T4, T5, T6, T7 が通る |
| M2 | `template.html` / `export_web.py` → `poe2db.html` | ブラウザでユースケース 1, 4 が動く |
| M3 | `notable` + `ascendancy` | T2, T10 が通る。UI にアセンダンシーフィルタ |
| M4 | `socketable` | T3 が通る。Desert Rune の効果がゲーム内表示と一致 |
| M5 | `unique_lines`（§6.1 手順 7）+ 石の拳（§6.6）+ 培養（§6.7）+ UI トグル | T12〜T15 が通る。ユースケース 5, 6 が動く |
| M6 | `gem`（§6.8）+ スキル専用ページ（§8.5） | T16, T17, T19 が通る。ユースケース 7, 8 が動く |
| M7 | `timeless`（§6.9） | T18 が通る。ユースケース 9 が動く |
| M8 | 訳率の計測と T8 閾値確定、T9、T11、ハイライト・キーボード操作・URL 状態 | 全テスト緑。`docs/SPEC.md` の数値を実測に更新 |
| M9 | §11 の未確定項目のうちユーザー回答が要るもの | 回答後 |

---

## 11. 未確定・要確認事項

| # | 事項 | 状態 |
|---|---|---|
| Q1 | ~~「石の拳専用 mod」の意味~~ | **解決**: Martial Artist の Way of the Stonefist による Hand Wraps 化。§6.6 に仕様化 |
| Q2 | ~~`PassiveSkills` の結合キー~~ | **解決**: `Id` でも `hash` でも 100% 一致（1,686 ノードで実測） |
| Q8 | ~~「Vaal Unique」の判定基準~~ | **解決**: GGPK `UniqueOrigins.Origin == 'Vaal'`（40 件）。ベースの `vaal_basetype` タグは無関係だった（置換対象行を持つ 196 件のうち vaal_basetype は 36 件のみ、逆に Vaal 起源 40 件は 37 件が置換対象行を持つ）。§6.7 手順 3 |
| Q9 | 培養プール 204 件に、ユニークのアイテムクラスによる抽選制限があるか（`UniqueMutatedVaalBelt…` のようにクラス名を含む ID がある一方、`spawn_weights` は空） | データからは不明。初版は制限なしで全件表示。`groups`/`type` に `Belt`, `Local…` を含むものだけ「クラス依存の可能性」バッジ |
| Q11 | タイムレスのキーストーン `ConquerorIndex`（1..3 / 1..5）と PoB `Variant:` 名（Vorana / Medved / Olroth 等）の対応順 | 初版は宣言順と仮定（§6.9 手順 4）。ゲーム内で 1 例確認できれば確定 |
| Q10 | 元 mod の無い `HandWraps` mod 10 件（`HandWrapsImplicit…` 3 件、`HandWrapsLocalIncreasedPhysicalDamageReductionRating7` 等）の扱い。implicit 系は Hand Wraps 化した手袋の implicit と思われる | 初版は `transforms_from NULL` で収録し、`HandWrapsImplicit…` は `sub_kind=handwraps` + `meta.is_implicit=true` |
| Q3 | ~~`SoulCoreStats` の実値~~ | **解決**: 砂漠のレッサールーンで確認。マーシャル武器「4から6の火ダメージを追加する」/ ワンドまたはスタッフ「ダメージの6%を追加火ダメージとして獲得する」/ 防具「火耐性 +10%」とゲーム内表示に一致。`Stats` 行番号配列と `StatsValues` は添字対応 |
| Q4 | ~~ユニークの item_class `Mace` の分解~~ | **解決**: PoB のベース名 → `base_items.item_class` で決まる（449 件中 437 件に PoB 定義あり）。残りは `slots.py` の別名表で `weapon` に寄せる |
| Q12 | ユニーク性能行の `ambiguous`（同じ英文の mod が複数）が 38%。石の拳・培養の付加情報は全候補一致時のみ出すため、一部の行で情報が出ない | 実装済みの回避策: 候補をアイテム部位で絞り、変化後 mod（`HandWraps*` / `UniqueMutatedVaal*`）を候補から除外。さらに絞るなら mod の `spawn_weights` とベースのタグ照合が要る |
| Q5 | `BrequelTree.json`（別サブツリー）や `weapon_set_points` を持つノード（武器セット専用パッシブ）の扱い | 初版は通常ツリー `Default.json` のみ。武器セットパッシブは通常ノードとして扱う |
| Q6 | mod の `domain='flask'` にチャーム mod が入っているか（`mods_by_base['Charms']` には prefix 4 / suffix 5 がある） | M1 で `applies_to` に `charm` が付く mod が 9 件以上あることを確認 |
| Q7 | 日本語 slot ラベルの実際の訳語（`ItemClasses.Name` JA） | M1 で S5 から取って `slots.py` の表を実訳に置換 |

---

## 12. 既知の落とし穴

DATA_PIPELINE.md §10 の全項目に加えて:

| 症状 | 原因 | 対処 |
|---|---|---|
| `憤怒` で 0 件 | FTS5 trigram は 3 文字未満に当たらない | 3 文字未満は LIKE に分岐（§5） |
| ユニークの slot が `Mace` / `Focii` | `uniques.json.item_class` は base_items と表記が違う | PoB ベース名 → `base_items.item_class` で決める（§6.1） |
| ルーンが `Rune` クラスで見つからない | 全部 `SoulCore` クラス。`tags` に `rune` / `rune_normal` 等 | `item_class=='SoulCore'` で取る（§6.5） |
| チャームが `Charm` クラスで見つからない | ベースは `UtilityFlask`（`tags` に `utility_flask`）。ユニークだけ `Charm` | `slots.py` で両方 `charm` に寄せる |
| アセンダンシーに PoE1 の Marauder 等が混ざる | `ascendancies.json` にレガシーが残る | `disabled` + 空ノード判定で除外（§6.3） |
| パッシブの本文に `xxx_no_display = 200` | 表示しない内部 stat | `_no_display` は本文から除外（§6.2） |
| `pathofexile-dat` の foreignrow が数値 | 行番号 | 参照先テーブルも export し行番号で引く（§3.2） |
| Windows Git Bash で Python にヒアドキュメント | `\1` `\n` が制御文字化 | ファイル編集は Edit/Write ツール。テストは `pytest` ファイルで |
| 前回と訳率が違う | `.csd` の取りこぼし or パッチで stat ID 変更 | ビルドログの訳率と `meta.source_counts` を前回と比較 |
| PoB ユニークをテキストとして読むと壊れる | `Uniques/*.json` は**文字列の JSON 配列**（1 要素 = 1 アイテムのブロック） | `json.load` してから各要素を §5.2 のブロックとして解析 |
| `SoulCoreStatCategories.TargetItemClasses` が空 | `All` / `Martial Weapon` / `Armour` はグループカテゴリ | `slots.py` の固定表で展開（§6.5） |
| `HandWraps` mod が `mods_by_base` に無い | `spawn_weights` が空（実体は元 mod 経由で付く） | `applies_to` は元 mod からコピー（§6.6） |
| 培養 mod の置換先が分からない | 元 mod → 置換先の対応表はデータに無い（プール抽選） | 対応を捏造しない。プール検索と置換対象印まで（§6.7） |
| Windows で Python の `print` に日本語を出すと化ける | 標準出力が cp932 | `PYTHONIOENCODING=utf-8` を設定。ログもファイルに UTF-8 で書く |
| `pathofexile-dat` の実行 | 初回はバンドル索引の取得で 1〜2 分。パッチ番号は `version.txt` の値をそのまま `config.json.patch` に | 今回 `4.5.5.2` で 8 テーブル × 2 言語が問題なく取れた |
| `pathofexile-dat` を再実行すると前回の出力が消える | `datexport/tables/` を毎回作り直す | **`config.json` には必要な全テーブルを常に書く**（分割実行しない） |
| `AlternatePassiveSkills` に PoE1 のタイムレス（Vaal / Eternal / Templar …）が混ざる | ゲームファイルにレガシーが残っている | `AlternateTreeVersions.ConquerorType in ('Kalguuran','Abyss')` で絞る（§6.9） |
| ジェムの `tags` に `nova` があるのに `active_skill.types` に無い | 表示用タグ（`skill_gems.tags`）と内部型（`skills.active_skill.types`）は別物 | UI のチップは `tags`、`type:` は補助（§6.8） |
| リネージュサポートが見分けられない | ベースアイテムの `tags` には印が無い（`support_gem, gem, default` だけ） | `skill_gems[].tags ∋ 'lineage'` で判定（85 件） |
| `[DNT] Arbiters Calling` のような開発用ジェム | `release_state=released` でも名前に `[DNT]` | 名前が `[DNT]` で始まるものは除外し件数をログ |

---

## 12-A. 画像（実装済み）

`https://image.ggpk.exposed/poe2/Art/<path>?format=png` が `.dds` を PNG にして返す。
`search_docs.icon` に `Art/` を除いた `.dds` パスを持ち、`fetch_images.py` が
`images/<path の / を _ に>.png` として落とす（**1,993 枚中 1,991 枚取得、60MB**）。

- UI は `images/…` を相対参照し、無ければ同じ画像の URL にフォールバックして、それも
  失敗したら枠ごと隠す。**オフラインで動き、`images/` が無くてもレイアウトは崩れない**
- 取得できない 2 枚（`2DArt/SkillIcons/passives/Storm Weaver.dds`,
  `2DItems/Armours/BodyArmours/Uniques/The Auspex.dds`）はサーバが 500 を返す。
  URL エンコードの問題ではなく、バンドルに実体が無い
- `web_data.json` では画像パスを共有配列 `icons` に持ち、各 doc は添字だけを持つ（`docs[12]`）

## 12-B. 表示の省略について

結果は**件数でも行数でも省略しない**（SPEC 初版にあった「各グループ最大 50 件」「本文 4 行」は撤回）。

- 効果行は常に全行出す
- 一覧は下端に近づくと 1,500 件ずつ描き足す。全 12,552 件を一度に描いても **276ms / HTML 5.7MB**
  なので、これは入力中の体感を保つためのもので、ユーザーから見た件数の上限は無い

## 13-A. ビルド実測（4.5.5.2、2026-09-17 実装完了時点）

`python build_db.py` のログと `pytest` で確認した値。次回パッチで下回ったら原因を調べる。

| 項目 | 値 |
|---|---|
| `.csd` | 589 ファイル / 15,845 ブロック / `no_description` 198 |
| テンプレート索引（系統 C） | 25,656 |
| trade2 対訳 | 3,750 |
| `search_docs` | **12,552**（mod 9,151 / gem 1,120 / notable 984 / unique 449 / ascendancy 425 / socketable 313 / timeless 77 / keystone 33） |
| mod | 9,636 行（訳 94.2%）、付く装備 8,949 組、石の拳リンク 588、培養の元 mod 242 |
| unique | 449（名前訳 98.4%、PoB 性能あり 437）、性能行 2,274（mod 対応 86.9%） |
| passive | 1,442（本文訳 93.7%、名前は `Id` で 100% 一致） |
| socketable | 313（訳 97.1%）、効果 490 |
| gem | 1,120（説明訳 94.7%、効果詳細 2,928 行中 79.4%） |
| timeless | 77（Kalguuran 40 / Abyss 36 / 追加 1） |
| ビルド時間 | 約 8 秒（`fetch_data.py` は初回 5〜10 分） |
| 出力 | `poe2db.sqlite` 約 40MB、`web_data.json` 8.54MB、`poe2db.html` **8.58MB**、`images/` 60MB |
| UI 検索速度 | 12,552 件の全走査で **2〜8ms**（実測） |

## 13. 参考: 実データで確認した数値（4.5.5.2）

- `base_items.json` 5,496 件。`SoulCore` 313 / `Jewel` 9 / `UtilityFlask`(チャーム) 13 / `LifeFlask` 9 / `ManaFlask` 9 / `Talisman` 31
- `uniques.json` 449 件。item_class 内訳: Body Armour 73, Helmet 53, Ring 43, Gloves 37, Shield 30, Amulet 26, Boots 25, Mace 24, Belt 21, Jewel 15, Charm 12, Bow 11, Sceptre 11, Spear 10, Staff 10, Focii 8, Quiver 8, Wand 8, Warstaff 7, Flask 6, Talisman 6, Crossbow 5
- `mods.json` 16,784 件。domain: item 8,151 / flask 181 / desecrated 415。generation_type: prefix 2,767 / suffix 2,458 / corrupted 126 / essence 117 / unique 10,447
- item ドメイン mod の `spawn_weights` タグ語彙（weight>0）: amulet, ring, wand, staff, sceptre, belt, gloves, quiver, body_armour, boots, helmet, shield, focus, bow, mace, axe, sword, spear, crossbow, flail, warstaff, dagger, claw, talisman, str_armour, dex_armour, int_armour, str_dex_armour, str_int_armour, dex_int_armour, str_dex_int_armour, one_hand_weapon, two_hand_weapon, weapon, armour …
- `passive_skill_trees/Default.json` 5,152 ノード。notable 1,305（うちアセンダンシー 321）、keystone 33
- `ascendancies.json` 37 件（PoE1 レガシー含む）。実在するもの: Titan / Warbringer / Smith of Kitava（Warrior）、Deadeye / Pathfinder（Ranger）、Infernalist / Blood Mage / Lich / Abyssal Lich（Witch）、Martial Artist / Invoker / Acolyte of Chayula（Monk）、Tactician / Witchhunter / Gemling Legionnaire（Mercenary）、Stormweaver / Chronomancer / Disciple of Varashta（Sorceress）、Amazon / Spirit Walker / Ritualist（Huntress）、Oracle / Shaman（Druid）。`[DNT-UNUSED]` を名前に含むものはレガシー（`disabled` が false のものもあるので**名前でも弾く**）
- `HandWraps*` mod 598 件（prefix 213 / suffix 198 / unique 187）、元 mod 存在 588。うち `HandWrapsUniqueMutatedVaal*` 28 件は培養 mod の Hand Wraps 版
- `UniqueMutatedVaal*` mod 204 件、`Incursion2MutatedUniqueModsClient.OriginalMods` 242 件
- PoB 手袋ユニーク 37 件、性能行 217 行中 192 行が mod ID に一致、177 行に Hand Wraps 版あり
- PoB 全ユニーク 435 件（26 カテゴリ）、現行 variant の性能行 2,311 行中 1,988 行（86%）が mod ID に一致
- `UniqueOrigins` 126 行（Ezomyte 64 / Vaal 48 / Kalguuran 14）。repoe の 441 ユニーク中 Vaal 40 / Ezomyte 60 / Kalguuran 14 / 起源なし 327。Vaal 40 件中 37 件が置換対象行あり
- 置換対象 mod（`OriginalMods` 242 件）を 1 行以上持つユニークは 196 件だが、そのうち Vaal は 37 件。`vaal_basetype` タグ持ちは 44 件で置換対象行とは無相関
- `SoulCoreStatCategories` 31 行。`SoulCore` の JA クラス名は「オーグメント」
- `skill_gems.json` 1,191 件（active 505 / support 642 / spirit 44）、うち `tags ∋ lineage` 85 件（全て support）。`skills.json` 8,349 件（14 MB）。`gem_tags.json` 67 件。`active_skill.types` の語彙 290 種
- `AlternateTreeVersions` 8 行（index 6 = Kalguuran, 7 = Abyss）。`AlternatePassiveSkills` 231 行中 Kalguuran 40（keystone 3 / notable 37）/ Abyss 36。`AlternatePassiveAdditions` 95 行中 Abyss 1。JA 名あり（例 `Scorched Earth` → `焦げた大地`、`Black Scythe Training` → `ブラックサイストレー二ング`）
- PoE2 のタイムレスジュエル: Heroic Tragedy（Kalguur。variant Vorana / Medved / Olroth）、Undying Hate（Abyssals。variant Amanamu / Kulemak / Kurgal / Tecrod / Ulaman）
- Python 3.11.1 同梱 SQLite 3.39.4、FTS5 trigram 利用可（確認済み）
