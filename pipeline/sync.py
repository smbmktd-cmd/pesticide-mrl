"""Download TFDA tables, diff them, and write the static-site snapshot."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from .build import SOURCE_URLS, build_database
from .crosscheck import CONSUMER_URL, LAW_URL, crosscheck, fetch_bytes, now_taipei
from .diffing import diff_databases
from .footnotes import parse_footnotes
from .pdfrepair import build_repair_map, pdftotext

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
REF_DIR = ROOT / "data" / "reference"
SITE_DATA = ROOT / "site" / "data"
HISTORY_PATH = ROOT / "data" / "history.jsonl"
SYNC_LOG_PATH = ROOT / "data" / "sync-runs.jsonl"

CSV_URLS = {name: SOURCE_URLS[name] for name in ("附表一", "附表二", "附表三", "附表四", "附表五")}
CSV_NAMES = {
    "附表一": "annex1.csv",
    "附表二": "annex2.csv",
    "附表三": "annex3.csv",
    "附表四": "annex4.csv",
    "附表五": "annex5.csv",
}


def _read_json(path: Path):
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, payload, pretty: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        if pretty:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        else:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")


def _append_jsonl(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _download_csvs() -> dict[str, str | None]:
    updated = {}
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    for annex, url in CSV_URLS.items():
        payload, headers = fetch_bytes(url)
        path = RAW_DIR / CSV_NAMES[annex]
        path.write_bytes(payload)
        updated[annex] = headers.get("last-modified")
    return updated


def _download_references() -> tuple[dict | None, list | None, dict]:
    """Law page, annex PDF repair map, footnotes. Failures become warnings."""
    info = {"errors": [], "law_html": None, "consumer_html": None, "pdf_url": None}
    law_html = None
    try:
        law_html, _headers = fetch_bytes(LAW_URL)
        law_html = law_html.decode("utf-8", errors="replace")
        info["law_html"] = law_html
    except Exception as exc:  # noqa: BLE001 — record and continue
        info["errors"].append(f"法規頁面：{exc.__class__.__name__}: {exc}")
    try:
        consumer_html, _headers = fetch_bytes(CONSUMER_URL, timeout=25)
        info["consumer_html"] = consumer_html.decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        info["errors"].append(f"消費者專區：{exc.__class__.__name__}: {exc}")

    repair_map = None
    footnotes = None
    pdf_url = None
    if law_html:
        from .crosscheck import parse_law_page

        pdf_url = parse_law_page(law_html).get("annex1_pdf")
        info["pdf_url"] = pdf_url
    if pdf_url:
        try:
            pdf_bytes, pdf_headers = fetch_bytes(pdf_url, timeout=90)
            pdf_path = REF_DIR / "annex1.pdf"
            REF_DIR.mkdir(parents=True, exist_ok=True)
            pdf_path.write_bytes(pdf_bytes)
            info["pdf_last_modified"] = pdf_headers.get("last-modified")
            layout = pdftotext(str(pdf_path), layout=True)
            plain = pdftotext(str(pdf_path), layout=False)
            import csv

            with (RAW_DIR / "annex1.csv").open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            repair_map = build_repair_map(layout, rows)
            footnotes = parse_footnotes(plain)
            _write_json(REF_DIR / "repair-map.json", repair_map, pretty=True)
            _write_json(REF_DIR / "footnotes.json", footnotes, pretty=True)
        except Exception as exc:  # noqa: BLE001
            info["errors"].append(f"附表一 PDF：{exc.__class__.__name__}: {exc}")
    return repair_map, footnotes, info


def _load_cached_references() -> tuple[dict | None, list | None]:
    repair_map = _read_json(REF_DIR / "repair-map.json")
    footnotes = _read_json(REF_DIR / "footnotes.json")
    return repair_map, footnotes


def run_sync(*, offline: bool = False, trigger: str = "manual") -> dict:
    header_dates = {}
    download_info = {"errors": []}
    if offline:
        repair_map, footnotes = _load_cached_references()
    else:
        header_dates = _download_csvs()
        repair_map, footnotes, download_info = _download_references()
        if repair_map is None or footnotes is None:
            cached_map, cached_notes = _load_cached_references()
            repair_map = repair_map or cached_map
            footnotes = footnotes or cached_notes

    old = _read_json(SITE_DATA / "db.json")
    db = build_database(RAW_DIR, repair_map=repair_map, footnotes=footnotes)
    counts = db["meta"]["counts"]
    report = crosscheck(
        annex_counts=counts,
        law_html=download_info.get("law_html") if not offline else None,
        consumer_html=download_info.get("consumer_html") if not offline else None,
        errors=download_info.get("errors") or [],
    )
    if offline and old and (old.get("meta") or {}).get("law_amended_on"):
        report["law_amended_on"] = old["meta"]["law_amended_on"]
        report["mismatches"] = [
            item
            for item in (old.get("meta") or {}).get("crosscheck_mismatches") or []
            if "無法" not in item and "未能" not in item
        ]
        # 離線重建沿用上一筆交叉檢查，不把「沒有重抓」寫成新的法規缺漏。
        if not report["mismatches"]:
            report["mismatches"] = list((old.get("meta") or {}).get("crosscheck_mismatches") or [])

    synced_at = now_taipei()
    dataset_updated_at = next((value for value in header_dates.values() if value), None)
    db["meta"]["synced_at"] = synced_at
    db["meta"]["dataset_updated_at"] = dataset_updated_at
    if not offline and not dataset_updated_at:
        db["meta"]["warnings"].append(
            "開放資料 CSV 回應沒有 Last-Modified，資料集更新時間無法從表頭取得；請以法規修正日與本次同步時間為準。"
        )
    db["meta"]["law_amended_on"] = report.get("law_amended_on") or (old or {}).get("meta", {}).get("law_amended_on")
    db["meta"]["crosscheck_mismatches"] = report.get("mismatches") or []
    db["meta"]["consumer_counts"] = report.get("consumer")
    db["meta"]["annex1_pdf"] = report.get("annex1_pdf") or download_info.get("pdf_url")
    db["meta"]["repair_stats"] = (repair_map or {}).get("stats")
    db["meta"]["trigger"] = trigger
    for item in db["meta"]["crosscheck_mismatches"]:
        if item not in db["meta"]["warnings"]:
            db["meta"]["warnings"].append(item)
    for item in download_info.get("errors") or []:
        if "消費者專區" in item and any("消費者專區" in text for text in db["meta"]["crosscheck_mismatches"]):
            continue
        warning = f"下載警告：{item}"
        if warning not in db["meta"]["warnings"]:
            db["meta"]["warnings"].append(warning)

    events = diff_databases(old, db)
    if events:
        _append_jsonl(HISTORY_PATH, events)
    history = _load_jsonl(HISTORY_PATH)
    diff_counts = dict(Counter(event["change"] for event in events))
    sync_entry = {
        "ran_at": synced_at,
        "trigger": trigger,
        "offline": offline,
        "counts": counts,
        "diff_counts": diff_counts,
        "diff_total": len(events),
        "warnings": db["meta"]["warnings"],
        "crosscheck_mismatches": db["meta"]["crosscheck_mismatches"],
        "hashes": db["meta"]["hashes"],
        "law_amended_on": db["meta"].get("law_amended_on"),
        "dataset_updated_at": dataset_updated_at,
        "initial": old is None,
    }
    if old is None:
        sync_entry["note"] = "初始建立，無前期可比對，不把既有列記成新增"
    _append_jsonl(SYNC_LOG_PATH, [sync_entry])
    runs = _load_jsonl(SYNC_LOG_PATH)

    _write_json(SITE_DATA / "db.json", db, pretty=False)
    _write_json(
        SITE_DATA / "meta.json",
        {
            "synced_at": db["meta"].get("synced_at"),
            "law_amended_on": db["meta"].get("law_amended_on"),
            "dataset_updated_at": db["meta"].get("dataset_updated_at"),
            "counts": counts,
            "warnings": db["meta"]["warnings"],
            "crosscheck_mismatches": db["meta"]["crosscheck_mismatches"],
            "sources": db["meta"]["sources"],
            "hashes": db["meta"]["hashes"],
            "consumer_counts": db["meta"].get("consumer_counts"),
            "repair_stats": db["meta"].get("repair_stats"),
        },
        pretty=True,
    )
    _write_json(SITE_DATA / "history.json", history, pretty=False)
    _write_json(SITE_DATA / "sync-log.json", runs[-120:], pretty=False)
    return sync_entry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="同步農藥殘留容許量開放資料")
    parser.add_argument("--offline", action="store_true", help="用 data/raw 與既有修復表重建，不連網")
    parser.add_argument("--trigger", default="manual")
    args = parser.parse_args(argv)
    entry = run_sync(offline=args.offline, trigger=args.trigger)
    print(json.dumps(entry, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
