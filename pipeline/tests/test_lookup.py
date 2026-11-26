"""Lookup priority against the real TFDA CSV snapshot."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.build import build_database
from pipeline.lookup import lookup

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
REPAIR = ROOT / "data" / "reference" / "repair-map.json"
NOTES = ROOT / "data" / "reference" / "footnotes.json"


@pytest.fixture(scope="module")
def db():
    repair = json.loads(REPAIR.read_text(encoding="utf-8")) if REPAIR.exists() else None
    notes = json.loads(NOTES.read_text(encoding="utf-8")) if NOTES.exists() else None
    return build_database(RAW, repair_map=repair, footnotes=notes)


def _crop(db, name):
    found = [crop for crop in db["crops"] if crop["name"] == name]
    assert found, name
    return found


def test_annex5_categories(db):
    assert db["meta"]["counts"]["附表五類別"] == 22
    assert db["meta"]["counts"]["附表一"] >= 8258
    assert db["meta"]["counts"]["附表四"] <= 67
    assert _crop(db, "苦瓜")[0]["category"] == "瓜菜類"
    assert _crop(db, "番茄")[0]["category"] == "果菜類"
    assert _crop(db, "西瓜")[0]["category"] == "瓜果類"
    assert "瓜果類" not in _crop(db, "苦瓜")[0]["groups"]
    cucumber = _crop(db, "小黃瓜")[0]
    assert cucumber["category"] == "瓜菜類"
    assert cucumber["separated_from"] == "胡瓜"
    assert len(_crop(db, "花豆(乾)")) == 1
    assert len(_crop(db, "花豆(鮮)")) == 1
    cloth = [
        row
        for pest in db["pesticides"]
        if pest["en_key"] == "clothianidin"
        for row in pest["rows"]
        if (row.get("crop") or "").strip() == "竹筍"
    ]
    assert len({row["limit"] for row in cloth}) <= 1
    assert db["meta"]["counts"]["普通名稱空白"] >= 0


def test_regression_bitter_melon_carbendazim(db):
    result = lookup(db, crop="苦瓜", pesticide="貝芬替", value="0.4", form="fresh")
    assert result["limit"] == "0.5"
    assert result["basis"] == "依瓜菜類標準"
    assert result["judgment"] == "合格"
    assert result["step"] == "category"
    assert "瓜果" not in result["basis"]
    assert result["crop"]["category"] == "瓜菜類"
    over = lookup(db, crop="苦瓜", pesticide="貝芬替", value="0.51")
    assert over["judgment"] == "超標"
    same = lookup(db, crop="苦瓜", pesticide="Carbendazim", value="0.5")
    assert same["judgment"] == "合格"


def test_regression_tomato_carbendazim(db):
    result = lookup(db, crop="番茄", pesticide="貝芬替", value="1.2", form="fresh")
    assert result["limit"] == "1"
    assert result["basis"] == "依其他果菜類(洛神葵除外)標準"
    assert result["judgment"] == "超標"
    assert result["step"] == "other_group"
    assert any(note["id"] == "註二" for note in result["footnotes"])


def test_regression_watermelon_carbendazim(db):
    result = lookup(db, crop="西瓜", pesticide="貝芬替", value="2", form="fresh")
    assert result["limit"] == "2"
    assert result["basis"] == "依瓜果類標準"
    assert result["judgment"] == "合格"
    assert result["step"] == "category"
    assert result["crop"]["category"] == "瓜果類"


def test_specific_beats_group_and_aliases_stay_split(db):
    roselle = lookup(db, crop="洛神葵", pesticide="貝芬替")
    assert roselle["basis"] == "依洛神葵個別標準"
    assert roselle["limit"] == "0.5"
    mini = lookup(db, crop="小黃瓜", pesticide="Acequinocyl", value="0.5")
    assert mini["basis"] == "依小黃瓜個別標準"
    assert "胡瓜" not in mini["basis"]
    cucumber = lookup(db, crop="胡瓜", pesticide="Acequinocyl")
    assert cucumber["basis"] == "依胡瓜(小黃瓜除外)個別標準"
    soybean = lookup(db, crop="黃豆", pesticide="貝芬替", value="0.5")
    assert soybean["crop"]["name"] == "大豆"
    assert soybean["basis"] == "依大豆個別標準"
    assert soybean["alias_note"]
    wheat = lookup(db, crop="黑小麥", pesticide="2‧4-D", value="2")
    assert wheat["crop"]["name"] == "小麥"
    assert wheat["limit"] == "2"
    assert wheat["judgment"] == "合格"


def test_ambiguous_crop_is_not_guessed(db):
    result = lookup(db, crop="花豆", pesticide="貝芬替")
    assert result["needs"] == "crop"
    names = {item["name"] for item in result["crop_candidates"]}
    assert names == {"花豆(乾)", "花豆(鮮)"}
    rice = lookup(db, crop="米", pesticide="貝芬替")
    assert {item["name"] for item in rice["crop_candidates"]} == {"水稻", "旱稻"}
    assert rice["judgment"] is None


def test_exempt_banned_unlisted_and_note5(db):
    exempt = lookup(db, crop="苦瓜", pesticide="蘇力菌", value="3")
    assert exempt["basis"] == "免訂（附表三）"
    assert exempt["judgment"] == "免訂"
    banned = lookup(db, crop="番茄", pesticide="滴滴涕", value="0.01")
    assert banned["basis"] == "禁用（附表四），不得檢出"
    assert banned["judgment"] == "超標"
    missing = lookup(db, crop="苦瓜", pesticide="完全沒有這種藥xyz", value="0.2")
    assert missing["basis"] == "表中未列，依第3條不得檢出"
    assert missing["judgment"] == "超標"
    dried = lookup(db, crop="薄荷", pesticide="貝芬替", form="dried")
    assert dried["basis"].startswith("依其他(茶類)*，註五定量極限")
    assert dried["limit"] == "0.05"
    fresh = lookup(db, crop="薄荷", pesticide="貝芬替", form="fresh")
    assert fresh["basis"] == "依其他(蔬果類)*，註五定量極限"
    assert fresh["limit"] == "0.01"
    dried_gourd = lookup(db, crop="苦瓜", pesticide="貝芬替", value="0.5", form="dried")
    assert dried_gourd["limit"] == "0.5"
    assert "不另乘濃縮係數" in dried_gourd["basis"]
    redirect = lookup(db, crop="苦瓜", pesticide="免賴得", value="0.4")
    assert redirect["limit"] == "0.5"
    assert redirect["basis"] == "依瓜菜類標準"
    assert "註二" in (redirect["via_note"] or "")


def test_encoding_repair_keeps_raw(db):
    pest = next(item for item in db["pesticides"] if item["en_key"] == "acequinocyl")
    assert "?" not in (pest.get("zh") or "")
    assert all("?" not in (row["crop"] + row["zh"] + row["note"]) for row in pest["rows"])
    damaged = [row for row in pest["rows"] if "?" in (row.get("crop_raw") or "")]
    if damaged:
        assert any(row["crop"] == "榅桲" for row in damaged)
        assert pest["zh"] == "亞醌蟎"
