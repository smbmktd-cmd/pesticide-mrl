from pipeline.diffing import diff_databases


def _db(rows, crops, exempt=None, banned=None):
    pests = {}
    for row in rows:
        pest = pests.setdefault(
            row["en_key"],
            {
                "en": row["en"],
                "zh": row["zh"],
                "en_key": row["en_key"],
                "zh_key": row["zh"],
                "exempt": row["en_key"] in (exempt or set()),
                "banned": row["en_key"] in (banned or set()),
                "rows": [],
            },
        )
        pest["rows"].append(row)
    for key in exempt or set():
        pests.setdefault(key, {"en": key, "zh": key, "en_key": key, "zh_key": key, "exempt": True, "banned": False, "rows": []})
    for key in banned or set():
        pests.setdefault(key, {"en": key, "zh": key, "en_key": key, "zh_key": key, "exempt": False, "banned": True, "rows": []})
    return {
        "meta": {
            "synced_at": "2026-11-26T06:00:00+08:00",
            "law_amended_on": "2026-04-21",
            "dataset_updated_at": None,
            "sources": {"附表一": "https://example.test/13", "附表五": "https://example.test/16"},
            "hashes": {"附表一": "abc", "附表五": "def"},
        },
        "pesticides": list(pests.values()),
        "crops": crops,
    }


def _row(**kwargs):
    base = {
        "en": "Carbendazim",
        "zh": "貝芬替",
        "en_key": "carbendazim",
        "crop_raw": "苦瓜",
        "crop": "苦瓜",
        "limit": "0.5",
        "note_raw": "殺菌劑",
        "annex": "附表一",
        "history_key": "carbendazim\t苦瓜",
    }
    base.update(kwargs)
    return base


def test_limit_note_and_category_changes():
    old = _db(
        [_row()],
        [{"id": "瓜菜類:苦瓜", "name": "苦瓜", "category": "瓜菜類"}],
    )
    new = _db(
        [_row(limit="0.6", note_raw="殺蟲劑")],
        [{"id": "瓜菜類:苦瓜", "name": "苦瓜", "category": "瓜果類"}],
    )
    events = diff_databases(old, new)
    kinds = {(event["change"], event["field"], event["old"], event["new"]) for event in events}
    assert ("限值變更", "容許量ppm", "0.5", "0.6") in kinds
    assert ("備註變更", "備註", "殺菌劑", "殺蟲劑") in kinds
    assert ("分類變更", "作物分類", "瓜菜類", "瓜果類") in kinds
    limit = next(event for event in events if event["change"] == "限值變更")
    assert limit["pesticide_zh"] == "貝芬替"
    assert limit["pesticide_en"] == "Carbendazim"
    assert limit["crop"] == "苦瓜"
    assert limit["law_amended_on"] == "2026-04-21"
    assert limit["date"] == "2026-11-26"
    assert limit["history_key"] == "carbendazim\t苦瓜"


def test_first_snapshot_has_no_mass_inserts():
    assert diff_databases(None, _db([_row()], [])) == []
