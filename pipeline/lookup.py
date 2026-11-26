"""MRL lookup. Priority is fixed and covered by regression tests.

1. 附表三 免訂
2. 個別作物列（含「胡瓜(小黃瓜除外)」這類列名；不把別名併回去）
3. 「其他○類(A、B除外)」：作物屬於該群且未被除外。同層取排除最多者。
4. 子類整列，然後類別整列（含附表一的「米」「茶」）
5. 「其他(蔬果類/穀類/茶類)*」註五定量極限
6. 附表四 禁用
7. 未列 → 第3條不得檢出

作物不在附表五，或俗稱對到多筆時，只回候選，不猜類別。
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from .labels import CATEGORY_ALIASES, note5_base
from .normalize import norm_en_key, norm_zh_key

_BUFFER = re.compile(r"於\s*(\d+)\s*年\s*(\d+)\s*月\s*(\d+)\s*日刪除，緩衝期\s*(\d+)\s*年")


def buffer_info(note: str | None) -> dict | None:
    """Parse 已刪除＋緩衝期 only when the note states the date and the length."""
    text = note or ""
    if "刪除" not in text:
        return None
    info = {"deleted": True, "raw": text, "end": None}
    matched = _BUFFER.search(text)
    if not matched:
        return info
    year = int(matched.group(1)) + 1911 + int(matched.group(4))
    month = int(matched.group(2))
    day = int(matched.group(3))
    info["end"] = f"{year:04d}-{month:02d}-{day:02d}"
    return info


def buffer_warning(info: dict | None) -> str:
    if not info:
        return ""
    text = "此標準已刪除，緩衝期內適用，請確認產品製造/輸入日期"
    if info.get("end"):
        text += f"（緩衝期至 {info['end']}）"
    return text

ND_TOKENS = {
    "nd",
    "n.d",
    "n.d.",
    "未檢出",
    "陰性",
    "nd.",
    "—",
    "-",
    "－",
    "<loq",
    "＜loq",
    "traces",
    "trace",
}


def _crop_names(crop: dict) -> set[str]:
    return set(crop.get("match_names") or []) | {crop["name"]}


def _excluded(row: dict, crop: dict) -> bool:
    names = _crop_names(crop)
    return any(item in names for item in row.get("exclusions") or [])


def _specific_hit(row: dict, crop: dict) -> bool:
    if row.get("kind") != "specific" or _excluded(row, crop):
        return False
    if crop["name"] in {row.get("label_core"), row.get("base")}:
        return True
    if row.get("form") is None and row.get("base") in crop.get("match_names", []):
        return True
    if row.get("label_core") in crop.get("match_names", []):
        return True
    return False


def _category_hit(row: dict, crop: dict) -> bool:
    if _excluded(row, crop):
        return False
    category = crop["category"]
    aliases = CATEGORY_ALIASES.get(category, [])
    return row.get("base") in {category, *aliases}


def _prefer_form(rows: list[dict], form: str) -> list[dict]:
    typed = [row for row in rows if row.get("form") == form]
    if typed:
        return typed
    return [row for row in rows if not row.get("form")]


def _form_ok(row: dict, form: str) -> bool:
    row_form = row.get("form")
    if row_form == "dried" and form != "dried":
        return False
    if row_form == "fresh" and form != "fresh":
        return False
    return True


def _narrow(rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    best = max((row.get("depth") or 0, len(row.get("exclusions") or [])) for row in rows)
    return [
        row
        for row in rows
        if (row.get("depth") or 0, len(row.get("exclusions") or [])) == best
    ]


def _limits_agree(rows: list[dict]) -> bool:
    return len({row.get("limit") for row in rows}) == 1


def _basis(step: str, row: dict, form: str) -> str:
    label = row.get("crop") or row.get("label") or ""
    if step == "specific":
        text = f"依{label}個別標準"
    elif step == "note5":
        text = f"依{label}，註五定量極限"
    else:
        text = f"依{label}標準"
    if row.get("annex") == "附表二":
        text += "（附表二外源性）"
    if form == "dried" and row.get("form") != "dried" and step != "note5":
        text += "（第2條以市售型態計，不另乘濃縮係數）"
    if step == "note5" and form == "dried" and row.get("base") == "茶類":
        text += "（乾燥基質對應檢驗方法 III 類）"
    return text


def _parse_amount(value) -> dict:
    if value is None:
        return {"kind": "empty"}
    text = str(value).strip()
    if text == "":
        return {"kind": "empty"}
    compact = text.replace(" ", "").replace("ppm", "").replace("PPM", "")
    if compact.casefold() in ND_TOKENS or compact in {"未檢出", "陰性", "N.D.", "ND", "n.d."}:
        return {"kind": "nd"}
    less = compact[1:] if compact[:1] in {"<", "＜", "≤"} else None
    if less is not None:
        try:
            return {"kind": "lt", "value": Decimal(less)}
        except InvalidOperation:
            return {"kind": "invalid", "raw": text}
    try:
        return {"kind": "num", "value": Decimal(compact.replace(",", ""))}
    except InvalidOperation:
        return {"kind": "invalid", "raw": text}


def _judge(amount: dict, *, mode: str, limit: str | None) -> tuple[str, str]:
    """mode: numeric | nondetect | exempt."""
    if amount["kind"] == "invalid":
        return "無法判定", "檢出量無法解讀"
    if amount["kind"] == "empty":
        return "未輸入檢出量", ""
    if mode == "exempt":
        return "免訂", "附表三所列，第4條毋需依殘留量判定"
    if mode == "nondetect":
        if amount["kind"] == "nd" or (amount["kind"] == "num" and amount["value"] <= 0):
            return "未檢出", "殘留為未檢出"
        if amount["kind"] == "lt":
            return "無法判定", "報告以小於符號表示，無法確認是否未檢出"
        return "超標", "不得檢出"
    limit_value = Decimal(limit or "0")
    if amount["kind"] == "nd" or (amount["kind"] == "num" and amount["value"] <= 0):
        return "合格", "未檢出，未逾容許量"
    if amount["kind"] == "lt":
        if amount["value"] <= limit_value:
            return "合格", "報告值上限未逾容許量"
        return "無法判定", "小於符號的上限高於容許量，無法確認是否超標"
    if amount["value"] <= limit_value:
        return "合格", "檢出量未逾容許量"
    return "超標", "檢出量逾容許量"


def suggest_crops(crops: list[dict], aliases: list[dict], query: str, limit: int = 12) -> list[dict]:
    key = norm_zh_key(query)
    if not key:
        return []
    scored: list[tuple[int, dict]] = []
    for crop in crops:
        name_key = norm_zh_key(crop["name"])
        score = None
        if name_key == key:
            score = 0
        elif name_key.startswith(key):
            score = 1
        elif key in name_key:
            score = 2
        else:
            for alias in crop.get("aliases") or []:
                alias_key = norm_zh_key(alias)
                if alias_key.startswith(key) or key in alias_key:
                    score = 3
                    break
        if score is not None:
            scored.append((score, crop))
    for alias in aliases:
        if key == alias["key"] or key in alias["key"]:
            for crop in crops:
                if crop["id"] in alias["crop_ids"]:
                    scored.append((1 if key == alias["key"] else 4, crop))
    scored.sort(key=lambda item: (item[0], len(item[1]["name"])))
    seen = set()
    out = []
    for _score, crop in scored:
        if crop["id"] in seen:
            continue
        seen.add(crop["id"])
        out.append(crop)
        if len(out) >= limit:
            break
    return out


def suggest_pesticides(pesticides: list[dict], query: str, limit: int = 12) -> list[dict]:
    zh = norm_zh_key(query)
    en = norm_en_key(query)
    scored: list[tuple[int, dict]] = []
    for pest in pesticides:
        score = None
        if zh and pest.get("zh_key") == zh:
            score = 0
        elif en and pest.get("en_key") == en:
            score = 0
        elif zh and pest.get("zh_key", "").startswith(zh):
            score = 1
        elif en and len(en) >= 3 and pest.get("en_key", "").startswith(en):
            score = 1
        elif zh and zh in (pest.get("zh_key") or ""):
            score = 2
        elif en and len(en) >= 3 and en in (pest.get("en_key") or ""):
            score = 2
        else:
            for alias in pest.get("aliases") or []:
                if zh and zh in norm_zh_key(alias.get("zh") or ""):
                    score = 3
                    break
                if en and len(en) >= 3 and en in norm_en_key(alias.get("en") or ""):
                    score = 3
                    break
        if score is not None:
            scored.append((score, pest))
    scored.sort(key=lambda item: (item[0], item[1].get("en") or item[1].get("zh") or ""))
    seen = set()
    out = []
    for _score, pest in scored:
        if pest["id"] in seen:
            continue
        seen.add(pest["id"])
        out.append(pest)
        if len(out) >= limit:
            break
    return out


def resolve_crop(db: dict, query: str | None = None, crop_id: str | None = None) -> dict:
    crops = db["crops"]
    if crop_id:
        for crop in crops:
            if crop["id"] == crop_id:
                return {"crop": crop, "candidates": [], "alias_note": None}
        return {"crop": None, "candidates": [], "alias_note": None, "error": "找不到作物代號"}
    key = norm_zh_key(query or "")
    if not key:
        return {"crop": None, "candidates": [], "alias_note": None}
    exact = [crop for crop in crops if key in crop.get("exact_keys", [])]
    uniq = {crop["id"]: crop for crop in exact}
    if len(uniq) == 1:
        return {"crop": next(iter(uniq.values())), "candidates": [], "alias_note": None}
    if len(uniq) > 1:
        return {"crop": None, "candidates": list(uniq.values()), "alias_note": None}
    alias_hits = [alias for alias in db.get("aliases") or [] if alias["key"] == key]
    linked = []
    notes = []
    for alias in alias_hits:
        notes.append(alias["note"])
        for crop in crops:
            if crop["id"] in alias["crop_ids"]:
                linked.append(crop)
    linked_uniq = {crop["id"]: crop for crop in linked}
    if len(linked_uniq) == 1:
        return {
            "crop": next(iter(linked_uniq.values())),
            "candidates": [],
            "alias_note": notes[0] if notes else None,
        }
    if len(linked_uniq) > 1:
        return {"crop": None, "candidates": list(linked_uniq.values()), "alias_note": None}
    return {
        "crop": None,
        "candidates": suggest_crops(crops, db.get("aliases") or [], query or ""),
        "alias_note": None,
    }


def resolve_pesticide(db: dict, query: str | None = None, pesticide_id: str | None = None) -> dict:
    pests = db["pesticides"]
    if pesticide_id:
        for pest in pests:
            if pest["id"] == pesticide_id:
                return {"pesticide": pest, "candidates": [], "via": None}
        return {"pesticide": None, "candidates": [], "via": None}
    zh = norm_zh_key(query or "")
    en = norm_en_key(query or "")
    exact = []
    for pest in pests:
        if zh and pest.get("zh_key") and pest["zh_key"] == zh:
            exact.append(pest)
        elif en and len(en) >= 2 and pest.get("en_key") == en:
            exact.append(pest)
    uniq = {pest["id"]: pest for pest in exact}
    if len(uniq) == 1:
        return {"pesticide": next(iter(uniq.values())), "candidates": [], "via": None}
    if len(uniq) > 1:
        return {"pesticide": None, "candidates": list(uniq.values()), "via": None}
    via = []
    for pest in pests:
        for alias in pest.get("aliases") or []:
            if zh and norm_zh_key(alias.get("zh") or "") == zh:
                via.append((pest, alias))
            elif en and len(en) >= 3 and norm_en_key(alias.get("en") or "") == en:
                via.append((pest, alias))
    via_uniq = {pest["id"]: (pest, alias) for pest, alias in via}
    if len(via_uniq) == 1:
        pest, alias = next(iter(via_uniq.values()))
        return {"pesticide": pest, "candidates": [], "via": alias}
    if len(via_uniq) > 1:
        return {"pesticide": None, "candidates": [item[0] for item in via_uniq.values()], "via": None}
    return {
        "pesticide": None,
        "candidates": suggest_pesticides(pests, query or ""),
        "via": None,
    }


def _public_crop(crop: dict) -> dict:
    return {
        "id": crop["id"],
        "name": crop["name"],
        "category": crop["category"],
        "groups": crop.get("groups") or [],
        "excerpt": crop.get("excerpt") or crop["name"],
        "separated_from": crop.get("separated_from"),
        "separation_note": crop.get("separation_note"),
        "aliases": crop.get("aliases") or [],
    }


def _public_pest(pest: dict) -> dict:
    return {
        "id": pest["id"],
        "en": pest.get("en") or "",
        "zh": pest.get("zh") or "",
        "exempt": bool(pest.get("exempt")),
        "banned": bool(pest.get("banned")),
        "ban_make": pest.get("ban_make") or "",
        "ban_use": pest.get("ban_use") or "",
    }


def _public_row(row: dict) -> dict:
    return {
        "id": row["id"],
        "annex": row["annex"],
        "en": row.get("en") or "",
        "zh": row.get("zh") or "",
        "crop": row.get("crop") or "",
        "crop_raw": row.get("crop_raw") or "",
        "limit": row.get("limit") or "",
        "limit_raw": row.get("limit_raw") or "",
        "star": bool(row.get("star")),
        "note": row.get("note") or "",
        "note_raw": row.get("note_raw") or "",
        "kind": row.get("kind"),
        "history_key": row.get("history_key"),
        "repairs": row.get("repairs") or [],
    }


def lookup(
    db: dict,
    *,
    crop: str | None = None,
    crop_id: str | None = None,
    pesticide: str | None = None,
    pesticide_id: str | None = None,
    value=None,
    form: str = "fresh",
) -> dict:
    if form not in {"fresh", "dried"}:
        form = "fresh"
    crop_hit = resolve_crop(db, crop, crop_id)
    pest_hit = resolve_pesticide(db, pesticide, pesticide_id)
    result = {
        "ok": False,
        "needs": None,
        "form": form,
        "crop": _public_crop(crop_hit["crop"]) if crop_hit.get("crop") else None,
        "crop_candidates": [_public_crop(item) for item in crop_hit.get("candidates") or []],
        "alias_note": crop_hit.get("alias_note"),
        "pesticide": _public_pest(pest_hit["pesticide"]) if pest_hit.get("pesticide") else None,
        "pesticide_candidates": [_public_pest(item) for item in pest_hit.get("candidates") or []],
        "via": pest_hit.get("via"),
        "step": None,
        "basis": None,
        "limit": None,
        "limit_raw": None,
        "star": False,
        "judgment": None,
        "judgment_detail": None,
        "ambiguous": False,
        "rows": [],
        "footnotes": [],
        "trace": [],
        "warnings": [],
        "history_keys": [],
        "official_text": None,
        "note5_group": None,
        "buffer": None,
        "buffer_warning": "",
    }
    if crop_hit.get("crop") is None:
        result["needs"] = "crop"
        if not result["crop_candidates"]:
            result["basis"] = "附表五找不到這個原料，請改寫官方作物名或從候選選取。名單以「等」結尾，未列名者不自動歸類。"
        else:
            result["basis"] = "原料名稱對到多筆附表五作物，請選一筆，系統不會代為猜測類別。"
        return result
    if pest_hit.get("pesticide") is None:
        result["needs"] = "pesticide"
        if result["pesticide_candidates"]:
            result["basis"] = "農殘名稱對到多筆，請選取普通名稱或國際普通名稱。"
        else:
            # 完全找不到農藥：若作物已確定，第3條不得檢出仍適用，但先確認不是打字差一點。
            suggestions = suggest_pesticides(db["pesticides"], pesticide or "", limit=5)
            result["pesticide_candidates"] = [_public_pest(item) for item in suggestions]
            if suggestions:
                result["basis"] = "查無完全相符的農藥名稱。若確定表中未列，依第3條不得檢出；請先核對下方相似名稱。"
                result["step"] = "unlisted_confirm"
                result["judgment"] = "待確認"
            else:
                chosen = crop_hit["crop"]
                result["needs"] = None
                result["step"] = "unlisted"
                result["basis"] = "表中未列，依第3條不得檢出"
                result["ok"] = True
                amount = _parse_amount(value)
                judgment, detail = _judge(amount, mode="nondetect", limit=None)
                result["judgment"] = judgment
                result["judgment_detail"] = detail
                result["trace"] = [
                    "附表三免訂：否",
                    "附表一／附表二：無此農藥",
                    "附表四禁用：否",
                    "第3條：未列不得檢出",
                ]
                result["official_text"] = f"{chosen['name']}／{pesticide or ''}／表中未列"
            return result

    pest = pest_hit["pesticide"]
    chosen = crop_hit["crop"]
    amount = _parse_amount(value)
    footnotes = []
    for note_id in pest.get("note_ids") or []:
        for note in db.get("footnotes") or []:
            if note["id"] == note_id:
                footnotes.append(note)
    if pest_hit.get("via") and pest_hit["via"].get("note"):
        via_note = pest_hit["via"]["note"]
        if not any(item["id"] == via_note for item in footnotes):
            for note in db.get("footnotes") or []:
                if note["id"] == via_note:
                    footnotes.append(note)
    result["footnotes"] = footnotes
    result["via_note"] = None
    if pest_hit.get("via"):
        alias = pest_hit["via"]
        result["via_note"] = alias.get("text") or (
            f"{alias.get('note') or ''}：{alias.get('zh') or alias.get('en')} 比照 {pest.get('zh') or pest.get('en')}"
        )

    trace = []
    if pest.get("exempt"):
        trace.append("附表三免訂：是")
        result.update(
            {
                "ok": True,
                "step": "exempt",
                "basis": "免訂（附表三）",
                "trace": trace,
                "official_text": f"{pest.get('zh') or pest.get('en')}／附表三得免訂容許量",
            }
        )
        judgment, detail = _judge(amount, mode="exempt", limit=None)
        result["judgment"] = judgment
        result["judgment_detail"] = detail
        return result
    trace.append("附表三免訂：否")

    rows = [row for row in pest.get("rows") or [] if _form_ok(row, form)]
    group_set = set(chosen.get("groups") or [])
    specs = [row for row in rows if _specific_hit(row, chosen)]
    others = [
        row
        for row in rows
        if row.get("kind") == "other_group" and row.get("base") in group_set and not _excluded(row, chosen)
    ]
    subs = [
        row
        for row in rows
        if row.get("kind") == "subgroup" and row.get("base") in group_set and not _excluded(row, chosen)
    ]
    cats = [row for row in rows if row.get("kind") == "category" and _category_hit(row, chosen)]
    broads = [
        row
        for row in rows
        if row.get("kind") == "broad"
        and form == "dried"
        and row.get("base") in set(chosen.get("broads") or [])
        and not _excluded(row, chosen)
    ]
    expected = note5_base(chosen["category"], form)
    result["note5_group"] = expected
    stars = [row for row in rows if row.get("kind") == "note5" and row.get("base") == expected]

    specs = _prefer_form(specs, form)
    cats = _prefer_form(cats, form)
    if chosen.get("uncategorized"):
        others, subs, cats, broads, stars = [], [], [], [], []
        trace.append("附表五未列此名稱，只比對同名個別列")

    chosen_rows: list[dict] = []
    step = None
    if specs:
        chosen_rows, step = specs, "specific"
        trace.append(f"個別標準：{specs[0].get('crop')} {specs[0].get('limit_raw')}")
    else:
        trace.append("個別標準：無")
    if step is None:
        narrowed = _narrow(others)
        if narrowed:
            chosen_rows, step = narrowed, "other_group"
            trace.append("其他類（除外）：" + "、".join(row.get("crop") or "" for row in narrowed))
        else:
            trace.append("其他類（除外）：無")
    if step is None:
        if subs:
            chosen_rows, step = subs, "subgroup"
            trace.append("子類標準：" + "、".join(row.get("crop") or "" for row in subs))
        else:
            trace.append("子類標準：無")
    if step is None:
        if cats:
            chosen_rows, step = cats, "category"
            trace.append("類別標準：" + "、".join(row.get("crop") or "" for row in cats))
        else:
            trace.append("類別標準：無")
    if step is None:
        if broads:
            chosen_rows, step = broads, "broad"
            trace.append("乾燥蔬果概括列：" + "、".join(row.get("crop") or "" for row in broads))
        else:
            trace.append("乾燥蔬果概括列：無")
    if step is None:
        if stars:
            chosen_rows, step = stars, "note5"
            trace.append(f"註五：其他({expected})*")
        else:
            trace.append("註五兜底列：無")

    if chosen_rows:
        ambiguous = not _limits_agree(chosen_rows)
        result["ambiguous"] = ambiguous
        result["rows"] = [_public_row(row) for row in chosen_rows]
        result["history_keys"] = [row.get("history_key") for row in chosen_rows if row.get("history_key")]
        result["step"] = step
        result["trace"] = trace
        if ambiguous:
            result["ok"] = True
            result["basis"] = "有多筆可能適用標準且限值不同，請人工確認，系統不代為挑選。"
            result["judgment"] = "無法判定"
            result["judgment_detail"] = "同優先順位有不同容許量"
            result["official_text"] = "；".join(
                f"{row.get('crop')} {row.get('limit_raw')} ppm" for row in chosen_rows
            )
            return result
        top = chosen_rows[0]
        basis = _basis(step, top, form)
        if len(chosen_rows) > 1:
            extra = "、".join(row.get("crop") or "" for row in chosen_rows[1:])
            basis += f"（另有相同限值：{extra}）"
        judgment, detail = _judge(amount, mode="numeric", limit=top.get("limit"))
        if step == "note5":
            for note in db.get("footnotes") or []:
                if note["id"] == "註五" and note not in result["footnotes"]:
                    result["footnotes"].append(note)
        info = buffer_info(top.get("note"))
        warning = buffer_warning(info)
        if warning:
            detail = f"{warning} {detail}".strip()
        result["buffer"] = info
        result["buffer_warning"] = warning
        result.update(
            {
                "ok": True,
                "basis": basis,
                "limit": top.get("limit"),
                "limit_raw": top.get("limit_raw"),
                "star": bool(top.get("star")),
                "judgment": judgment,
                "judgment_detail": detail,
                "official_text": (
                    f"{top.get('zh') or ''} {top.get('en') or ''}／{top.get('crop')}／"
                    f"{top.get('limit_raw')} ppm／{top.get('note') or ''}"
                ).strip(),
            }
        )
        return result

    if pest.get("banned"):
        trace.append("附表四禁用：是")
        result.update(
            {
                "ok": True,
                "step": "banned",
                "basis": "禁用（附表四），不得檢出",
                "trace": trace,
                "official_text": (
                    f"{pest.get('zh') or pest.get('en')}／附表四禁用"
                    f"／禁止銷售或使用：{pest.get('ban_use') or '—'}"
                ),
            }
        )
        judgment, detail = _judge(amount, mode="nondetect", limit=None)
        result["judgment"] = judgment
        result["judgment_detail"] = detail
        return result
    trace.append("附表四禁用：否")
    trace.append("第3條：未列不得檢出")
    result.update(
        {
            "ok": True,
            "step": "unlisted",
            "basis": "表中未列，依第3條不得檢出",
            "trace": trace,
            "official_text": f"{pest.get('zh') or pest.get('en')}／{chosen['name']}／表中未列",
        }
    )
    judgment, detail = _judge(amount, mode="nondetect", limit=None)
    result["judgment"] = judgment
    result["judgment_detail"] = detail
    return result
