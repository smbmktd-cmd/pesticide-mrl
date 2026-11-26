"""Cross-check the open-data CSV against the statute page and the consumer site."""

from __future__ import annotations

import re
import ssl
import urllib.request
from datetime import datetime, timedelta, timezone

TPE = timezone(timedelta(hours=8))
LAW_URL = "https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=L0040083"
CONSUMER_URL = "https://consumer.fda.gov.tw/Law/PesticideList.aspx?nodeID=520&tk=1"
UA = "pesticide-mrl-sync/1.0 (public MRL lookup; +https://github.com/smbmktd-cmd/pesticide-mrl)"


def now_taipei() -> str:
    return datetime.now(TPE).isoformat(timespec="seconds")


def roc_to_iso(year: str, month: str, day: str) -> str:
    return f"{int(year) + 1911:04d}-{int(month):02d}-{int(day):02d}"


def _get(url: str, timeout: int = 40) -> tuple[str, dict]:
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    context = ssl.create_default_context()
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        raw = response.read()
        headers = {key.lower(): value for key, value in response.headers.items()}
        charset = response.headers.get_content_charset() or "utf-8"
        try:
            text = raw.decode(charset)
        except LookupError:
            text = raw.decode("utf-8", errors="replace")
    return text, headers


def fetch_bytes(url: str, timeout: int = 60) -> tuple[bytes, dict]:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    context = ssl.create_default_context()
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        payload = response.read()
        headers = {key.lower(): value for key, value in response.headers.items()}
    return payload, headers


def parse_law_page(html: str) -> dict:
    amended = None
    window = html
    marker = html.find("修正日期")
    if marker >= 0:
        window = html[marker : marker + 400]
    matched = re.search(r"民國\s*(\d+)\s*年\s*(\d+)\s*月\s*(\d+)\s*日", window)
    if matched:
        amended = roc_to_iso(*matched.groups())
    pdfs = []
    for href, title in re.findall(r'href="([^"]*LawGetFile\.ashx[^"]*)"[^>]*>([^<]*)', html):
        url = href.replace("&amp;", "&")
        if url.startswith("/"):
            url = "https://law.moj.gov.tw" + url
        elif url.startswith("LawGetFile"):
            url = "https://law.moj.gov.tw/LawClass/" + url
        pdfs.append({"title": re.sub(r"\s+", "", title), "url": url})
    annex1 = next((item["url"] for item in pdfs if "附表一" in item["title"]), None)
    return {"law_amended_on": amended, "pdfs": pdfs, "annex1_pdf": annex1}


def parse_consumer_counts(html: str) -> dict:
    """Pull whatever totals the consumer page actually prints. Never invent them."""
    text = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    found = {}
    for label, pattern in (
        ("容許量", r"容許量[^0-9]{0,12}(\d{3,6})"),
        ("禁用", r"禁用[^0-9]{0,12}(\d{1,4})"),
        ("免訂", r"免訂[^0-9]{0,12}(\d{1,4})"),
        ("總筆數", r"(?:共|總計|總筆數)\s*(\d{3,6})\s*(?:筆|項)"),
    ):
        matched = re.search(pattern, text)
        if matched:
            found[label] = int(matched.group(1))
    return {"counts": found, "excerpt": text[:500]}


def crosscheck(
    *,
    annex_counts: dict,
    law_html: str | None,
    consumer_html: str | None,
    errors: list[str],
    include_consumer: bool = True,
) -> dict:
    report = {"law_amended_on": None, "annex1_pdf": None, "consumer": None, "mismatches": []}
    if law_html:
        parsed = parse_law_page(law_html)
        report["law_amended_on"] = parsed["law_amended_on"]
        report["annex1_pdf"] = parsed["annex1_pdf"]
        if not parsed["law_amended_on"]:
            report["mismatches"].append("全國法規資料庫頁面未解析到修正日期")
    else:
        report["mismatches"].append("未能下載全國法規資料庫條文，法規修正日缺漏")
    if not include_consumer:
        return report
    if consumer_html:
        parsed_consumer = parse_consumer_counts(consumer_html)
        report["consumer"] = parsed_consumer["counts"]
        consumer_mrl = parsed_consumer["counts"].get("容許量") or parsed_consumer["counts"].get("總筆數")
        if consumer_mrl is not None and consumer_mrl != annex_counts.get("附表一"):
            report["mismatches"].append(
                f"消費者專區容許量筆數 {consumer_mrl} 與開放資料附表一 {annex_counts.get('附表一')} 不一致"
            )
        consumer_ban = parsed_consumer["counts"].get("禁用")
        if consumer_ban is not None and consumer_ban != annex_counts.get("附表四"):
            report["mismatches"].append(
                f"消費者專區禁用筆數 {consumer_ban} 與開放資料附表四 {annex_counts.get('附表四')} 不一致"
            )
        if not parsed_consumer["counts"]:
            report["mismatches"].append("已連上消費者專區，但頁面沒有可解析的筆數，無法完成交叉比對")
    else:
        detail = "連線失敗"
        blob = " ".join(errors)
        if "SSL" in blob or "UNEXPECTED_EOF" in blob:
            detail = "TLS 連線中斷，這次沒有讀到頁面上的筆數"
        elif errors:
            detail = errors[0].split(":", 1)[-1].strip()[:120]
        report["mismatches"].append(f"消費者專區無法交叉比對筆數（{detail}）")
    return report
