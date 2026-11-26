import json
from pathlib import Path

import pytest

from pipeline.consumer import (
    ConsumerUnavailable,
    crosscheck_csv,
    describe_finding,
    parse_list_html,
    resolve_primary,
)

FIXTURE = """
<table><tr><th>項次</th><th>國際普通名稱</th></tr>
<tr>
  <td data-th="項次">1</td>
  <td data-th="國際普通名稱">Carbendazim</td>
  <td data-th="普通名稱">貝芬替</td>
  <td data-th="作物類別">&#33510;&#29916;</td>
  <td data-th="容許量(ppm)">0.5</td>
  <td data-th="備註">殺菌劑</td>
</tr></table>
共有 1 筆搜尋結果
"""


def test_parse_list_html_decodes_entities():
    rows, total = parse_list_html(FIXTURE, "mrl")
    assert total == 1
    assert rows == [
        {
            "國際普通名稱": "Carbendazim",
            "普通名稱": "貝芬替",
            "作物類別": "苦瓜",
            "容許量ppm": "0.5",
            "備註": "殺菌劑",
        }
    ]


def test_resolve_primary_keeps_snapshot_and_refuses_csv():
    kept, stale = resolve_primary(None, {"附表一": [{"作物類別": "苦瓜"}]})
    assert stale is True
    assert kept["附表一"][0]["作物類別"] == "苦瓜"
    fresh, stale = resolve_primary({"附表一": [{"作物類別": "番茄"}]}, {"附表一": []})
    assert stale is False
    assert fresh["附表一"][0]["作物類別"] == "番茄"
    with pytest.raises(ConsumerUnavailable):
        resolve_primary(None, None)


def test_crosscheck_reports_rows_csv_lacks():
    site = {
        "附表一": [
            {"國際普通名稱": "A", "普通名稱": "甲", "作物類別": "苦瓜", "容許量ppm": "0.2", "備註": ""},
            {"國際普通名稱": "B", "普通名稱": "乙", "作物類別": "番茄", "容許量ppm": "1", "備註": ""},
        ],
        "附表二": [],
        "附表三": [],
        "附表四": [
            {"農藥名稱": "滴滴涕", "英文名稱": "DDT", "禁止製造或輸入日期": "", "禁止銷售或使用日期": ""},
        ],
    }
    csv_tables = {
        "附表一": [
            {"國際普通名稱": "B", "普通名稱": "乙", "作物類別": "番茄", "容許量ppm": "1", "備註": ""},
        ],
        "附表二": [],
        "附表三": [],
        "附表四": [
            {"農藥名稱": "滴滴涕", "英文名稱": "DDT", "禁止製造或輸入日期": "", "禁止銷售或使用日期": ""},
            {"農藥名稱": "多出來", "英文名稱": "Extra", "禁止製造或輸入日期": "", "禁止銷售或使用日期": ""},
        ],
    }
    findings = crosscheck_csv(site, csv_tables)
    texts = [describe_finding(item) for item in findings]
    assert any("苦瓜" in text and "缺少" in text for text in texts)
    assert any("多出來" in text and "未採用" in text for text in texts)
    assert not any("番茄" in text and "缺少" in text for text in texts)


def test_archived_page_parses_ten_rows():
    sample = Path("/tmp/try.html")
    if not sample.exists():
        pytest.skip("no archived page in this environment")
    rows, total = parse_list_html(sample.read_text("utf-8", "replace"), "mrl")
    assert len(rows) == 10
    assert total == 8055
    assert rows[0]["作物類別"] == "柿"
