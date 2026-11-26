"""Parse 附表一／附表二 crop labels into a structured rule."""

from __future__ import annotations

import re

from .normalize import norm_label

NOTE5 = re.compile(r"^其他\((蔬果類|穀類|茶類)\)\*$")
EXCLUSION = re.compile(r"\(([^()]+)除外\)$")
FORM = re.compile(r"\((乾|乾燥|鮮)\)$")
INCLUDES = re.compile(r"\((含|包括|包含)([^()]+)\)$")

# 附表一用「米」「茶」當整類列名，附表五的類名是「米類」「茶類」。
CATEGORY_ALIASES = {
    "米類": ["米"],
    "茶類": ["茶"],
}

VEGETABLE_CATEGORIES = {
    "包葉菜類",
    "小葉菜類",
    "根莖菜類",
    "蕈菜類",
    "果菜類",
    "瓜菜類",
    "豆菜類",
    "芽菜類",
}
FRUIT_CATEGORIES = {
    "瓜果類",
    "大漿果類",
    "小漿果類",
    "核果類",
    "梨果類",
    "柑桔類",
}
GRAIN_CATEGORIES = {"米類", "麥類", "雜糧類", "乾豆類"}

# Group used by「其他莓」. Depth sits with other subgroups.
BERRY_GROUP = "莓"


def split_names(text: str) -> list[str]:
    text = text.replace("及", "、").replace("與", "、")
    parts = re.split(r"[、,，]", text)
    out = []
    for part in parts:
        name = part.strip()
        if name and name not in {"等", "等。"}:
            out.append(name)
    return out


def _depth(base: str, categories: set[str], subgroups: set[str]) -> int:
    if base == "食用花卉":
        return 3
    if (
        base in subgroups
        or f"{base}類" in subgroups
        or base in {BERRY_GROUP, "香辛植物", "草木本植物", "食用花卉"}
    ):
        return 2
    if base in categories or base in {"米", "茶"}:
        return 1
    return 1


def parse_label(raw: str, categories: set[str], subgroups: set[str]) -> dict:
    original = raw or ""
    text = norm_label(original)
    matched = NOTE5.match(text)
    if matched:
        return {
            "kind": "note5",
            "base": matched.group(1),
            "label_core": text,
            "exclusions": [],
            "form": None,
            "depth": 0,
            "label": text,
        }

    exclusions: list[str] = []
    while True:
        found = EXCLUSION.search(text)
        if not found:
            break
        exclusions = split_names(found.group(1)) + exclusions
        text = text[: found.start()]

    form = None
    form_hit = FORM.search(text)
    if form_hit:
        form = {"乾": "dried", "乾燥": "dried", "鮮": "fresh"}[form_hit.group(1)]
        base = text[: form_hit.start()]
        label_core = text
    else:
        base = text
        label_core = text

    include_hit = INCLUDES.search(base)
    if include_hit:
        base = base[: include_hit.start()]
        label_core = base

    alias_targets = {alias for names in CATEGORY_ALIASES.values() for alias in names}

    if base == "十字花科蔬菜(包葉菜類)":
        kind, base, depth = "subgroup", "十字花科包葉菜類", 2
    elif base == "十字花科蔬菜(小葉菜類)":
        kind, base, depth = "subgroup", "十字花科小葉菜類", 2
    elif base in {"蔬菜", "水果"} and form == "dried":
        kind, depth = "broad", 0
    elif base == "香辛植物":
        kind, depth = "subgroup", 2
    elif base.startswith("其他"):
        rest = base[2:]
        kind = "other_group"
        base = rest
        depth = _depth(base, categories, subgroups)
    elif base in categories or base in alias_targets:
        kind, depth = "category", 1
    elif base in subgroups:
        kind, depth = "subgroup", 3 if base == "食用花卉" else 2
    elif f"{base}類" in subgroups and base not in categories:
        # 附表五寫「十字花科包葉菜」，附表一常加「類」。
        base = f"{base}類"
        kind, depth = "subgroup", 2
    else:
        kind, depth = "specific", 9

    return {
        "kind": kind,
        "base": base,
        "label_core": label_core,
        "exclusions": exclusions,
        "form": form,
        "depth": depth,
        "label": norm_label(original),
    }


def note5_base(category: str, form: str) -> str:
    """Map a 附表五 category + 市售型態 to 註五的三種定量極限。

    MOHWP0055.05：I 類新鮮蔬果與新鮮香辛／草本 → 其他(蔬果類)*；
    II 類穀類及乾豆類 → 其他(穀類)*；
    III 類茶與乾燥蔬果、乾燥香辛／草本 → 其他(茶類)*。
    第2條不另乘濃縮係數；型態只影響註五這條兜底。
    """
    if category in GRAIN_CATEGORIES:
        return "穀類"
    if category == "茶類":
        return "茶類"
    if category == "香辛植物及其他草木本植物":
        return "茶類" if form == "dried" else "蔬果類"
    if form == "dried":
        return "茶類"
    return "蔬果類"


def broad_groups(category: str) -> list[str]:
    groups = []
    if category in VEGETABLE_CATEGORIES:
        groups.append("蔬菜")
    if category in FRUIT_CATEGORIES:
        groups.append("水果")
    return groups
