"""Parse 附表五 into searchable crops. Lists end with「等」and are not closed."""

from __future__ import annotations

import re

from .labels import BERRY_GROUP, broad_groups
from .normalize import norm_zh_key

# 俗稱 → 附表五作物。多個目標代表不可自動選。
COMMON_ALIASES: dict[str, list[str]] = {
    "洛神花": ["洛神葵"],
    "洛神": ["洛神葵"],
    "地瓜": ["甘藷"],
    "番薯": ["甘藷"],
    "黃瓜": ["胡瓜"],
    "大黃瓜": ["胡瓜"],
    "小瓜": ["小黃瓜"],
    "枸杞子": ["枸杞"],
    "稻米": ["水稻", "旱稻"],
    "白米": ["水稻", "旱稻"],
    "糙米": ["水稻", "旱稻"],
    "米": ["水稻", "旱稻"],
    "高麗菜": ["甘藍"],
    "大白菜": ["結球白菜"],
    "白菜": ["結球白菜", "小白菜"],
    "空心菜": ["蕹菜"],
    "地瓜葉": ["葉用甘藷"],
    "番瓜": ["南瓜"],
    "青椒": ["甜椒"],
    "紅棗": ["棗"],
    "柿": ["柿子"],
    "茶": ["茶葉"],
    # 開放資料把美洲胡桃誤作美洲湖桃。
    "美洲湖桃": ["美洲胡桃"],
}

# 附表五用字和附表一列名不同、但指同一作物。
SPELLING_VARIANTS: dict[str, list[str]] = {
    "柿子": ["柿"],
    "棗": ["紅棗"],
}

_PAIRS = {"(": ")", "（": "）", "【": "】", "[": "]"}
_CLOSER = {v: k for k, v in _PAIRS.items()}


def split_top(text: str, seps: str = "、，,") -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in text:
        if ch in _PAIRS:
            depth += 1
            buf.append(ch)
        elif ch in _CLOSER:
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch in seps and depth == 0:
            piece = "".join(buf).strip()
            if piece:
                parts.append(piece)
            buf = []
        else:
            buf.append(ch)
    piece = "".join(buf).strip()
    if piece:
        parts.append(piece)
    return parts


def _strip_outer_etc(text: str) -> tuple[str, bool]:
    body = text.strip()
    body = re.sub(r"[。．.\s]+$", "", body)
    open_ended = False
    if body.endswith("等"):
        open_ended = True
        body = body[:-1].strip()
    return body, open_ended


def _split_named_sections(text: str) -> list[str] | None:
    """ODT 附表五用「香辛植物(種子)：」分段，沒有 (1)(2)(3)(4)。"""
    pattern = r"(?=(?:香辛植物\([^)]*\)|草木本植物)\s*[：:])"
    if not re.search(pattern, text):
        return None
    parts = [part.strip(" 。") for part in re.split(pattern, text) if part.strip(" 。")]
    return parts or None


def _strip_item_etc(item: str) -> str:
    if item.endswith("等") and item.count("(") == item.count(")") and item.count("（") == item.count("）"):
        return item[:-1].strip()
    return item


class CropBuilder:
    def __init__(self) -> None:
        self.crops: list[dict] = []
        self.subgroups: set[str] = set()
        self.open_categories: list[str] = []
        self.closed_categories: list[str] = []
        self._seen: set[tuple[str, str]] = set()

    def add_crop(
        self,
        name: str,
        category: str,
        groups: list[str],
        *,
        aliases: list[str] | None = None,
        excerpt: str = "",
        alias_open: bool = False,
    ) -> dict | None:
        name = name.strip()
        if not name or name in {"等", "等。"}:
            return None
        key = (category, name)
        if key in self._seen:
            return None
        self._seen.add(key)
        group_list = []
        for group in groups:
            if group and group not in group_list:
                group_list.append(group)
        crop = {
            "id": f"{category}:{name}",
            "name": name,
            "category": category,
            "groups": group_list,
            "broads": broad_groups(category),
            "aliases": list(aliases or []),
            "alias_open": alias_open,
            "excerpt": excerpt or name,
            "separated_from": None,
            "origin": "annex5",
            "match_names": [name],
            "exact_keys": [norm_zh_key(name)],
            "soft": False,
        }
        self.crops.append(crop)
        return crop

    def parse_table(self, rows: list[dict]) -> None:
        for row in rows:
            category = re.sub(r"^\d+\.\s*", "", (row.get("類別") or "").strip())
            body = row.get("農作物類農產品") or ""
            text, open_ended = _strip_outer_etc(body)
            if open_ended:
                self.open_categories.append(category)
            else:
                self.closed_categories.append(category)
            numbered = re.search(r"\(\d+\)", text)
            named = _split_named_sections(text)
            if numbered:
                chunks = [chunk.strip() for chunk in re.split(r"\(\d+\)", text) if chunk.strip()]
                for chunk in chunks:
                    self._parse_section(chunk, category, [category])
            elif named:
                for chunk in named:
                    self._parse_section(chunk, category, [category])
            else:
                self._parse_list(text, category, [category])

    def _parse_section(self, text: str, category: str, groups: list[str]) -> None:
        matched = re.match(r"^([^：:]{1,40})[：:](.*)$", text, re.S)
        if not matched:
            self._parse_list(text, category, groups)
            return
        header = matched.group(1).strip()
        rest = matched.group(2).strip()
        extra = [header]
        if header.endswith("菜"):
            extra.append(header + "類")
        for name in extra:
            self.subgroups.add(name)
        self._parse_list(rest, category, groups + extra)

    def _parse_list(self, text: str, category: str, groups: list[str]) -> None:
        body, _open = _strip_outer_etc(text)
        for item in split_top(body):
            item = _strip_item_etc(item)
            if item:
                self._parse_item(item, category, groups)

    def _parse_item(self, item: str, category: str, groups: list[str]) -> None:
        bracket = re.match(r"^([^【]+)【(.+)】$", item)
        if bracket:
            header = bracket.group(1).strip()
            extra = [header]
            if header.endswith("菜"):
                extra.append(header + "類")
            for name in extra:
                self.subgroups.add(name)
            self._parse_list(bracket.group(2), category, groups + extra)
            return

        working = item
        qualifier = ""
        prefixed = re.match(r"^\[([^\]]+)\](.+)$", working)
        if prefixed:
            qualifier = prefixed.group(1).strip()
            working = prefixed.group(2).strip()

        name, inner = _peel_paren(working)
        aliases: list[str] = []
        alias_open = False
        if inner is None:
            official = name
        elif inner in {"乾", "鮮", "乾燥"}:
            official = working
        elif inner.startswith(("含", "包括", "包含")):
            payload = re.sub(r"^(含|包括|包含)", "", inner).strip()
            payload, alias_open = _strip_outer_etc(payload)
            aliases = split_top(payload)
            official = name
        elif ("、" in inner or "及" in inner) and (
            "科" in name or name.endswith("類") or name.endswith("植物")
        ):
            header_groups = [name]
            if not name.endswith("類"):
                header_groups.append(name + "類")
            for group in header_groups:
                self.subgroups.add(group)
            self._parse_list(inner, category, groups + header_groups)
            return
        elif "、" in inner or "及" in inner:
            aliases = split_top(inner.replace("及", "、"))
            official = name
        else:
            official = working

        extra_aliases = []
        if qualifier:
            extra_aliases.append(official)
            official_display = official
            excerpt = item
            crop = self.add_crop(
                official_display,
                category,
                groups,
                aliases=aliases,
                excerpt=excerpt,
                alias_open=alias_open,
            )
            if crop is not None:
                crop["aliases"].append(qualifier + official_display)
        else:
            self.add_crop(
                official,
                category,
                groups,
                aliases=aliases + extra_aliases,
                excerpt=item,
                alias_open=alias_open,
            )

    def subgroup_names(self) -> set[str]:
        names = set(self.subgroups)
        names.add(BERRY_GROUP)
        names.add("香辛植物")
        return names


def _peel_paren(text: str) -> tuple[str, str | None]:
    if not (text.endswith(")") or text.endswith("）")):
        return text, None
    closer = text[-1]
    opener = "(" if closer == ")" else "（"
    depth = 0
    for index in range(len(text) - 1, -1, -1):
        ch = text[index]
        if ch == closer:
            depth += 1
        elif ch == opener:
            depth -= 1
            if depth == 0:
                return text[:index], text[index + 1 : -1]
    return text, None


def _form_variants(name: str) -> list[str]:
    if name.endswith("(乾燥)"):
        return [name, name[: -len("(乾燥)")] + "(乾)"]
    if name.endswith("(乾)"):
        return [name, name[: -len("(乾)")] + "(乾燥)"]
    return [name]


def finalize_crops(builder: CropBuilder, label_tokens: set[str]) -> tuple[list[dict], list[dict], list[str]]:
    """Split aliases that 附表一另列，並加上俗稱。回傳 crops, aliases, warnings."""
    warnings: list[str] = []
    by_name: dict[str, list[dict]] = {}
    for crop in builder.crops:
        by_name.setdefault(crop["name"], []).append(crop)

    distinct_alias_names = set(label_tokens)
    for crop in list(builder.crops):
        kept = []
        for alias in crop["aliases"]:
            alias = alias.strip()
            if not alias:
                continue
            is_distinct = alias in distinct_alias_names or alias in by_name
            if is_distinct:
                if alias not in {c["name"] for c in by_name.get(alias, [])}:
                    created = builder.add_crop(
                        alias,
                        crop["category"],
                        list(crop["groups"]),
                        excerpt=crop["excerpt"],
                    )
                    if created is not None:
                        created["separated_from"] = crop["name"]
                        created["origin"] = "annex5-alias"
                        by_name.setdefault(alias, []).append(created)
                else:
                    for existing in by_name.get(alias, []):
                        if existing["category"] == crop["category"]:
                            existing["separated_from"] = existing["separated_from"] or crop["name"]
            else:
                kept.append(alias)
        crop["aliases"] = kept

    # 同名只應有一筆；若跨類別，保留並讓查詢變成候選。
    crops = builder.crops
    name_counts: dict[str, int] = {}
    for crop in crops:
        name_counts[crop["name"]] = name_counts.get(crop["name"], 0) + 1

    for crop in crops:
        variants = []
        for name in [crop["name"], *SPELLING_VARIANTS.get(crop["name"], [])]:
            # 乾燥／乾 只在另一方不是獨立作物時互指。
            for variant in _form_variants(name):
                if variant == crop["name"] or name_counts.get(variant, 0) == 0 or variant == name:
                    if variant not in variants:
                        variants.append(variant)
        for variant in SPELLING_VARIANTS.get(crop["name"], []):
            if variant not in variants:
                variants.append(variant)
        crop["match_names"] = variants
        crop["exact_keys"] = []
        for variant in variants:
            key = norm_zh_key(variant)
            if key and key not in crop["exact_keys"]:
                crop["exact_keys"].append(key)
        if crop["category"] == "小漿果類" and (
            "莓" in crop["name"] or crop["name"] in {"覆盆子", "黑莓"}
        ):
            if BERRY_GROUP not in crop["groups"]:
                crop["groups"].append(BERRY_GROUP)
        spice_parts = [group for group in crop["groups"] if group.startswith("香辛植物(")]
        if spice_parts and "香辛植物" not in crop["groups"]:
            crop["groups"].append("香辛植物")

    aliases: list[dict] = []
    crops_by_name = {}
    for crop in crops:
        crops_by_name.setdefault(crop["name"], []).append(crop)

    def _add_alias(alias: str, targets: list[str], kind: str, note: str) -> None:
        ids = []
        for target in targets:
            found = crops_by_name.get(target) or []
            if not found:
                warnings.append(f"別名「{alias}」找不到附表五作物「{target}」")
                continue
            ids.extend(crop["id"] for crop in found)
        if not ids:
            return
        aliases.append(
            {
                "alias": alias,
                "key": norm_zh_key(alias),
                "crop_ids": ids,
                "kind": kind,
                "note": note,
            }
        )

    for crop in crops:
        if crop["aliases"]:
            tail = "（名單未封閉）" if crop["alias_open"] else ""
            note = f"附表五：{crop.get('excerpt') or crop['name']}{tail}；附表一未另列，比照{crop['name']}"
            for alias in crop["aliases"]:
                _add_alias(alias, [crop["name"]], "annex5-include", note)
        if crop["separated_from"]:
            note = (
                f"附表五寫在{crop['separated_from']}項下，但附表一將「{crop['name']}」"
                f"與「{crop['separated_from']}」分開列示，依個別列名比對"
            )
            crop["separation_note"] = note

    for alias, targets in COMMON_ALIASES.items():
        if norm_zh_key(alias) in {norm_zh_key(crop["name"]) for crop in crops}:
            continue
        note = "俗稱對照：" + "、".join(targets)
        _add_alias(alias, targets, "common", note)

    if builder.closed_categories:
        warnings.append(
            "附表五下列類別結尾不是「等」，名單可能已封閉或來源缺字："
            + "、".join(builder.closed_categories)
        )
    return crops, aliases, warnings
