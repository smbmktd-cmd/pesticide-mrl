"""Build the lookup database from official CSVs."""

from __future__ import annotations

import csv
import hashlib
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .crops import CropBuilder, finalize_crops
from .footnotes import NOTE_ON_KEY, REDIRECTS, parse_footnotes
from .labels import CATEGORY_ALIASES, parse_label
from .normalize import clean_ws, norm_en_key, norm_label, norm_limit_number, norm_zh_key
from .pdfrepair import apply_row_repair

ANNEX_FILES = {
    "附表一": "annex1.csv",
    "附表二": "annex2.csv",
    "附表三": "annex3.csv",
    "附表四": "annex4.csv",
    "附表五": "annex5.csv",
}

SOURCE_URLS = {
    "附表一": "https://data.fda.gov.tw/data/opendata/export/13/csv",
    "附表二": "https://data.fda.gov.tw/data/opendata/export/173/csv",
    "附表三": "https://data.fda.gov.tw/data/opendata/export/14/csv",
    "附表四": "https://data.fda.gov.tw/data/opendata/export/15/csv",
    "附表五": "https://data.fda.gov.tw/data/opendata/export/16/csv",
    "法規": "https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=L0040083",
    "消費者專區": "https://consumer.fda.gov.tw/Law/PesticideList.aspx?nodeID=520&tk=1",
}


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _limit_fields(raw: str) -> tuple[str, str, bool]:
    number, star = norm_limit_number(raw)
    try:
        decimal = Decimal(number)
    except InvalidOperation as exc:
        raise ValueError(f"bad limit {raw!r}") from exc
    plain = format(decimal, "f")
    if "." in plain:
        plain = plain.rstrip("0").rstrip(".")
    return clean_ws(raw), plain, star


def _history_key(en_key: str, crop_raw: str, occurrence: int) -> str:
    base = f"{en_key}\t{crop_raw}"
    if occurrence:
        return f"{base}\t#{occurrence + 1}"
    return base


def build_database(
    raw_dir: Path,
    *,
    repair_map: dict | None = None,
    footnotes: list[dict] | None = None,
    meta_extra: dict | None = None,
) -> dict:
    warnings: list[str] = []
    paths = {name: raw_dir / filename for name, filename in ANNEX_FILES.items()}
    for name, path in paths.items():
        if not path.exists():
            raise FileNotFoundError(f"缺少{name}：{path}")
    hashes = {name: file_sha256(path) for name, path in paths.items()}
    annex1 = read_csv(paths["附表一"])
    annex2 = read_csv(paths["附表二"])
    annex3 = read_csv(paths["附表三"])
    annex4 = read_csv(paths["附表四"])
    annex5 = read_csv(paths["附表五"])

    categories = {(row.get("類別") or "").strip() for row in annex5}
    builder = CropBuilder()
    builder.parse_table(annex5)

    prepared_rows = []
    blank_zh = 0
    encoding_left = 0
    grouped_index: dict[str, int] = {}
    for annex_name, source_rows in (("附表一", annex1), ("附表二", annex2)):
        siblings: dict[str, list[str]] = {}
        for row in source_rows:
            key = norm_en_key(row.get("國際普通名稱") or "")
            siblings.setdefault(key, []).append(row.get("作物類別") or "")
        running: dict[str, int] = {}
        for row in source_rows:
            en_raw = row.get("國際普通名稱") or ""
            zh_raw = row.get("普通名稱") or ""
            crop_raw = row.get("作物類別") or ""
            note_raw = row.get("備註") or ""
            en = clean_ws(en_raw)
            en_key = norm_en_key(en)
            index = running.get(en_key, 0)
            running[en_key] = index + 1
            zh, note, crop, repairs = apply_row_repair(
                en_key=en_key,
                index=index,
                zh=clean_ws(zh_raw),
                note=clean_ws(note_raw),
                crop=crop_raw,
                repair_map=repair_map,
                siblings=siblings.get(en_key) or [],
            )
            if not clean_ws(zh_raw):
                blank_zh += 1
            if any("?" in value for value in (zh, note, crop)):
                encoding_left += 1
            limit_raw, limit, star = _limit_fields(row.get("容許量ppm") or "")
            prepared_rows.append(
                {
                    "annex": annex_name,
                    "en_raw": en_raw,
                    "zh_raw": zh_raw,
                    "crop_raw": crop_raw,
                    "note_raw": note_raw,
                    "en": en,
                    "zh": zh,
                    "crop": norm_label(crop) if crop else "",
                    "note": note,
                    "en_key": en_key,
                    "limit_raw": limit_raw,
                    "limit": limit,
                    "star": star,
                    "repairs": repairs,
                    "_index": index,
                }
            )

    # Tokens that prove an alias is listed on its own in 附表一.
    token_probe_categories = categories
    token_probe_subgroups = builder.subgroup_names()
    label_tokens: set[str] = set()
    for item in prepared_rows:
        parsed = parse_label(item["crop"], token_probe_categories, token_probe_subgroups)
        if parsed["kind"] == "specific":
            label_tokens.add(parsed["base"])
            label_tokens.add(parsed["label_core"])
        label_tokens.update(parsed["exclusions"])

    crops, aliases, crop_warnings = finalize_crops(builder, label_tokens)
    warnings.extend(crop_warnings)
    subgroups = builder.subgroup_names()
    for crop in crops:
        for group in crop["groups"]:
            subgroups.add(group)

    if len(categories) != 22:
        warnings.append(f"附表五類別數為 {len(categories)}，預期 22")
    if blank_zh:
        warnings.append(f"附表一／附表二有 {blank_zh} 列普通名稱空白，仍可用國際普通名稱查詢")
    if encoding_left:
        warnings.append(f"仍有 {encoding_left} 列含「?」缺字，原始值已保留")

    # Duplicate keys on the raw official crop string.
    buckets: dict[tuple[str, str, str], list[dict]] = {}
    for item in prepared_rows:
        buckets.setdefault((item["annex"], item["en_key"], item["crop_raw"]), []).append(item)
    duplicate_warnings = []
    for key, group in buckets.items():
        if len(group) < 2:
            continue
        limits = {entry["limit"] for entry in group}
        notes = {entry["note_raw"].strip() for entry in group}
        if len(limits) == 1 and len(notes) == 1:
            duplicate_warnings.append(
                f"{key[0]} {group[0]['en']}＋{key[2].strip()} 重複 {len(group)} 列且限值相同，查詢採用一列"
            )
            for entry in group[1:]:
                entry["_drop"] = True
        else:
            duplicate_warnings.append(
                f"{key[0]} {group[0]['en']}＋{key[2].strip()} 重複且內容不同，查詢會標成無法判定"
            )
    warnings.extend(duplicate_warnings[:30])
    if len(duplicate_warnings) > 30:
        warnings.append(f"另有 {len(duplicate_warnings) - 30} 筆重複列警告")

    occurrence: dict[tuple[str, str, str], int] = {}
    pesticides: dict[str, dict] = {}
    for item in prepared_rows:
        if item.get("_drop"):
            continue
        parsed = parse_label(item["crop"], categories, subgroups)
        key = (item["annex"], item["en_key"], item["crop_raw"])
        nth = occurrence.get(key, 0)
        occurrence[key] = nth + 1
        history_key = _history_key(item["en_key"], item["crop_raw"], nth)
        row_id = f"{item['annex']}-{item['en_key']}-{nth}-{norm_zh_key(item['crop_raw'])[:40]}"
        record = {
            "id": row_id,
            "annex": item["annex"],
            "en": item["en"],
            "zh": item["zh"],
            "en_raw": item["en_raw"],
            "zh_raw": item["zh_raw"],
            "crop": item["crop"],
            "crop_raw": item["crop_raw"],
            "limit": item["limit"],
            "limit_raw": item["limit_raw"],
            "star": item["star"],
            "note": item["note"],
            "note_raw": item["note_raw"],
            "kind": parsed["kind"],
            "base": parsed["base"],
            "label_core": parsed["label_core"],
            "exclusions": parsed["exclusions"],
            "form": parsed["form"],
            "depth": parsed["depth"],
            "repairs": item["repairs"],
            "history_key": history_key,
        }
        pest = pesticides.get(item["en_key"])
        if pest is None:
            pest = {
                "id": item["en_key"] or norm_zh_key(item["zh"]),
                "en": item["en"],
                "zh": item["zh"],
                "en_key": item["en_key"],
                "zh_key": norm_zh_key(item["zh"]) if item["zh"] else "",
                "en_raw": item["en_raw"],
                "zh_raw": item["zh_raw"],
                "exempt": False,
                "banned": False,
                "ban_make": "",
                "ban_use": "",
                "aliases": [],
                "note_ids": list(NOTE_ON_KEY.get(item["en_key"], [])),
                "rows": [],
            }
            pesticides[item["en_key"]] = pest
        if item["zh"] and not pest["zh"]:
            pest["zh"] = item["zh"]
            pest["zh_key"] = norm_zh_key(item["zh"])
        elif item["zh"] and pest["zh"] and norm_zh_key(item["zh"]) != pest["zh_key"] and "?" not in item["zh"]:
            # Keep the repaired / most common name; extra names become search aliases.
            if pest["zh_key"] and norm_zh_key(item["zh"]) != pest["zh_key"]:
                pest["aliases"].append({"zh": item["zh"], "en": item["en"], "note": "", "text": ""})
        pest["rows"].append(record)

    def ensure_pest(en: str, zh: str) -> dict:
        key = norm_en_key(en) or norm_zh_key(zh)
        pest = pesticides.get(key)
        if pest is None:
            pest = {
                "id": key,
                "en": clean_ws(en),
                "zh": clean_ws(zh),
                "en_key": norm_en_key(en),
                "zh_key": norm_zh_key(zh),
                "en_raw": en,
                "zh_raw": zh,
                "exempt": False,
                "banned": False,
                "ban_make": "",
                "ban_use": "",
                "aliases": [],
                "note_ids": list(NOTE_ON_KEY.get(norm_en_key(en), [])),
                "rows": [],
            }
            pesticides[key] = pest
        else:
            if zh and not pest["zh"]:
                pest["zh"] = clean_ws(zh)
                pest["zh_key"] = norm_zh_key(zh)
            if en and not pest["en"]:
                pest["en"] = clean_ws(en)
        return pest

    for row in annex3:
        pest = ensure_pest(row.get("英文名稱") or "", row.get("農藥名稱") or "")
        pest["exempt"] = True
    for row in annex4:
        pest = ensure_pest(row.get("英文名稱") or "", row.get("農藥名稱") or "")
        pest["banned"] = True
        pest["ban_make"] = clean_ws(row.get("禁止製造或輸入日期") or "")
        pest["ban_use"] = clean_ws(row.get("禁止銷售或使用日期") or "")

    for redirect in REDIRECTS:
        target = pesticides.get(redirect["target"])
        if target is None:
            warnings.append(f"{redirect['note']} 找不到對應農藥 {redirect['target']}")
            continue
        if redirect["note"] not in target["note_ids"]:
            target["note_ids"].append(redirect["note"])
        for alias in redirect["aliases"]:
            target["aliases"].append(
                {
                    "zh": alias["zh"],
                    "en": alias["en"],
                    "note": redirect["note"],
                    "text": f"{redirect['note']}：{alias['zh']}（{alias['en']}）比照{target['zh'] or target['en']}",
                }
            )

    # Drop alias duplicates.
    for pest in pesticides.values():
        seen = set()
        unique = []
        for alias in pest["aliases"]:
            marker = (norm_zh_key(alias.get("zh") or ""), norm_en_key(alias.get("en") or ""), alias.get("note"))
            if marker in seen:
                continue
            seen.add(marker)
            unique.append(alias)
        pest["aliases"] = unique

    notes = footnotes if footnotes is not None else parse_footnotes(None)
    pest_list = sorted(pesticides.values(), key=lambda item: (item.get("en") or item.get("zh") or "").lower())
    meta = {
        "sources": SOURCE_URLS,
        "hashes": hashes,
        "counts": {
            "附表一": len(annex1),
            "附表二": len(annex2),
            "附表三": len(annex3),
            "附表四": len(annex4),
            "附表五類別": len(annex5),
            "作物": len(crops),
            "農藥": len(pest_list),
            "容許量列": sum(len(pest["rows"]) for pest in pest_list),
            "普通名稱空白": blank_zh,
            "缺字未修": encoding_left,
        },
        "category_aliases": CATEGORY_ALIASES,
        "warnings": warnings,
    }
    if meta_extra:
        meta.update(meta_extra)
    return {
        "meta": meta,
        "footnotes": notes,
        "crops": crops,
        "aliases": aliases,
        "pesticides": pest_list,
    }
