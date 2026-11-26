"""Download TFDA tables, diff them, and write the static-site snapshot."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from .build import ANNEX_FILES, SOURCE_URLS, build_database, read_csv
from .consumer import (
    ConsumerUnavailable,
    crosscheck_csv,
    describe_finding,
    fetch_all,
    load_manifest,
    load_snapshot,
    save_snapshot,
)
from .crosscheck import LAW_URL, crosscheck, fetch_bytes, now_taipei
from .diffing import diff_databases
from .footnotes import parse_footnotes
from .pdfrepair import build_repair_map, pdftotext

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
REF_DIR = ROOT / "data" / "reference"
SITE_DIR = ROOT / "data" / "site"
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


def _csv_tables() -> dict[str, list[dict]]:
    return {annex: read_csv(RAW_DIR / filename) for annex, filename in ANNEX_FILES.items()}


def _acquire_site(offline: bool, fetched_at: str) -> tuple[dict | None, bool, str]:
    """Return site tables, stale flag, and error text. Refuse a silent CSV fallback."""
    if offline:
        return load_snapshot(SITE_DIR), False, ""
    try:
        tables, _manifest = fetch_all(SITE_DIR, fetched_at)
        return tables, False, ""
    except Exception as exc:  # noqa: BLE001 — keep the last good snapshot
        message = f"{exc.__class__.__name__}: {exc}"
        previous = load_snapshot(SITE_DIR)
        manifest = load_manifest(SITE_DIR) or {}
        if previous is None:
            raise ConsumerUnavailable(
                "消費者專區無法連線，且沒有可沿用的成功快照。拒絕改用開放資料。"
            ) from exc
        manifest.update({"ok": False, "stale": True, "error": message})
        save_snapshot(SITE_DIR, previous, manifest)
        return previous, True, message


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

    synced_at = now_taipei()
    site_tables, site_stale, site_error = _acquire_site(offline, synced_at)
    old = _read_json(SITE_DATA / "db.json")
    db = build_database(
        RAW_DIR,
        repair_map=repair_map,
        footnotes=footnotes,
        site_tables=site_tables,
    )
    counts = db["meta"]["counts"]
    report = crosscheck(
        annex_counts=counts,
        law_html=download_info.get("law_html") if not offline else None,
        consumer_html=None,
        errors=[],
        include_consumer=False,
    )
    if offline and old and (old.get("meta") or {}).get("law_amended_on"):
        report["law_amended_on"] = old["meta"]["law_amended_on"]

    csv_findings = []
    if site_tables:
        csv_findings = crosscheck_csv(site_tables, _csv_tables())
    csv_messages = [describe_finding(item) for item in csv_findings]
    manifest = load_manifest(SITE_DIR) or {}
    dataset_updated_at = next((value for value in header_dates.values() if value), None)
    db["meta"]["synced_at"] = synced_at
    db["meta"]["dataset_updated_at"] = dataset_updated_at
    if not offline and not dataset_updated_at:
        db["meta"]["warnings"].append(
            "開放資料 CSV 回應沒有 Last-Modified，資料集更新時間無法從表頭取得；請以法規修正日與本次同步時間為準。"
        )
    db["meta"]["law_amended_on"] = report.get("law_amended_on") or (old or {}).get("meta", {}).get("law_amended_on")
    db["meta"]["crosscheck_mismatches"] = list(report.get("mismatches") or []) + csv_messages
    db["meta"]["consumer_counts"] = {name: spec.get("rows") for name, spec in (manifest.get("tables") or {}).items()}
    db["meta"]["primary_source"] = "consumer" if site_tables else "csv"
    db["meta"]["site_stale"] = site_stale
    db["meta"]["site_fetched_at"] = manifest.get("fetched_at")
    db["meta"]["site_error"] = site_error
    if site_stale:
        when = manifest.get("fetched_at") or "未知時間"
        db["meta"]["warnings"].insert(
            0,
            f"消費者專區本次無法連線（{site_error}）。容許量與禁用名單沿用 {when} 的成功下載，沒有改用開放資料 CSV。",
        )
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
    prior_runs = _load_jsonl(SYNC_LOG_PATH)
    prior_cross = set((prior_runs[-1].get("csv_crosscheck") or []) if prior_runs else [])
    for message in csv_messages:
        if message in prior_cross:
            continue
        events.append(
            {
                "date": synced_at[:10],
                "time": synced_at,
                "change": "開放資料差異",
                "pesticide_zh": "",
                "pesticide_en": "",
                "crop": "",
                "field": "交叉比對",
                "old": "",
                "new": message,
                "source": "https://data.fda.gov.tw/data/opendata/export/13/csv",
                "file_hash": (db["meta"].get("hashes") or {}).get("附表一", ""),
                "dataset_updated_at": dataset_updated_at,
                "law_amended_on": db["meta"].get("law_amended_on"),
                "annex": "開放資料",
                "history_key": "",
            }
        )
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
        "primary_source": db["meta"].get("primary_source"),
        "site_stale": site_stale,
        "site_fetched_at": db["meta"].get("site_fetched_at"),
        "site_error": site_error,
        "csv_crosscheck": csv_messages,
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
            "primary_source": db["meta"].get("primary_source"),
            "site_stale": site_stale,
            "site_fetched_at": db["meta"].get("site_fetched_at"),
            "site_error": site_error,
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
