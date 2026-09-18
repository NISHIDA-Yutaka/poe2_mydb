"""poe2db: 入力フォーマットのパーサと正規化（docs/DATA_PIPELINE.md §5, §6）.

ここには「読む」処理だけを置く。どのファイルを読むか・どう結合するかは build_db.py。
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

# ---------------------------------------------------------------- マークアップ

_LINK_NAMED = re.compile(r"\[([^\[\]|]+)\|([^\[\]]+)\]")
_LINK_PLAIN = re.compile(r"\[([^\[\]|]+)\]")


def strip_markup(text: str) -> str:
    """`[Resistances|Cold Resistance]` → `Cold Resistance`、`[Fire]` → `Fire`."""
    if not text or "[" not in text:
        return text or ""
    prev = None
    out = text
    while prev != out:  # 入れ子があり得る
        prev = out
        out = _LINK_NAMED.sub(r"\2", out)
        out = _LINK_PLAIN.sub(r"\1", out)
    return out


# ------------------------------------------------------------------- 正規化

_NUM = re.compile(r"[+-]?\d+(?:\.\d+)?")
_RANGE = re.compile(r"\(\s*[+-]?\d+(?:\.\d+)?\s*-\s*[+-]?\d+(?:\.\d+)?\s*\)")
_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    """英文照合用（DATA_PIPELINE §6.2）: 数値を `#` に潰した比較キー."""
    s = strip_markup(text or "")
    s = _RANGE.sub("#", s)
    s = _NUM.sub("#", s)
    s = s.replace("+#", "#")
    s = _WS.sub(" ", s).strip().lower()
    return s


def normalize_template(template: str) -> str:
    """`.csd` テンプレート（`{0}` 入り）を normalize と同じキーに落とす."""
    s = re.sub(r"\{\d*(?::[^}]*)?\}", "#", template or "")
    return normalize(s)


def normalize_for_search(text: str) -> str:
    """検索用（SPEC §4.1）: マークアップ除去 → NFKC → 小文字 → 空白圧縮."""
    s = strip_markup(text or "")
    s = unicodedata.normalize("NFKC", s)
    s = _WS.sub(" ", s).strip().lower()
    return s


# ------------------------------------------------------------ .csd ハンドラ


def _fmt(value: float, dp: int | None = None, *, if_required: bool = False) -> str:
    """数値を表示文字列にする。`if_required` は小数が不要なら整数表示."""
    if dp is None:
        if float(value).is_integer():
            return str(int(round(value)))
        return f"{value:g}"
    if if_required and float(round(value, dp)).is_integer():
        return str(int(round(value)))
    return f"{value:.{dp}f}"


def _div(n: float, dp: int | None = None, *, if_required: bool = False):
    def handler(v: float) -> str:
        return _fmt(v / n, dp, if_required=if_required)
    return handler


def _mul(n: float, dp: int | None = None, *, if_required: bool = False):
    def handler(v: float) -> str:
        return _fmt(v * n, dp, if_required=if_required)
    return handler


HANDLERS: dict[str, object] = {
    # 割る
    "divide_by_two_0dp": _div(2, 0),
    "divide_by_three": _div(3),
    "divide_by_four": _div(4),
    "divide_by_five": _div(5),
    "divide_by_six": _div(6),
    "divide_by_ten_0dp": _div(10, 0),
    "divide_by_ten_1dp": _div(10, 1),
    "divide_by_ten_1dp_if_required": _div(10, 1, if_required=True),
    "divide_by_ten_2dp": _div(10, 2),
    "divide_by_ten_2dp_if_required": _div(10, 2, if_required=True),
    "divide_by_twelve": _div(12),
    "divide_by_fifteen_0dp": _div(15, 0),
    "divide_by_twenty_then_double_0dp": lambda v: _fmt(v / 20 * 2, 0),
    "divide_by_fifty": _div(50),
    "divide_by_one_hundred": _div(100),
    "divide_by_one_hundred_0dp": _div(100, 0),
    "divide_by_one_hundred_1dp": _div(100, 1),
    "divide_by_one_hundred_2dp": _div(100, 2),
    "divide_by_one_hundred_2dp_if_required": _div(100, 2, if_required=True),
    "divide_by_one_thousand": _div(1000),
    "divide_by_one_thousand_1dp": _div(1000, 1),
    "divide_by_one_thousand_2dp": _div(1000, 2),
    # 時間
    "milliseconds_to_seconds": _div(1000),
    "milliseconds_to_seconds_0dp": _div(1000, 0),
    "milliseconds_to_seconds_1dp": _div(1000, 1),
    "milliseconds_to_seconds_2dp": _div(1000, 2),
    "milliseconds_to_seconds_2dp_if_required": _div(1000, 2, if_required=True),
    "deciseconds_to_seconds": _div(10),
    "per_minute_to_per_second": _div(60),
    "per_minute_to_per_second_0dp": _div(60, 0),
    "per_minute_to_per_second_1dp": _div(60, 1),
    "per_minute_to_per_second_2dp": _div(60, 2),
    "per_minute_to_per_second_2dp_if_required": _div(60, 2, if_required=True),
    # 符号・倍率
    "negate": lambda v: _fmt(-v),
    "negate_and_double": lambda v: _fmt(-v * 2),
    "double": _mul(2),
    "times_twenty": _mul(20),
    "times_one_point_five": _mul(1.5),
    "30%_of_value": _mul(0.3),
    "60%_of_value": _mul(0.6),
    "multiplicative_damage_modifier": lambda v: _fmt(v + 100),
    "multiplicative_permyriad_damage_modifier": lambda v: _fmt(v / 100 + 100),
    "old_leech_percent": _div(100),
    "old_leech_permyriad": _div(10000),
    "divide_by_one_hundred_and_negate": lambda v: _fmt(-v / 100),
    "negate_and_divide_by_one_hundred": lambda v: _fmt(-v / 100),
    "milliseconds_to_seconds_halved": lambda v: _fmt(v / 2000),
    "metre_to_millimetre": _mul(1000),
    "millimetres_to_metres": _div(1000),
    "millimetres_to_metres_1dp": _div(1000, 1),
    "millimetres_to_metres_2dp": _div(1000, 2),
    "millimetres_to_metres_1dp_if_required": _div(1000, 1, if_required=True),
    "centimetres_to_metres": _div(100),
    "add_one": lambda v: _fmt(v + 1),
    "subtract_one": lambda v: _fmt(v - 1),
    "passive_keystone_index": lambda v: _fmt(v),
    "mages_legacy_index": lambda v: _fmt(v),
    "display_indexable_support": lambda v: _fmt(v),
    "mod_value_to_item_class": lambda v: _fmt(v),
    "canonical_line": lambda v: _fmt(v),
    "canonical_stat": lambda v: _fmt(v),
}

UNKNOWN_HANDLERS: set[str] = set()


def apply_handler(name: str, value: float) -> str:
    fn = HANDLERS.get(name)
    if fn is None:
        UNKNOWN_HANDLERS.add(name)
        return _fmt(value)
    return fn(value)  # type: ignore[operator]


# ------------------------------------------------------------- .csd パーサ

_VARIANT_LINE = re.compile(r'^(?P<pre>[^"]*)"(?P<tpl>(?:[^"\\]|\\.)*)"(?P<post>.*)$')


def _unescape(s: str) -> str:
    return s.replace("\\n", "\n").replace('\\"', '"').replace("\\t", "\t").replace("\\\\", "\\")


class Variant:
    """`.csd` の 1 バリアント行."""

    __slots__ = ("conditions", "template", "handlers")

    def __init__(self, conditions: list[str], template: str,
                 handlers: dict[int, list[str]]) -> None:
        self.conditions = conditions
        self.template = template
        self.handlers = handlers

    def __repr__(self) -> str:  # pragma: no cover - デバッグ用
        return f"Variant({self.conditions!r}, {self.template!r})"


def _parse_handlers(post: str) -> dict[int, list[str]]:
    """`divide_by_ten_1dp_if_required 1 negate 2` → {0: [...], 1: [...]}（0 始まりに直す）."""
    out: dict[int, list[str]] = {}
    tokens = post.split()
    i = 0
    while i < len(tokens):
        name = tokens[i]
        i += 1
        if name == "reminderstring":  # 引数は文字列 1 つ。読み飛ばす
            i += 1
            continue
        idxs: list[int] = []
        while i < len(tokens) and re.fullmatch(r"-?\d+", tokens[i]):
            idxs.append(int(tokens[i]))
            i += 1
        for idx in idxs:
            out.setdefault(idx - 1, []).append(name)
    return out


def _parse_variant(line: str, n_ids: int) -> Variant | None:
    m = _VARIANT_LINE.match(line.strip())
    if not m:
        return None
    conds = m.group("pre").split()
    if len(conds) < n_ids:  # 条件が省略されている（実データでは起きないが保険）
        conds += ["#"] * (n_ids - len(conds))
    return Variant(conds[:n_ids], _unescape(m.group("tpl")),
                   _parse_handlers(m.group("post")))


def parse_csd(path: str | Path) -> tuple[dict[tuple[str, ...], dict[str, list[Variant]]],
                                         list[str], set[str]]:
    """`.csd` を読む。

    戻り値: ({stat ID の組: {言語名: [Variant, ...]}},
             include しているファイル名, `no_description` 指定の stat ID)
    """
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    lines = text.replace("\r\n", "\n").split("\n")
    blocks: dict[tuple[str, ...], dict[str, list[Variant]]] = {}
    includes: list[str] = []
    no_desc: set[str] = set()

    i = 0
    total = len(lines)
    while i < total:
        raw = lines[i]
        line = raw.strip()
        i += 1
        if line.startswith("include "):
            m = re.search(r'"([^"]+)"', line)
            if m:
                includes.append(m.group(1))
            continue
        if line.startswith("no_description "):
            no_desc.update(line.split()[1:])
            continue
        if line != "description":
            continue
        # ID 行
        while i < total and not lines[i].strip():
            i += 1
        if i >= total:
            break
        head = lines[i].strip().split()
        i += 1
        if not head or not head[0].isdigit():
            continue
        n_ids = int(head[0])
        ids = tuple(head[1:1 + n_ids])
        if len(ids) != n_ids:
            continue
        langs: dict[str, list[Variant]] = {}
        lang = "English"
        # 言語ごとに <件数> + 件数分のバリアント行
        while i < total:
            cur = lines[i].strip()
            if cur.startswith('lang "'):
                lang = cur.split('"')[1]
                i += 1
                continue
            if not cur:
                i += 1
                if i < total and lines[i].strip() in ("description", "") :
                    break
                continue
            if not cur.split()[0].isdigit() or '"' in cur.split()[0]:
                break
            count = int(cur.split()[0])
            i += 1
            variants: list[Variant] = []
            for _ in range(count):
                if i >= total:
                    break
                v = _parse_variant(lines[i], n_ids)
                i += 1
                if v is not None:
                    variants.append(v)
            if variants:
                langs.setdefault(lang, []).extend(variants)
            # 次が lang でも description でもなければブロック終わり
            nxt = lines[i].strip() if i < total else ""
            if not nxt.startswith('lang "'):
                break
        if ids and langs:
            existing = blocks.setdefault(ids, {})
            for k, v in langs.items():
                existing.setdefault(k, v)
    return blocks, includes, no_desc


# ------------------------------------------------------- .csd バリアント選択

def _match_condition(cond: str, value: float) -> bool:
    """`#` / `10` / `1|#` / `#|-1` / `-10|10` を生の値で判定する."""
    cond = cond.strip()
    if not cond or cond == "#":
        return True
    if cond.startswith("!"):
        return not _match_condition(cond[1:], value)
    if "|" in cond:
        lo, hi = cond.split("|", 1)
        if lo not in ("", "#") and value < float(lo):
            return False
        if hi not in ("", "#") and value > float(hi):
            return False
        return True
    try:
        return float(cond) == float(value)
    except ValueError:
        return True


def pick_variant(variants: list[Variant], values: list[float]) -> Variant | None:
    """条件に合う最初のバリアントを返す（生の値で判定。DATA_PIPELINE §5.1）."""
    for v in variants:
        if all(_match_condition(c, values[n] if n < len(values) else 0)
               for n, c in enumerate(v.conditions)):
            return v
    return None


_PLACEHOLDER = re.compile(r"\{(\d*)(?::([^}]*))?\}")


def fill(variant: Variant, values: list[float]) -> str:
    """ハンドラを適用して `{n}` を埋める."""
    shown: list[str] = []
    for n, v in enumerate(values):
        s = None
        for name in variant.handlers.get(n, []):
            s = apply_handler(name, float(v) if s is None else float(s))
        shown.append(s if s is not None else _fmt(v))

    counter = {"n": 0}

    def sub(m: re.Match[str]) -> str:
        if m.group(1):
            idx = int(m.group(1))
        else:
            idx = counter["n"]
            counter["n"] += 1
        text = shown[idx] if idx < len(shown) else "?"
        spec = m.group(2) or ""
        if "+" in spec and not text.startswith("-"):
            text = "+" + text
        return text

    return _PLACEHOLDER.sub(sub, variant.template)


def fill_range(variant: Variant, mins: list[float], maxs: list[float]) -> str:
    """min/max が違うときに `(a-b)` 形式で埋める（mod・タイムレス用）."""
    parts: list[str] = []
    for n, (lo, hi) in enumerate(zip(mins, maxs)):
        lo_s = hi_s = None
        for name in variant.handlers.get(n, []):
            lo_s = apply_handler(name, float(lo) if lo_s is None else float(lo_s))
            hi_s = apply_handler(name, float(hi) if hi_s is None else float(hi_s))
        lo_s = lo_s if lo_s is not None else _fmt(lo)
        hi_s = hi_s if hi_s is not None else _fmt(hi)
        # negate 系は大小が入れ替わる
        try:
            if float(lo_s) > float(hi_s):
                lo_s, hi_s = hi_s, lo_s
        except ValueError:
            pass
        parts.append(lo_s if lo_s == hi_s else f"({lo_s}-{hi_s})")

    counter = {"n": 0}

    def sub(m: re.Match[str]) -> str:
        if m.group(1):
            idx = int(m.group(1))
        else:
            idx = counter["n"]
            counter["n"] += 1
        text = parts[idx] if idx < len(parts) else "?"
        spec = m.group(2) or ""
        if "+" in spec and not text.startswith(("-", "(")):
            text = "+" + text
        elif "+" in spec and text.startswith("("):
            text = "+" + text
        return text

    return _PLACEHOLDER.sub(sub, variant.template)


# ------------------------------------------------- Path of Building ユニーク

_META_PREFIX = re.compile(
    r"^(Variant|Implicits|Requires|Req|LevelReq|League|Source|Upgrade|Selected Variant|"
    r"Has Alt Variant|Limited to|Radius|Rarity|Unique|Prefix|Suffix|Item Level|Quality|"
    r"Armour|Evasion|Energy Shield|Ward|Sockets|Crit|Attacks per Second|Range|"
    r"Physical Damage|Elemental Damage|Chaos Damage|Cold Damage|Fire Damage|"
    r"Lightning Damage|Weapon Range|Block|Spirit|Charm Slots|Base Chance|"
    r"Implicit|Shaper Item|Elder Item|Crusader Item|Redeemer Item|Hunter Item|"
    r"Warlord Item|Synthesised Item|Fractured Item|Mirrored|Corrupted|Historic)\b[: ]",
)
_TAG_PREFIX = re.compile(r"^(\{[^}]*\})+")
_VARIANT_TAG = re.compile(r"\{variant:([\d,]+)\}")


def _strip_tags(line: str) -> str:
    return _TAG_PREFIX.sub("", line).strip()


def parse_pob_uniques(blocks: list[str]) -> list[dict]:
    """PoB の 1 アイテム 1 ブロックを解析する（DATA_PIPELINE §5.2）.

    現行 variant（最後に宣言された `Variant:`）の行だけを残す。
    """
    items: list[dict] = []
    for block in blocks:
        lines = [l.rstrip() for l in block.split("\n") if l.strip()]
        if len(lines) < 2:
            continue
        name = lines[0].strip()
        n_variants = sum(1 for l in lines if l.startswith("Variant:"))
        current = str(n_variants) if n_variants else None

        def in_current(line: str) -> bool:
            m = _VARIANT_TAG.search(line)
            if not m or current is None:
                return True
            return current in m.group(1).split(",")

        # ベース行: 名前の次で、variant 条件を満たす最初の非メタ行
        base = ""
        body_start = 1
        for idx in range(1, len(lines)):
            cand = lines[idx]
            if cand.startswith("Variant:"):
                continue
            if not in_current(cand):
                continue
            stripped = _strip_tags(cand)
            if not stripped or _META_PREFIX.match(stripped):
                continue
            base = stripped
            body_start = idx + 1
            break

        implicit_count = 0
        for l in lines:
            m = re.match(r"^Implicits:\s*(\d+)", l)
            if m:
                implicit_count = int(m.group(1))
                break

        implicits: list[str] = []
        stats: list[str] = []
        seen_implicits = 0
        after_implicit_marker = False
        for line in lines[body_start:]:
            if re.match(r"^Implicits:\s*\d+", line):
                after_implicit_marker = True
                continue
            if line.startswith("Variant:"):
                continue
            if not in_current(line):
                continue
            text = _strip_tags(line)
            if not text or _META_PREFIX.match(text):
                continue
            if after_implicit_marker and seen_implicits < implicit_count:
                implicits.append(text)
                seen_implicits += 1
            else:
                stats.append(text)

        items.append({
            "name": name,
            "base_item": base,
            "implicits": implicits,
            "stats": stats,
            "variants": [l.split(":", 1)[1].strip() for l in lines if l.startswith("Variant:")],
        })
    return items


# ------------------------------------------------------------- クエリ構文

FILTER_KEYS = {"kind", "slot", "sub", "asc", "hw", "cult", "origin", "tag",
               "gem", "type", "color", "jewel", "src"}


class Query:
    """検索クエリ（SPEC §7.1）."""

    def __init__(self) -> None:
        self.terms: list[str] = []
        self.excludes: list[str] = []
        self.phrases: list[str] = []
        self.filters: dict[str, list[str]] = {}

    def __repr__(self) -> str:  # pragma: no cover - デバッグ用
        return (f"Query(terms={self.terms}, excludes={self.excludes}, "
                f"phrases={self.phrases}, filters={self.filters})")

    @property
    def is_empty(self) -> bool:
        return not (self.terms or self.excludes or self.phrases or self.filters)


_TOKEN = re.compile(r'-?(?:\w+:)?"[^"]*"|\S+')


def parse_query(text: str) -> Query:
    q = Query()
    for token in _TOKEN.findall(text or ""):
        negate = token.startswith("-")
        if negate:
            token = token[1:]
        m = re.match(r'^(\w+):(.*)$', token)
        if m and m.group(1).lower() in FILTER_KEYS:
            key = m.group(1).lower()
            value = m.group(2).strip('"')
            q.filters.setdefault(key, []).extend(
                normalize_for_search(v) for v in value.split(",") if v.strip())
            continue
        if token.startswith('"') and token.endswith('"') and len(token) >= 2:
            value = normalize_for_search(token[1:-1])
            if value:
                (q.excludes if negate else q.phrases).append(value)
            continue
        value = normalize_for_search(token)
        if value:
            (q.excludes if negate else q.terms).append(value)
    return q


def match_haystack(q: Query, haystack: str) -> bool:
    """テキスト条件だけを見る（フィルタは呼び出し側）."""
    for t in q.terms:
        if t not in haystack:
            return False
    for p in q.phrases:
        if p not in haystack:
            return False
    for e in q.excludes:
        if e in haystack:
            return False
    return True
