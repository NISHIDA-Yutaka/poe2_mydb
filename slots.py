"""poe2db: item_class → slot（装備部位）の対応（docs/SPEC.md §4.2）.

データから自動導出せずコードで固定する。日本語ラベルは GGPK `ItemClasses.Name`（JA）で
上書きされる（build_db.py が `slot_labels()` に渡す）。
"""
from __future__ import annotations

# 親 slot
ARMOUR = "armour"
WEAPON = "weapon"
JEWELLERY = "jewellery"

# item_class（repoe / GGPK の英語 ID）→ slot ID
CLASS_TO_SLOT: dict[str, str] = {
    # 防具
    "Helmet": "helmet",
    "Body Armour": "body_armour",
    "Gloves": "gloves",
    "Boots": "boots",
    "Shield": "shield",
    "Buckler": "shield",
    "Focus": "focus",
    "Quiver": "quiver",
    # 装飾品
    "Amulet": "amulet",
    "Ring": "ring",
    "Belt": "belt",
    # その他装備
    "Jewel": "jewel",
    "AbyssJewel": "jewel",
    "UtilityFlask": "charm",
    "LifeFlask": "flask",
    "ManaFlask": "flask",
    "Talisman": "talisman",
    "SoulCore": "socketable",
    # 武器
    "One Hand Mace": "one_hand_mace",
    "Two Hand Mace": "two_hand_mace",
    "One Hand Sword": "one_hand_sword",
    "Two Hand Sword": "two_hand_sword",
    "One Hand Axe": "one_hand_axe",
    "Two Hand Axe": "two_hand_axe",
    "Thrown One Hand Axe": "one_hand_axe",
    "Thrown Two Hand Axe": "two_hand_axe",
    "Thrown Shield": "shield",
    "Claw": "claw",
    "Dagger": "dagger",
    "Flail": "flail",
    "Spear": "spear",
    "Bow": "bow",
    "Crossbow": "crossbow",
    "Sceptre": "sceptre",
    "Wand": "wand",
    "Staff": "staff",
    "Warstaff": "warstaff",
    "FishingRod": "fishing_rod",
}

# 武器 slot（親 `weapon` を持つ）
WEAPON_SLOTS = {
    "one_hand_mace", "two_hand_mace", "one_hand_sword", "two_hand_sword",
    "one_hand_axe", "two_hand_axe", "claw", "dagger", "flail", "spear",
    "bow", "crossbow", "sceptre", "wand", "staff", "warstaff", "fishing_rod",
}
# 近接・遠隔などの「マーシャル武器」（キャスター武器 = wand/staff/sceptre を除く）
MARTIAL_WEAPON_SLOTS = WEAPON_SLOTS - {"wand", "staff", "sceptre"}
# 防具 slot（親 `armour` を持つ）
ARMOUR_SLOTS = {"helmet", "body_armour", "gloves", "boots", "shield", "focus"}
# 装飾品 slot（親 `jewellery` を持つ）
JEWELLERY_SLOTS = {"amulet", "ring", "belt"}

# 既定の日本語ラベル（GGPK に無い / 親 slot 用）
DEFAULT_LABELS: dict[str, tuple[str, str]] = {
    "helmet": ("Helmet", "兜"),
    "body_armour": ("Body Armour", "鎧"),
    "gloves": ("Gloves", "手袋"),
    "boots": ("Boots", "靴"),
    "shield": ("Shield", "盾"),
    "focus": ("Focus", "フォーカス"),
    "quiver": ("Quiver", "矢筒"),
    "amulet": ("Amulet", "アミュレット"),
    "ring": ("Ring", "指輪"),
    "belt": ("Belt", "ベルト"),
    "jewel": ("Jewel", "ジュエル"),
    "charm": ("Charm", "チャーム"),
    "flask": ("Flask", "フラスコ"),
    "talisman": ("Talisman", "タリスマン"),
    "socketable": ("Augment", "オーグメント"),
    "one_hand_mace": ("One Hand Mace", "片手メイス"),
    "two_hand_mace": ("Two Hand Mace", "両手メイス"),
    "one_hand_sword": ("One Hand Sword", "片手剣"),
    "two_hand_sword": ("Two Hand Sword", "両手剣"),
    "one_hand_axe": ("One Hand Axe", "片手斧"),
    "two_hand_axe": ("Two Hand Axe", "両手斧"),
    "claw": ("Claw", "クロー"),
    "dagger": ("Dagger", "短剣"),
    "flail": ("Flail", "フレイル"),
    "spear": ("Spear", "スピア"),
    "bow": ("Bow", "弓"),
    "crossbow": ("Crossbow", "クロスボウ"),
    "sceptre": ("Sceptre", "セプター"),
    "wand": ("Wand", "ワンド"),
    "staff": ("Staff", "スタッフ"),
    "warstaff": ("Quarterstaff", "クォータースタッフ"),
    "fishing_rod": ("Fishing Rod", "釣り竿"),
    WEAPON: ("Weapon", "武器"),
    ARMOUR: ("Armour", "防具"),
    JEWELLERY: ("Jewellery", "装飾品"),
}

# ユニークの item_class は base_items と表記が違う（SPEC §4.2）
UNIQUE_CLASS_ALIAS: dict[str, str] = {
    "Mace": WEAPON,      # 片手/両手の判別はベース名から
    "Focii": "focus",
    "Flask": "flask",
    "Charm": "charm",
    "Sword": WEAPON,
    "Axe": WEAPON,
    "Quarterstaff": "warstaff",
}

# SoulCoreStatCategories の TargetItemClasses が空のカテゴリ（SPEC §6.5）
CATEGORY_SLOTS: dict[str, set[str]] = {
    "All": set(DEFAULT_LABELS) - {WEAPON, ARMOUR, JEWELLERY, "socketable"},
    "Armour": set(ARMOUR_SLOTS),
    "Martial Weapon": set(MARTIAL_WEAPON_SLOTS),
    "Martial Or Caster Weapon": set(WEAPON_SLOTS),
    "Martial Weapon Wand or Staff": MARTIAL_WEAPON_SLOTS | {"wand", "staff"},
}

# 表示順（UI のチップ順）
SLOT_ORDER = [
    "helmet", "body_armour", "gloves", "boots", "shield", "focus", "quiver",
    "amulet", "ring", "belt", "jewel", "charm", "flask", "talisman",
    "one_hand_mace", "two_hand_mace", "one_hand_sword", "two_hand_sword",
    "one_hand_axe", "two_hand_axe", "claw", "dagger", "flail", "spear",
    "bow", "crossbow", "sceptre", "wand", "staff", "warstaff", "fishing_rod",
    "socketable", ARMOUR, JEWELLERY, WEAPON,
]


def slot_for_class(item_class: str | None) -> str | None:
    """item_class から slot ID を引く（不明なら None）."""
    if not item_class:
        return None
    return CLASS_TO_SLOT.get(item_class) or UNIQUE_CLASS_ALIAS.get(item_class)


def with_parents(slots) -> list[str]:
    """子 slot に親 slot（weapon / armour / jewellery）を足して重複を除く."""
    out: set[str] = set()
    for s in slots:
        if not s:
            continue
        out.add(s)
        if s in WEAPON_SLOTS:
            out.add(WEAPON)
        if s in ARMOUR_SLOTS:
            out.add(ARMOUR)
        if s in JEWELLERY_SLOTS:
            out.add(JEWELLERY)
    order = {s: n for n, s in enumerate(SLOT_ORDER)}
    return sorted(out, key=lambda s: order.get(s, 999))


def slots_for_category(category_id: str, target_classes: list[str]) -> list[str]:
    """SoulCoreStatCategories 1 行から装着先 slot を決める."""
    if target_classes:
        return with_parents(slot_for_class(c) for c in target_classes)
    return with_parents(CATEGORY_SLOTS.get(category_id, set()))
