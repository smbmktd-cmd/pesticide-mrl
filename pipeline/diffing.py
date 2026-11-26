"""Row-level diff between two built databases. Append-only history."""

from __future__ import annotations

from decimal import Decimal


def _row_index(db: dict) -> dict[str, dict]:
    index = {}
    for pest in db.get("pesticides") or []:
        for row in pest.get("rows") or []:
            key = row.get("history_key")
            if not key:
                continue
            index[key] = {
                "annex": row.get("annex"),
                "en": pest.get("en") or row.get("en") or "",
                "zh": pest.get("zh") or row.get("zh") or "",
                "crop_raw": row.get("crop_raw") or "",
                "crop": row.get("crop") or "",
                "limit": row.get("limit") or "",
                "note": (row.get("note_raw") or "").strip(),
                "history_key": key,
            }
    return index


def _status_index(db: dict, field: str) -> dict[str, dict]:
    index = {}
    for pest in db.get("pesticides") or []:
        if not pest.get(field):
            continue
        key = f"{field}:{pest.get('en_key') or pest.get('zh_key')}"
        index[key] = pest
    return index


def _crop_index(db: dict) -> dict[str, str]:
    index = {}
    for crop in db.get("crops") or []:
        # 同名跨類別時用 id 分開，避免蓋掉。
        index[crop["id"]] = crop["category"]
    return index


def _event(meta: dict, **fields) -> dict:
    return {
        "date": (meta.get("synced_at") or "")[:10],
        "time": meta.get("synced_at"),
        "change": fields["change"],
        "pesticide_zh": fields.get("pesticide_zh") or "",
        "pesticide_en": fields.get("pesticide_en") or "",
        "crop": fields.get("crop") or "",
        "field": fields.get("field") or "",
        "old": fields.get("old") or "",
        "new": fields.get("new") or "",
        "source": fields.get("source") or "",
        "file_hash": fields.get("file_hash") or "",
        "dataset_updated_at": meta.get("dataset_updated_at"),
        "law_amended_on": meta.get("law_amended_on"),
        "annex": fields.get("annex") or "",
        "history_key": fields.get("history_key") or "",
    }


def diff_databases(old: dict | None, new: dict) -> list[dict]:
    if not old:
        return []
    meta = new.get("meta") or {}
    hashes = meta.get("hashes") or {}
    events = []
    old_rows = _row_index(old)
    new_rows = _row_index(new)
    for key, row in new_rows.items():
        source = (meta.get("sources") or {}).get(row["annex"], "")
        file_hash = hashes.get(row["annex"], "")
        previous = old_rows.get(key)
        if previous is None:
            events.append(
                _event(
                    meta,
                    change="新增",
                    pesticide_zh=row["zh"],
                    pesticide_en=row["en"],
                    crop=row["crop_raw"].strip(),
                    field="容許量ppm",
                    old="",
                    new=row["limit"],
                    source=source,
                    file_hash=file_hash,
                    annex=row["annex"],
                    history_key=key,
                )
            )
            continue
        if previous["limit"] != row["limit"]:
            try:
                changed = Decimal(previous["limit"]) != Decimal(row["limit"])
            except Exception:
                changed = True
            if changed:
                events.append(
                    _event(
                        meta,
                        change="限值變更",
                        pesticide_zh=row["zh"],
                        pesticide_en=row["en"],
                        crop=row["crop_raw"].strip(),
                        field="容許量ppm",
                        old=previous["limit"],
                        new=row["limit"],
                        source=source,
                        file_hash=file_hash,
                        annex=row["annex"],
                        history_key=key,
                    )
                )
        if previous["note"] != row["note"]:
            events.append(
                _event(
                    meta,
                    change="備註變更",
                    pesticide_zh=row["zh"],
                    pesticide_en=row["en"],
                    crop=row["crop_raw"].strip(),
                    field="備註",
                    old=previous["note"],
                    new=row["note"],
                    source=source,
                    file_hash=file_hash,
                    annex=row["annex"],
                    history_key=key,
                )
            )
    for key, row in old_rows.items():
        if key in new_rows:
            continue
        events.append(
            _event(
                meta,
                change="刪除",
                pesticide_zh=row["zh"],
                pesticide_en=row["en"],
                crop=row["crop_raw"].strip(),
                field="容許量ppm",
                old=row["limit"],
                new="",
                source=(meta.get("sources") or {}).get(row["annex"], ""),
                file_hash=hashes.get(row["annex"], ""),
                annex=row["annex"],
                history_key=key,
            )
        )

    for field, annex, label in (
        ("exempt", "附表三", "免訂"),
        ("banned", "附表四", "禁用"),
    ):
        old_set = _status_index(old, field)
        new_set = _status_index(new, field)
        source = (meta.get("sources") or {}).get(annex, "")
        file_hash = hashes.get(annex, "")
        for key, pest in new_set.items():
            if key in old_set:
                continue
            events.append(
                _event(
                    meta,
                    change="新增",
                    pesticide_zh=pest.get("zh") or "",
                    pesticide_en=pest.get("en") or "",
                    field=label,
                    old="",
                    new=label,
                    source=source,
                    file_hash=file_hash,
                    annex=annex,
                )
            )
        for key, pest in old_set.items():
            if key in new_set:
                continue
            events.append(
                _event(
                    meta,
                    change="刪除",
                    pesticide_zh=pest.get("zh") or "",
                    pesticide_en=pest.get("en") or "",
                    field=label,
                    old=label,
                    new="",
                    source=source,
                    file_hash=file_hash,
                    annex=annex,
                )
            )

    old_crops = _crop_index(old)
    new_crops = _crop_index(new)
    names = {}
    for crop in list(old.get("crops") or []) + list(new.get("crops") or []):
        names[crop["id"]] = crop["name"]
    source = (meta.get("sources") or {}).get("附表五", "")
    file_hash = hashes.get("附表五", "")
    for crop_id, category in new_crops.items():
        previous = old_crops.get(crop_id)
        if previous is None:
            events.append(
                _event(
                    meta,
                    change="分類變更",
                    crop=names.get(crop_id, crop_id),
                    field="作物分類",
                    old="",
                    new=category,
                    source=source,
                    file_hash=file_hash,
                    annex="附表五",
                )
            )
        elif previous != category:
            events.append(
                _event(
                    meta,
                    change="分類變更",
                    crop=names.get(crop_id, crop_id),
                    field="作物分類",
                    old=previous,
                    new=category,
                    source=source,
                    file_hash=file_hash,
                    annex="附表五",
                )
            )
    for crop_id, category in old_crops.items():
        if crop_id in new_crops:
            continue
        events.append(
            _event(
                meta,
                change="分類變更",
                crop=names.get(crop_id, crop_id),
                field="作物分類",
                old=category,
                new="",
                source=source,
                file_hash=file_hash,
                annex="附表五",
            )
        )
    return events
