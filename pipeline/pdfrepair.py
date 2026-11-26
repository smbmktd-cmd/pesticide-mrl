"""Repair open-data '?' glyphs using the official 附表一 PDF text.

The CSV replaces some characters (notably 蟎) with literal '?'. Repairs are
applied only when the PDF row lines up with the CSV. Raw values stay intact.
"""

from __future__ import annotations

import re
import subprocess
from collections import defaultdict

from .normalize import clean_ws, norm_en_key, norm_label, squash_ws

OBVIOUS_CROPS = {
    "?桲": "榅桲",
    "?用豌豆": "葉用豌豆",
    "?花生芽": "落花生芽",
    "?蔔": "蘿蔔",
    "洋牛蒡?": "洋牛蒡葉",
    "穗醋?": "穗醋栗",
    "胡?蔔": "胡蘿蔔",
    "馬?薯": "馬鈴薯",
}

_PPM = re.compile(r"(\d+(?:\.\d+)?)\*?\s*$")
_NOTE = re.compile(r"(殺菌劑|殺蟲劑|殺蟎劑|殺草劑|除草劑|殺線蟲劑|生長調節劑|燻蒸劑|殺螺劑)\s*$")


def obvious_crop(crop: str, sibling_crops: list[str]) -> str | None:
    label = norm_label(crop)
    if label in OBVIOUS_CROPS:
        return OBVIOUS_CROPS[label]
    if "?結球萵苣" in label and "半結球萵苣" in label:
        return label.replace("?結球萵苣", "不結球萵苣")
    if label == "?結球萵苣" and any(norm_label(item) == "半結球萵苣" for item in sibling_crops):
        return "不結球萵苣"
    return None


def pdftotext(pdf_path: str, layout: bool = False) -> str:
    cmd = ["pdftotext", "-layout" if layout else "", pdf_path, "-"]
    cmd = [part for part in cmd if part]
    return subprocess.check_output(cmd, text=True, errors="replace")


def _names_pattern(names: list[str]) -> re.Pattern[str]:
    ordered = sorted({name for name in names if name}, key=len, reverse=True)
    return re.compile(r"(?:^|\s)(" + "|".join(re.escape(name) for name in ordered) + r")(?=\s|$)")


def extract_pdf_rows(layout_text: str, english_names: list[str]) -> dict[str, list[dict]]:
    """Best-effort rows keyed by normalized English name, in PDF order."""
    finder = _names_pattern(english_names)
    grouped: dict[str, list[dict]] = defaultdict(list)
    current = None

    def finish_crop(record: dict) -> None:
        crop = squash_ws(record.get("_crop") or "")
        crop = crop.replace("（", "(").replace("）", ")")
        record["crop"] = crop
        record.pop("_crop", None)

    for raw_line in layout_text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        found = finder.search(line)
        starts = False
        if found and found.start(1) <= 4:
            starts = True
        if starts:
            if current:
                finish_crop(current)
                grouped[current["en_key"]].append(current)
            en = found.group(1)
            rest = line[found.end(1) :].strip()
            zh = ""
            zh_match = re.match(r"([^\d\s*][^\d]*?)\s{2,}(.*)$", rest)
            if zh_match and not re.match(r"^[\d.]", zh_match.group(1)):
                zh = squash_ws(zh_match.group(1))
                rest = zh_match.group(2).strip()
            elif rest and re.match(r"^[\u4e00-\u9fff]", rest):
                bits = rest.split()
                if bits and re.match(r"^[\u4e00-\u9fff（）()]+$", bits[0]):
                    zh = bits[0]
                    rest = rest[len(bits[0]) :].strip()
            note = ""
            note_match = _NOTE.search(rest)
            if note_match:
                note = note_match.group(1)
                rest = rest[: note_match.start()].strip()
            ppm = ""
            ppm_match = re.search(r"(\d+(?:\.\d+)?\*?)\s*$", rest)
            if ppm_match:
                ppm = ppm_match.group(1)
                rest = rest[: ppm_match.start()].strip()
            current = {
                "en": en,
                "en_key": norm_en_key(en),
                "zh": zh,
                "note": note,
                "ppm": ppm,
                "_crop": rest,
            }
            continue
        if current is not None:
            extra = line.strip()
            extra_note = _NOTE.search(extra)
            if extra_note and not current.get("note"):
                current["note"] = extra_note.group(1)
                extra = extra[: extra_note.start()].strip()
            if extra and not re.match(r"^[A-Za-z]", extra):
                current["_crop"] = (current.get("_crop") or "") + extra
    if current:
        finish_crop(current)
        grouped[current["en_key"]].append(current)
    return grouped


def _wildcard_match(csv_crop: str, pdf_crop: str) -> bool:
    left = norm_label(csv_crop)
    right = norm_label(pdf_crop)
    if "?" not in left:
        return left == right
    parts = left.split("?")
    pattern = "^" + ".*".join(re.escape(part) for part in parts) + "$"
    return re.match(pattern, right) is not None and abs(len(right) - len(left)) <= left.count("?")


def build_repair_map(layout_text: str, csv_rows: list[dict]) -> dict:
    names = sorted({clean_ws(row.get("國際普通名稱") or "") for row in csv_rows})
    pdf_rows = extract_pdf_rows(layout_text, names)
    by_key: dict[str, list[dict]] = defaultdict(list)
    for row in csv_rows:
        by_key[norm_en_key(row.get("國際普通名稱") or "")].append(row)

    zh_by_en = {}
    note_by_en = {}
    aligned: dict[str, list[dict]] = {}
    stats = {"aligned": 0, "skipped": 0, "pdf_pesticides": len(pdf_rows)}

    for key, rows in by_key.items():
        pdf = pdf_rows.get(key) or []
        zh_values = {item.get("zh") or "" for item in pdf if item.get("zh") and "?" not in item["zh"]}
        # Ignore values that are really crops (very long or equal a csv crop).
        csv_crops = {norm_label(row.get("作物類別") or "") for row in rows}
        zh_values = {value for value in zh_values if norm_label(value) not in csv_crops and len(value) <= 20}
        if len(zh_values) == 1:
            zh_by_en[key] = next(iter(zh_values))
        notes = {item.get("note") or "" for item in pdf if item.get("note")}
        if len(notes) == 1:
            note_by_en[key] = next(iter(notes))
        if len(pdf) != len(rows):
            stats["skipped"] += 1
            continue
        ok = True
        for csv_row, pdf_row in zip(rows, pdf):
            if not _wildcard_match(csv_row.get("作物類別") or "", pdf_row.get("crop") or ""):
                ok = False
                break
        if not ok:
            stats["skipped"] += 1
            continue
        aligned[key] = [
            {
                "crop": item.get("crop") or "",
                "note": item.get("note") or "",
                "zh": item.get("zh") or "",
                "ppm": item.get("ppm") or "",
            }
            for item in pdf
        ]
        stats["aligned"] += 1
    return {
        "zh_by_en": zh_by_en,
        "note_by_en": note_by_en,
        "rows": aligned,
        "stats": stats,
    }


def apply_row_repair(
    *,
    en_key: str,
    index: int,
    zh: str,
    note: str,
    crop: str,
    repair_map: dict | None,
    siblings: list[str],
) -> tuple[str, str, str, list[str]]:
    repairs: list[str] = []
    crop_out, note_out, zh_out = crop, note, zh
    obvious = obvious_crop(crop, siblings)
    if obvious and obvious != norm_label(crop):
        crop_out = obvious
        repairs.append("作物類別")
    if not repair_map:
        if "?" in note_out and note_out.strip() in {"殺?劑", "殺?劑"}:
            # 不在沒有 PDF 時猜普通名稱；備註的單一缺字另記警告，不強改。
            pass
        return zh_out, clean_ws(note_out), crop_out, repairs

    aligned = (repair_map.get("rows") or {}).get(en_key)
    if aligned and index < len(aligned):
        item = aligned[index]
        pdf_crop = item.get("crop") or ""
        if "?" in crop and pdf_crop and _wildcard_match(crop, pdf_crop):
            crop_out = pdf_crop
            if "作物類別" not in repairs:
                repairs.append("作物類別")
        pdf_note = item.get("note") or ""
        if "?" in note and pdf_note:
            note_out = pdf_note
            repairs.append("備註")
        pdf_zh = item.get("zh") or ""
        if (not zh or "?" in zh) and pdf_zh and "?" not in pdf_zh and norm_label(pdf_zh) != norm_label(crop_out):
            zh_out = pdf_zh
            if "普通名稱" not in repairs:
                repairs.append("普通名稱")
    if "?" in zh_out:
        mapped = (repair_map.get("zh_by_en") or {}).get(en_key)
        if mapped:
            zh_out = mapped
            if "普通名稱" not in repairs:
                repairs.append("普通名稱")
    if "?" in note_out:
        mapped_note = (repair_map.get("note_by_en") or {}).get(en_key)
        if mapped_note and "?" not in mapped_note:
            note_out = mapped_note
            if "備註" not in repairs:
                repairs.append("備註")
    return clean_ws(zh_out), clean_ws(note_out), norm_label(crop_out) if repairs else crop_out, repairs
