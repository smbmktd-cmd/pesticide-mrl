"""Download the consumer FDA pesticide lists. They override the open-data CSV.

附表一 tk=1, 附表二 tk=7, 附表三 tk=2, 附表四 tk=3.
附表五 is not on this site. If the site cannot be reached, callers must keep
the last successful snapshot and must not substitute CSV values.
"""

from __future__ import annotations

import html
import json
import re
import ssl
import subprocess
import tempfile
import time
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode
from zipfile import ZipFile
from xml.etree import ElementTree

from .normalize import clean_ws, norm_en_key, norm_label, norm_zh_key

BASE = "https://consumer.fda.gov.tw/Law/PesticideList.aspx"
NODE = "520"
TABLES = {
    "附表一": {"tk": "1", "kind": "mrl", "file": "annex1.json"},
    "附表二": {"tk": "7", "kind": "mrl", "file": "annex2.json"},
    "附表三": {"tk": "2", "kind": "exempt", "file": "annex3.json"},
    "附表四": {"tk": "3", "kind": "banned", "file": "annex4.json"},
}
UA = "Mozilla/5.0 (compatible; pesticide-mrl-sync/1.0; +https://github.com/smbmktd-cmd/pesticide-mrl)"

CURL_VARIANTS = [
    ["--http1.1", "--tlsv1.2", "--tls-max", "1.2"],
    ["--http1.1", "--tlsv1.2", "--tls-max", "1.2", "--ciphers", "DEFAULT@SECLEVEL=1"],
    ["--http1.1", "--tls-max", "1.2", "--ciphers", "ALL:@SECLEVEL=0"],
    ["--http1.1"],
    ["--http2", "--tlsv1.3"],
    [],
]


class ConsumerUnavailable(RuntimeError):
    """The consumer site could not be read and there is no snapshot to keep."""


class _HiddenFields(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.fields: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "input":
            return
        data = {key.lower(): value for key, value in attrs}
        if (data.get("type") or "").lower() == "hidden" and data.get("name"):
            self.fields[data["name"]] = data.get("value") or ""


def page_url(tk: str, page: int | None = None) -> str:
    query = f"nodeID={NODE}&tk={tk}"
    if page and page > 1:
        query += f"&p={page}"
    return f"{BASE}?{query}"


def _curl(url: str, extra: list[str], body: bytes | None, cookie: Path, timeout: int) -> bytes:
    cmd = [
        "curl",
        "-fsS",
        "--max-time",
        str(timeout),
        "-A",
        UA,
        "-c",
        str(cookie),
        "-b",
        str(cookie),
        "-D",
        "-",
        *extra,
    ]
    if body is None:
        cmd.append("-L")
    if body is not None:
        cmd += ["-X", "POST", "-H", "Content-Type: application/x-www-form-urlencoded", "--data-binary", "@-"]
    cmd.append(url)
    proc = subprocess.run(
        cmd,
        input=body,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace")[-300:]
        raise RuntimeError(f"curl {proc.returncode}: {err}")
    raw = proc.stdout
    # -D - prepends headers. Split on the header/body boundary.
    marker = raw.find(b"\r\n\r\n")
    if marker == -1:
        marker = raw.find(b"\n\n")
        sep = 2
    else:
        sep = 4
    if marker == -1:
        raise RuntimeError("curl response has no header break")
    header = raw[:marker].decode("utf-8", "replace")
    payload = raw[marker + sep :]
    status = re.search(r"HTTP/\d(?:\.\d)?\s+(\d+)", header)
    code = int(status.group(1)) if status else 0
    if code >= 400:
        raise RuntimeError(f"HTTP {code}")
    return payload


def _urllib(url: str, timeout: int, tls12: bool, seclevel0: bool) -> bytes:
    context = ssl.create_default_context()
    if tls12 and hasattr(ssl, "TLSVersion"):
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.maximum_version = ssl.TLSVersion.TLSv1_2
    if seclevel0:
        try:
            context.set_ciphers("ALL:@SECLEVEL=0")
        except ssl.SSLError:
            context.set_ciphers("DEFAULT@SECLEVEL=1")
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        return response.read()


def fetch_bytes(url: str, *, body: bytes | None = None, timeout: int = 60, attempts: int = 3) -> bytes:
    """GET or POST with several TLS/HTTP settings. Remembers the first one that works."""
    cookie = Path(tempfile.mkstemp(prefix="mrl-cookie-", suffix=".txt")[1])
    errors: list[str] = []
    saved = getattr(fetch_bytes, "_variant", None)
    variants = CURL_VARIANTS
    if saved is not None:
        variants = [saved] + [item for item in CURL_VARIANTS if item != saved]
    try:
        for round_index in range(attempts):
            for extra in variants:
                try:
                    payload = _curl(url, extra, body, cookie, timeout)
                    fetch_bytes._variant = extra  # type: ignore[attr-defined]
                    return payload
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"curl {extra or ['default']}: {exc}")
            if body is None:
                for tls12, sec0 in ((False, False), (True, False), (True, True)):
                    try:
                        return _urllib(url, timeout, tls12, sec0)
                    except Exception as exc:  # noqa: BLE001
                        errors.append(f"urllib tls12={tls12} sec0={sec0}: {exc}")
            if round_index + 1 < attempts:
                time.sleep(1 + round_index * 2)
    finally:
        cookie.unlink(missing_ok=True)
    tail = " | ".join(errors[-4:])
    raise ConsumerUnavailable(f"消費者專區連線失敗（已重試 TLS／HTTP 設定）：{tail}")


def _rows_from_cells(cells: list[tuple[str, str]], kind: str) -> dict | None:
    data = {html.unescape(key).strip(): html.unescape(value).strip() for key, value in cells}
    if "項次" in data and len(data) == 1:
        return None
    if kind == "mrl":
        if "國際普通名稱" not in data and "作物類別" not in data:
            return None
        return {
            "國際普通名稱": clean_ws(data.get("國際普通名稱", "")),
            "普通名稱": clean_ws(data.get("普通名稱", "")),
            "作物類別": clean_ws(data.get("作物類別", "")),
            "容許量ppm": clean_ws(data.get("容許量(ppm)", data.get("容許量ppm", ""))),
            "備註": clean_ws(data.get("備註", "")),
        }
    if kind == "exempt":
        if "農藥名稱" not in data and "英文名稱" not in data:
            return None
        return {
            "農藥名稱": clean_ws(data.get("農藥名稱", "")),
            "英文名稱": clean_ws(data.get("英文名稱", "")),
        }
    if "農藥名稱" not in data and "英文名稱" not in data:
        return None
    return {
        "農藥名稱": clean_ws(data.get("農藥名稱", "")),
        "英文名稱": clean_ws(data.get("英文名稱", "")),
        "禁止製造或輸入日期": clean_ws(
            data.get("禁止製造、輸入日期", data.get("禁止製造或輸入日期", ""))
        ),
        "禁止銷售或使用日期": clean_ws(
            data.get("禁止銷售、使用日期", data.get("禁止銷售或使用日期", ""))
        ),
    }


def parse_list_html(page: str, kind: str) -> tuple[list[dict], int | None]:
    """Return data rows and the stated total (共有 N 筆), if printed."""
    rows: list[dict] = []
    for block in re.findall(r"<tr\b[^>]*>(.*?)</tr>", page, flags=re.I | re.S):
        cells = re.findall(r'data-th="([^"]*)"[^>]*>([^<]*)', block, flags=re.I)
        if not cells:
            cells = [
                (re.sub(r"<[^>]+>", "", label), "")
                for label in re.findall(r"<th\b[^>]*>(.*?)</th>", block, flags=re.I | re.S)
            ]
            if cells and all(not value for _label, value in cells):
                continue
            # header row
            if cells and any("名稱" in label or "項次" in label for label, _value in cells):
                continue
        if "data-th" not in block:
            continue
        parsed = _rows_from_cells(cells, kind)
        if parsed and any(parsed.values()):
            rows.append(parsed)
    total = None
    plain = re.sub(r"<[^>]+>", " ", page)
    matched = re.search(r"共有\s*([0-9,]+)\s*筆", plain)
    if matched:
        total = int(matched.group(1).replace(",", ""))
    return rows, total


def last_page(page: str, tk: str) -> int | None:
    numbers = [int(item) for item in re.findall(rf"tk={tk}(?:&amp;|&)p=(\d+)", page)]
    return max(numbers) if numbers else None


def parse_xlsx(payload: bytes, kind: str) -> list[dict]:
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    blob = tempfile.SpooledTemporaryFile()
    blob.write(payload)
    blob.seek(0)
    with ZipFile(blob) as book:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in book.namelist():
            root = ElementTree.fromstring(book.read("xl/sharedStrings.xml"))
            for item in root.findall("m:si", ns):
                shared.append("".join(node.text or "" for node in item.iter(f"{{{ns['m']}}}t")))
        sheet_name = "xl/worksheets/sheet1.xml"
        root = ElementTree.fromstring(book.read(sheet_name))
        table: list[list[str]] = []
        for row in root.findall("m:sheetData/m:row", ns):
            values: list[str] = []
            for cell in row.findall("m:c", ns):
                ref = cell.attrib.get("r", "A")
                col = re.match(r"[A-Z]+", ref)
                index = 0
                if col:
                    for ch in col.group(0):
                        index = index * 26 + (ord(ch) - 64)
                    index -= 1
                while len(values) < index:
                    values.append("")
                kind_attr = cell.attrib.get("t")
                node = cell.find("m:v", ns)
                inline = cell.find("m:is", ns)
                if kind_attr == "s" and node is not None and node.text:
                    text = shared[int(node.text)]
                elif kind_attr == "inlineStr" and inline is not None:
                    text = "".join(part.text or "" for part in inline.iter(f"{{{ns['m']}}}t"))
                else:
                    text = node.text if node is not None and node.text else ""
                values.append(text)
            table.append(values)
    if not table:
        return []
    header = [clean_ws(item) for item in table[0]]
    rows = []
    for values in table[1:]:
        cells = [(header[i], values[i] if i < len(values) else "") for i in range(len(header))]
        parsed = _rows_from_cells(cells, kind)
        if parsed and any(parsed.values()):
            rows.append(parsed)
    return rows


def _looks_like_export(payload: bytes, kind: str, total: int | None) -> list[dict] | None:
    if payload.startswith(b"PK"):
        try:
            rows = parse_xlsx(payload, kind)
        except Exception:
            return None
    else:
        text = payload.decode("utf-8", "replace")
        if "<tr" not in text.lower():
            return None
        rows, _stated = parse_list_html(text, kind)
    if total and len(rows) + 5 < total:
        return None
    if len(rows) < 1:
        return None
    return rows


def _hidden_fields(page: str) -> dict[str, str]:
    parser = _HiddenFields()
    parser.feed(page)
    return parser.fields


def fetch_table(tk: str, kind: str) -> tuple[list[dict], str, int | None]:
    first = fetch_bytes(page_url(tk, 1)).decode("utf-8", "replace")
    rows, total = parse_list_html(first, kind)
    if not rows and total is None:
        raise ConsumerUnavailable(f"tk={tk} 首頁沒有表格，可能不是農藥清單")
    per_page = max(len(rows), 1)
    expected_pages = last_page(first, tk) or (1 if not total else (total + per_page - 1) // per_page)
    fields = _hidden_fields(first)
    if fields:
        fields["__EVENTTARGET"] = "ctl00$content$LinkButton1"
        fields["__EVENTARGUMENT"] = ""
        body = urlencode(fields).encode("utf-8")
        try:
            exported = fetch_bytes(page_url(tk, 1), body=body, timeout=180, attempts=2)
            parsed = _looks_like_export(exported, kind, total)
            if parsed and (total is None or len(parsed) >= total):
                return parsed, "excel", total
        except ConsumerUnavailable:
            pass
    if expected_pages <= 1:
        return rows, "page", total
    found: dict[int, list[dict]] = {1: rows}

    def load(page: int) -> tuple[int, list[dict]]:
        text = fetch_bytes(page_url(tk, page)).decode("utf-8", "replace")
        page_rows, _stated = parse_list_html(text, kind)
        if not page_rows:
            raise ConsumerUnavailable(f"tk={tk} 第 {page} 頁沒有資料列")
        return page, page_rows

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(load, page) for page in range(2, expected_pages + 1)]
        for future in as_completed(futures):
            page, page_rows = future.result()
            found[page] = page_rows
    ordered: list[dict] = []
    for page in range(1, expected_pages + 1):
        ordered.extend(found[page])
    if total is not None and len(ordered) != total:
        raise ConsumerUnavailable(f"tk={tk} 抓到 {len(ordered)} 列，頁面寫共有 {total} 筆")
    return ordered, "pages", total


def snapshot_path(site_dir: Path, annex: str) -> Path:
    return site_dir / TABLES[annex]["file"]


def _normalize_site_row(row: dict, kind: str) -> dict:
    if kind == "mrl":
        limit = row.get("容許量ppm") or row.get("容許量(ppm)") or ""
        return {
            "國際普通名稱": clean_ws(row.get("國際普通名稱") or ""),
            "普通名稱": clean_ws(row.get("普通名稱") or ""),
            "作物類別": clean_ws(row.get("作物類別") or ""),
            "容許量ppm": clean_ws(str(limit)),
            "備註": clean_ws(row.get("備註") or ""),
        }
    if kind == "exempt":
        return {
            "農藥名稱": clean_ws(row.get("農藥名稱") or ""),
            "英文名稱": clean_ws(row.get("英文名稱") or ""),
        }
    return {
        "農藥名稱": clean_ws(row.get("農藥名稱") or ""),
        "英文名稱": clean_ws(row.get("英文名稱") or ""),
        "禁止製造或輸入日期": clean_ws(
            row.get("禁止製造或輸入日期") or row.get("禁止製造、輸入日期") or ""
        ),
        "禁止銷售或使用日期": clean_ws(
            row.get("禁止銷售或使用日期") or row.get("禁止銷售、使用日期") or ""
        ),
    }


def read_site_csv(path: Path, kind: str) -> list[dict]:
    import csv

    with path.open(encoding="utf-8-sig", newline="") as handle:
        return [_normalize_site_row(row, kind) for row in csv.DictReader(handle)]


def load_snapshot(site_dir: Path) -> dict[str, list[dict]] | None:
    if not (site_dir / "site_table1.csv").exists() and not (site_dir / "annex1.json").exists():
        return None
    loaded = {}
    for annex, spec in TABLES.items():
        csv_path = site_dir / f"site_table{ {'附表一':'1','附表二':'2','附表三':'3','附表四':'4'}[annex] }.csv"
        json_path = site_dir / spec["file"]
        if csv_path.exists():
            loaded[annex] = read_site_csv(csv_path, spec["kind"])
        elif json_path.exists():
            loaded[annex] = json.loads(json_path.read_text(encoding="utf-8"))
        else:
            return None
    return loaded


def load_annex5_rows(site_dir: Path) -> list[dict] | None:
    path = site_dir / "site_table5.csv"
    if not path.exists():
        return None
    import csv

    rows = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            rows.append(
                {
                    "類別": re.sub(r"^\d+\.\s*", "", (row.get("類別") or "").strip()),
                    "農作物類農產品": row.get("農作物類農產品") or "",
                }
            )
    return rows


def load_manifest(site_dir: Path) -> dict | None:
    path = site_dir / "manifest.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_snapshot(site_dir: Path, tables: dict[str, list[dict]], manifest: dict) -> None:
    site_dir.mkdir(parents=True, exist_ok=True)
    for annex, rows in tables.items():
        path = snapshot_path(site_dir, annex)
        path.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    (site_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def fetch_all(site_dir: Path, fetched_at: str) -> tuple[dict[str, list[dict]], dict]:
    """Primary path: 匯出 Excel postback. Writes site_tableN.csv and keeps 附表五."""
    import csv

    from .fetch_site_mrl import download_workbooks

    site_dir.mkdir(parents=True, exist_ok=True)
    books = download_workbooks(site_dir)
    kind_by_tk = {1: "mrl", 7: "mrl", 2: "exempt", 3: "banned"}
    file_by_tk = {1: "site_table1.csv", 7: "site_table2.csv", 2: "site_table3.csv", 3: "site_table4.csv"}
    for tk, path in books.items():
        parsed = parse_xlsx(path.read_bytes(), kind_by_tk[tk])
        with (site_dir / file_by_tk[tk]).open("w", encoding="utf-8", newline="") as handle:
            fields = list(parsed[0].keys()) if parsed else []
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(parsed)
    tables = load_snapshot(site_dir)
    if not tables or len(tables.get("附表一") or []) < 1000:
        raise ConsumerUnavailable("匯出 Excel 沒有取得附表一")
    methods = {
        annex: {"rows": len(rows), "method": "excel-postback", "tk": TABLES[annex]["tk"]}
        for annex, rows in tables.items()
    }
    manifest = {
        "fetched_at": fetched_at,
        "ok": True,
        "stale": False,
        "error": "",
        "source": BASE,
        "method": "匯出Excel postback __EVENTTARGET=ctl00$content$LinkButton1",
        "tables": methods,
    }
    (site_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for annex, rows in tables.items():
        print(f"{annex} {len(rows)} 列 via excel-postback", flush=True)
    return tables, manifest


def resolve_primary(fetched: dict | None, previous: dict | None) -> tuple[dict, bool]:
    """Return (tables, stale). Never substitutes another dataset."""
    if fetched is not None:
        return fetched, False
    if previous is not None:
        return previous, True
    raise ConsumerUnavailable("消費者專區無法連線，且沒有可沿用的成功快照。拒絕改用開放資料。")


def _limits_differ(csv_value: str, site_value: str) -> bool:
    from decimal import Decimal, InvalidOperation

    left = clean_ws(csv_value).replace("＊", "*")
    right = clean_ws(site_value).replace("＊", "*")
    if left.endswith("*") != right.endswith("*"):
        return True
    try:
        return Decimal(left.rstrip("*") or "0") != Decimal(right.rstrip("*") or "0")
    except InvalidOperation:
        return left != right


def _loose_crop(text: str) -> str:
    import unicodedata

    folded = unicodedata.normalize("NFKC", norm_label(text))
    return folded.replace("﹙", "(").replace("﹚", ")").replace("﹝", "(").replace("﹞", ")")


def _crop_match(csv_crop: str, site_crop: str) -> bool:
    left = _loose_crop(csv_crop)
    right = _loose_crop(site_crop)
    if left == right:
        return True
    if "?" not in left or left.count("?") > 4:
        return False
    pattern = "^" + "".join("." if char == "?" else re.escape(char) for char in left) + "$"
    return re.fullmatch(pattern, right) is not None


def _mrl_key(row: dict) -> tuple[str, str]:
    return (norm_en_key(row.get("國際普通名稱") or ""), _loose_crop(row.get("作物類別") or ""))


def _name_key(row: dict) -> tuple[str, str]:
    return (norm_en_key(row.get("英文名稱") or ""), norm_zh_key(row.get("農藥名稱") or ""))


def crosscheck_csv(site_tables: dict[str, list[dict]], csv_tables: dict[str, list[dict]]) -> list[dict]:
    """Rows the open-data CSV does not match. Site values stay in effect."""
    findings = []
    for annex, spec in TABLES.items():
        site_rows = site_tables.get(annex) or []
        csv_rows = csv_tables.get(annex) or []
        if spec["kind"] == "mrl":
            buckets: dict[str, list[dict]] = {}
            for row in csv_rows:
                buckets.setdefault(norm_en_key(row.get("國際普通名稱") or ""), []).append(row)
            used: dict[str, set[int]] = {}
            for row in site_rows:
                key = norm_en_key(row.get("國際普通名稱") or "")
                pool = buckets.get(key) or []
                chosen = None
                for index, other in enumerate(pool):
                    if index in used.setdefault(key, set()):
                        continue
                    if _crop_match(other.get("作物類別") or "", row.get("作物類別") or ""):
                        chosen = index
                        break
                if chosen is None:
                    findings.append({"annex": annex, "diff": "開放資料缺少", "site": row, "csv": None})
                    continue
                used[key].add(chosen)
                other = pool[chosen]
                if _limits_differ(other.get("容許量ppm") or "", row.get("容許量ppm") or ""):
                    findings.append({"annex": annex, "diff": "限值不同", "site": row, "csv": other})
            for key, pool in buckets.items():
                for index, other in enumerate(pool):
                    if index not in used.get(key, set()):
                        findings.append({"annex": annex, "diff": "開放資料多出未採用", "site": None, "csv": other})
            continue
        key_fn = _name_key
        csv_bag = Counter(key_fn(row) for row in csv_rows)
        site_bag = Counter(key_fn(row) for row in site_rows)
        seen = Counter()
        for row in site_rows:
            key = key_fn(row)
            seen[key] += 1
            if seen[key] > csv_bag.get(key, 0):
                findings.append({"annex": annex, "diff": "開放資料缺少", "site": row, "csv": None})
        seen_csv = Counter()
        for row in csv_rows:
            key = key_fn(row)
            seen_csv[key] += 1
            if seen_csv[key] > site_bag.get(key, 0):
                findings.append({"annex": annex, "diff": "開放資料多出未採用", "site": None, "csv": row})
    return findings


def describe_finding(item: dict) -> str:
    annex = item["annex"]
    kind = item["diff"]
    row = item["site"] or item["csv"] or {}
    if "作物類別" in row:
        who = f"{row.get('普通名稱') or row.get('國際普通名稱')}／{row.get('國際普通名稱')}／{row.get('作物類別')}／{row.get('容許量ppm')} ppm"
    else:
        who = f"{row.get('農藥名稱')}／{row.get('英文名稱')}"
    if kind == "限值不同" and item["csv"] and item["site"]:
        return (
            f"{annex}{kind}（採用消費者專區）：{who}；"
            f"開放資料為 {item['csv'].get('容許量ppm')} ppm"
        )
    if kind == "開放資料缺少":
        return f"{annex}{kind}（已補進消費者專區）：{who}"
    return f"{annex}{kind}：{who}"
