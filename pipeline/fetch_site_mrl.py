"""Download consumer-site MRL workbooks with the 匯出 Excel postback.

GET the list page, then POST __EVENTTARGET=ctl00$content$LinkButton1
together with that response's __VIEWSTATE, __VIEWSTATEGENERATOR and
__EVENTVALIDATION. Verified 2026-11-26 against consumer.fda.gov.tw.
"""

from __future__ import annotations

import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://consumer.fda.gov.tw/Law/PesticideList.aspx"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
TABS = {
    1: "site_table1.xlsx",
    7: "site_table2.xlsx",
    2: "site_table3.xlsx",
    3: "site_table4.xlsx",
}


def _hidden(soup: BeautifulSoup, name: str) -> str:
    element = soup.find("input", {"name": name})
    return element.get("value", "") if element else ""


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
        }
    )
    return session


def fetch_tab(session: requests.Session, tk: int, dest: Path) -> dict:
    url = f"{BASE}?nodeID=520&tk={tk}"
    response = session.get(url, timeout=60)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    stated_match = re.search(r"共有\s*<span[^>]*>([\d,]+)</span>\s*筆", response.text)
    if stated_match is None:
        stated_match = re.search(r"共有\s*([\d,]+)\s*筆", response.text)
    stated = int(stated_match.group(1).replace(",", "")) if stated_match else None
    target = "ctl00$content$LinkButton1"
    for anchor in soup.find_all("a"):
        href = anchor.get("href") or ""
        if "匯出Excel" in anchor.get_text(" ", strip=True):
            found = re.search(r"__doPostBack\('([^']+)'", href)
            if found:
                target = found.group(1)
                break
    fields = {
        "__EVENTTARGET": target,
        "__EVENTARGUMENT": "",
        "__LASTFOCUS": "",
        "__VIEWSTATE": _hidden(soup, "__VIEWSTATE"),
        "__VIEWSTATEGENERATOR": _hidden(soup, "__VIEWSTATEGENERATOR"),
        "__EVENTVALIDATION": _hidden(soup, "__EVENTVALIDATION"),
    }
    for name in ("ctl00$content$TagType", "ctl00$content$TagType2", "ctl00$content$icname"):
        if soup.find(attrs={"name": name}) is not None:
            fields[name] = ""
    posted = session.post(
        url,
        data=fields,
        headers={
            "Referer": url,
            "Origin": "https://consumer.fda.gov.tw",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=180,
    )
    posted.raise_for_status()
    if not posted.content.startswith(b"PK"):
        raise RuntimeError(f"tk={tk} 匯出不是 xlsx（{posted.headers.get('Content-Type')}）")
    dest.write_bytes(posted.content)
    return {"tk": tk, "stated_count": stated, "bytes": len(posted.content), "path": str(dest)}


def download_workbooks(dest: Path) -> dict[int, Path]:
    dest.mkdir(parents=True, exist_ok=True)
    session = _session()
    written = {}
    for tk, filename in TABS.items():
        path = dest / filename
        fetch_tab(session, tk, path)
        written[tk] = path
    return written
