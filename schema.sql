-- poe2db: SQLite スキーマ（docs/SPEC.md §5）

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);

-- 参照
CREATE TABLE item_classes (
    id TEXT PRIMARY KEY, name_en TEXT, name_ja TEXT, category TEXT, slot TEXT);

CREATE TABLE base_items (
    id TEXT PRIMARY KEY, name_en TEXT, name_ja TEXT, item_class TEXT, slot TEXT,
    tags TEXT, drop_level INT, release_state TEXT);

CREATE TABLE gem_tags (id TEXT PRIMARY KEY, name_en TEXT, name_ja TEXT);

-- kind: unique
CREATE TABLE uniques (
    id TEXT PRIMARY KEY, name_en TEXT, name_ja TEXT, item_class TEXT, slot TEXT,
    base_item_en TEXT, base_item_ja TEXT, base_item_id TEXT, has_stats INT,
    implicits_json TEXT, stats_json TEXT, is_alternate_art INT,
    origin TEXT, is_vaal_unique INT, cultivation_target INT);

CREATE TABLE unique_lines (
    unique_id TEXT, line_no INT, is_implicit INT, text_en TEXT, text_ja TEXT,
    mod_id TEXT, match_kind TEXT, PRIMARY KEY (unique_id, line_no));

-- kind: notable / ascendancy
CREATE TABLE passives (
    hash INT PRIMARY KEY, node_id TEXT, name_en TEXT, name_ja TEXT,
    is_notable INT, is_keystone INT, is_start INT, is_small INT,
    ascendancy_id TEXT, stats_json TEXT, lines_json TEXT,
    flavour_en TEXT, flavour_ja TEXT, icon TEXT);

CREATE TABLE ascendancies (
    id TEXT PRIMARY KEY, name_en TEXT, name_ja TEXT,
    class_en TEXT, class_ja TEXT, disabled INT);

-- kind: mod
CREATE TABLE mods (
    id TEXT PRIMARY KEY, name TEXT, text_en TEXT, text_ja TEXT, ja_source TEXT,
    generation_type TEXT, domain TEXT, required_level INT, mod_group TEXT,
    tags TEXT, stat_ids TEXT, stats_json TEXT, is_essence_only INT, spawn_tags TEXT,
    sub_kind TEXT, transforms_from TEXT, handwraps_id TEXT, cultivation_replaceable INT);

CREATE TABLE mod_applies_to (
    mod_id TEXT, item_class TEXT, slot TEXT, tagset TEXT, required_level INT,
    PRIMARY KEY (mod_id, item_class, tagset));

-- kind: socketable
CREATE TABLE socketables (
    base_id TEXT PRIMARY KEY, name_en TEXT, name_ja TEXT, type_en TEXT, type_ja TEXT,
    tier TEXT, required_level INT, limit_en TEXT, limit_ja TEXT,
    description_en TEXT, description_ja TEXT, flags_json TEXT, tags TEXT);

CREATE TABLE socketable_effects (
    base_id TEXT, category_id TEXT, category_en TEXT, category_ja TEXT,
    target_classes TEXT, target_slots TEXT, lines_json TEXT,
    PRIMARY KEY (base_id, category_id));

-- kind: gem
CREATE TABLE gems (
    id TEXT PRIMARY KEY, name_en TEXT, name_ja TEXT, gem_type TEXT, is_lineage INT,
    color TEXT, tags TEXT, skill_types TEXT, req_str INT, req_dex INT, req_int INT,
    level_req INT, cast_time INT, skill_id TEXT,
    summary_en TEXT, summary_ja TEXT, desc_en TEXT, desc_ja TEXT,
    detail_json TEXT, recommended_supports TEXT, weapon_restrictions TEXT);

-- kind: timeless
CREATE TABLE timeless_passives (
    id TEXT PRIMARY KEY, jewel TEXT, name_en TEXT, name_ja TEXT, passive_type TEXT,
    conqueror_index INT, spawn_weight INT, stats_json TEXT, lines_json TEXT,
    flavour_en TEXT, flavour_ja TEXT, icon TEXT, jewel_name TEXT);

-- kind: keyword（ゲーム内の用語解説）
CREATE TABLE keywords (
    id TEXT PRIMARY KEY, term_en TEXT, term_ja TEXT,
    definition_en TEXT, definition_ja TEXT);

-- 検索層
CREATE TABLE search_docs (
    id TEXT PRIMARY KEY, kind TEXT, sub_kind TEXT, slots TEXT,
    name_en TEXT, name_ja TEXT, group_en TEXT, group_ja TEXT,
    lines_json TEXT, meta_json TEXT, haystack TEXT, sort_key INT,
    icon TEXT);   -- `Art/` を除いた .dds パス。images/ の PNG 名は fetch_images.local_name

CREATE INDEX idx_search_kind ON search_docs(kind);
CREATE VIRTUAL TABLE search_fts USING fts5(id UNINDEXED, haystack, tokenize='trigram');
