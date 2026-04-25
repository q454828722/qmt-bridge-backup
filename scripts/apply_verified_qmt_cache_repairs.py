#!/usr/bin/env python3
"""Apply verified external-source repairs to QMT-derived cache artifacts.

This script deliberately avoids writing arbitrary third-party records into
xtdata's private cache files. It performs two safe operations:

1. Patch verified instrument metadata in the project QMT-derived CSV cache.
2. Build a symbol list for QMT's native ``download_financial_data2`` refresh.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent.parent
BASIC_DIR = ROOT / "data" / "yuanqi_replica" / "basic"
DEFAULT_OPEN_DATE_CANDIDATES = BASIC_DIR / "quant_open_date_repair_candidates.csv"
DEFAULT_INSTRUMENT_DETAILS = BASIC_DIR / "instrument_details.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply verified external-source repairs to QMT-derived cache artifacts",
    )
    parser.add_argument(
        "--gm-overlay",
        default="",
        help="GM cross-source overlay CSV. Defaults to the newest gm_cross_source_repair_overlay.csv",
    )
    parser.add_argument("--basic-dir", default=str(BASIC_DIR))
    parser.add_argument("--output-dir", default="")
    parser.add_argument(
        "--open-date-candidates",
        default=str(DEFAULT_OPEN_DATE_CANDIDATES),
        help="Public open-date candidates, normally Eastmoney-derived.",
    )
    parser.add_argument(
        "--instrument-details",
        default=str(DEFAULT_INSTRUMENT_DETAILS),
        help="QMT-derived instrument_details.csv to patch when --apply-open-date-repairs is set.",
    )
    parser.add_argument(
        "--manual-financial-validation-file",
        default="",
        help="Optional CSV with manually verified public financial dates.",
    )
    parser.add_argument(
        "--apply-open-date-repairs",
        action="store_true",
        help="Actually patch verified open_date values into instrument_details.csv.",
    )
    parser.add_argument(
        "--skip-public-financial-query",
        action="store_true",
        help="Do not query Eastmoney for single-source financial rows.",
    )
    parser.add_argument("--request-delay", type=float, default=0.08)
    parser.add_argument("--timeout", type=int, default=15)
    return parser.parse_args()


def clean_date(value: object) -> str:
    text = "" if value is None else str(value)
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(rows: list[dict[str, str]], path: Path, fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def latest_gm_overlay_path() -> Path:
    candidates = list(
        ROOT.glob(
            "research/output/data_cleaning/*/external_evidence/"
            "*_gm_fallback_validation/summary/gm_cross_source_repair_overlay.csv"
        )
    )
    if not candidates:
        raise FileNotFoundError("No gm_cross_source_repair_overlay.csv found under research/output")
    return max(candidates, key=lambda item: (csv_row_count(item), item.stat().st_mtime))


def csv_row_count(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return max(sum(1 for _ in handle) - 1, 0)
    except OSError:
        return 0


def default_output_dir(overlay_path: Path) -> Path:
    try:
        parent_run = overlay_path.parents[3]
    except IndexError:
        parent_run = ROOT / "research" / "output" / "data_cleaning"
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S_verified_qmt_cache_apply")
    return parent_run / "qmt_cache_apply" / run_id


def index_by_stock(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {row.get("stock_code", "").strip().upper(): row for row in rows if row.get("stock_code")}


def resolve_open_date_repair(
    *,
    overlay_row: dict[str, str],
    eastmoney_row: dict[str, str] | None,
    current_open_date: str,
) -> dict[str, str]:
    stock_code = overlay_row.get("stock_code", "").strip().upper()
    gm_date = clean_date(overlay_row.get("gm_list_date"))
    akshare_date = clean_date(overlay_row.get("akshare_list_date"))
    eastmoney_date = clean_date((eastmoney_row or {}).get("public_open_date"))
    current_date = clean_date(current_open_date)

    verified_date = ""
    source_chain: list[str] = []
    validation_level = "unverified"
    conflict_note = ""

    if gm_date and akshare_date and gm_date == akshare_date:
        verified_date = gm_date
        source_chain = ["gm", "akshare"]
        validation_level = "gm_akshare_agree_open_date"
        if eastmoney_date == gm_date:
            source_chain.append("eastmoney")
            validation_level = "gm_akshare_eastmoney_agree_open_date"
    elif gm_date and eastmoney_date and gm_date == eastmoney_date:
        verified_date = gm_date
        source_chain = ["gm", "eastmoney"]
        validation_level = "gm_eastmoney_agree_open_date"
        if akshare_date and akshare_date != gm_date:
            conflict_note = f"akshare_conflict:{akshare_date}"
            validation_level = "gm_eastmoney_resolve_akshare_conflict_open_date"
    elif akshare_date and eastmoney_date and akshare_date == eastmoney_date:
        verified_date = akshare_date
        source_chain = ["akshare", "eastmoney"]
        validation_level = "akshare_eastmoney_agree_open_date"

    apply_flag = "0"
    decision = "hold"
    if verified_date:
        if current_date in {"", "0", "00000000"}:
            apply_flag = "1"
            decision = "patch_qmt_derived_instrument_cache"
        elif current_date == verified_date:
            decision = "already_consistent"
        else:
            decision = "hold_current_nonzero_conflict"
            conflict_note = f"{conflict_note};current_conflict:{current_date}".strip(";")

    return {
        "stock_code": stock_code,
        "name": overlay_row.get("name", ""),
        "current_open_date": current_date,
        "verified_open_date": verified_date,
        "gm_list_date": gm_date,
        "akshare_list_date": akshare_date,
        "eastmoney_list_date": eastmoney_date,
        "validation_level": validation_level,
        "source_chain": ",".join(source_chain),
        "apply": apply_flag,
        "decision": decision,
        "conflict_note": conflict_note,
    }


def fetch_eastmoney_financial_dates(stock_code: str, timeout: int) -> dict[str, str]:
    params = {
        "reportName": "RPT_F10_FINANCE_MAINFINADATA",
        "columns": "SECUCODE,SECURITY_NAME_ABBR,REPORT_DATE,REPORT_DATE_NAME,NOTICE_DATE",
        "filter": f'(SECUCODE="{stock_code}")',
        "pageNumber": "1",
        "pageSize": "3",
        "sortColumns": "REPORT_DATE",
        "sortTypes": "-1",
    }
    url = "https://datacenter.eastmoney.com/securities/api/data/v1/get?" + urlencode(params)
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://emweb.securities.eastmoney.com/",
        },
    )
    with urlopen(req, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    rows = (payload.get("result") or {}).get("data") or []
    if not rows:
        return {"status": "empty", "source_url": url}
    latest = rows[0]
    return {
        "status": "success",
        "latest_report": clean_date(latest.get("REPORT_DATE")),
        "latest_notice": clean_date(latest.get("NOTICE_DATE")),
        "source_url": url,
    }


def gm_latest_dates(row: dict[str, str]) -> tuple[str, str]:
    report_dates = [
        clean_date(row.get("gm_balance_latest_report")),
        clean_date(row.get("gm_income_latest_report")),
        clean_date(row.get("gm_cashflow_latest_report")),
    ]
    notice_dates = [
        clean_date(row.get("gm_balance_latest_announce")),
        clean_date(row.get("gm_income_latest_announce")),
        clean_date(row.get("gm_cashflow_latest_announce")),
    ]
    return max(report_dates), max(notice_dates)


def load_manual_financial_validations(path: str) -> dict[str, dict[str, str]]:
    if not path:
        return {}
    manual_path = Path(path)
    if not manual_path.exists():
        return {}
    return index_by_stock(read_csv(manual_path))


def resolve_financial_validation(
    row: dict[str, str],
    eastmoney: dict[str, str],
    manual: dict[str, str] | None,
) -> dict[str, str]:
    stock_code = row.get("stock_code", "").strip().upper()
    gm_report, gm_notice = gm_latest_dates(row)
    prior_sources = [item.strip() for item in row.get("prior_success_sources", "").split(",") if item.strip()]
    original_level = row.get("validation_level", "")

    source_chain = ["gm"]
    validation_level = original_level or "unverified"
    decision = "hold"
    validated = "0"
    notes = ""

    if original_level == "gm_plus_prior_external_sources":
        source_chain.extend(prior_sources)
        validated = "1"
        decision = "qmt_native_financial_refresh"
    elif original_level == "gm_single_external_source":
        em_report = eastmoney.get("latest_report", "")
        em_notice = eastmoney.get("latest_notice", "")
        if eastmoney.get("status") == "success" and em_report == gm_report and em_notice == gm_notice:
            source_chain.append("eastmoney")
            validation_level = "gm_eastmoney_agree_latest_financial"
            validated = "1"
            decision = "qmt_native_financial_refresh"
        elif manual:
            manual_report = clean_date(manual.get("latest_report"))
            manual_notice = clean_date(manual.get("latest_notice"))
            if manual_report == gm_report and manual_notice == gm_notice:
                source_chain.append(manual.get("public_source", "manual_public_source"))
                validation_level = "gm_manual_public_agree_latest_financial"
                validated = "1"
                decision = "qmt_native_financial_refresh"
                notes = manual.get("notes", "")
        if validated != "1":
            notes = notes or "single source not fully confirmed by public source"

    return {
        "stock_code": stock_code,
        "name": row.get("name", ""),
        "original_validation_level": original_level,
        "validation_level": validation_level,
        "validated": validated,
        "decision": decision,
        "source_chain": ",".join(source_chain),
        "gm_latest_report": gm_report,
        "gm_latest_notice": gm_notice,
        "eastmoney_status": eastmoney.get("status", ""),
        "eastmoney_latest_report": eastmoney.get("latest_report", ""),
        "eastmoney_latest_notice": eastmoney.get("latest_notice", ""),
        "eastmoney_source_url": eastmoney.get("source_url", ""),
        "qmt_balance_records": row.get("qmt_balance_records", ""),
        "qmt_income_records": row.get("qmt_income_records", ""),
        "qmt_cashflow_records": row.get("qmt_cashflow_records", ""),
        "qmt_latest_announce": row.get("qmt_latest_announce", ""),
        "notes": notes,
    }


def main() -> int:
    args = parse_args()
    overlay_path = Path(args.gm_overlay) if args.gm_overlay else latest_gm_overlay_path()
    output_dir = Path(args.output_dir) if args.output_dir else default_output_dir(overlay_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    basic_dir = Path(args.basic_dir)
    instrument_path = Path(args.instrument_details)
    open_candidates_path = Path(args.open_date_candidates)

    overlay_rows = read_csv(overlay_path)
    instrument_rows = read_csv(instrument_path)
    instrument_fieldnames = list(instrument_rows[0].keys()) if instrument_rows else []
    instrument_by_stock = index_by_stock(instrument_rows)
    eastmoney_open_by_stock = index_by_stock(read_csv(open_candidates_path))

    open_repairs = []
    for row in overlay_rows:
        if row.get("category") != "instrument_open_date_invalid":
            continue
        stock_code = row.get("stock_code", "").strip().upper()
        current_open_date = instrument_by_stock.get(stock_code, {}).get("open_date", row.get("qmt_open_date", ""))
        open_repairs.append(
            resolve_open_date_repair(
                overlay_row=row,
                eastmoney_row=eastmoney_open_by_stock.get(stock_code),
                current_open_date=current_open_date,
            )
        )

    open_repair_path = output_dir / "verified_open_date_repairs.csv"
    open_fields = [
        "stock_code",
        "name",
        "current_open_date",
        "verified_open_date",
        "gm_list_date",
        "akshare_list_date",
        "eastmoney_list_date",
        "validation_level",
        "source_chain",
        "apply",
        "decision",
        "conflict_note",
    ]
    write_csv(open_repairs, open_repair_path, open_fields)

    patched_open_dates = 0
    backup_path = ""
    if args.apply_open_date_repairs:
        backup = output_dir / f"{instrument_path.stem}.before_verified_open_date_repair.csv"
        shutil.copy2(instrument_path, backup)
        backup_path = str(backup)
        repairs_by_stock = {row["stock_code"]: row for row in open_repairs if row["apply"] == "1"}
        for row in instrument_rows:
            stock_code = row.get("stock_code", "").strip().upper()
            repair = repairs_by_stock.get(stock_code)
            if repair:
                row["open_date"] = repair["verified_open_date"]
                patched_open_dates += 1
        write_csv(instrument_rows, instrument_path, instrument_fieldnames)

    manual_financial = load_manual_financial_validations(args.manual_financial_validation_file)
    financial_validations = []
    for row in overlay_rows:
        if row.get("category") != "financial_incomplete_after_qmt_refresh":
            continue
        stock_code = row.get("stock_code", "").strip().upper()
        eastmoney: dict[str, str] = {}
        if row.get("validation_level") == "gm_single_external_source" and not args.skip_public_financial_query:
            try:
                eastmoney = fetch_eastmoney_financial_dates(stock_code, timeout=args.timeout)
            except Exception as exc:
                eastmoney = {"status": "error", "error": repr(exc)}
            if args.request_delay > 0:
                time.sleep(args.request_delay)
        financial_validations.append(
            resolve_financial_validation(row, eastmoney, manual_financial.get(stock_code))
        )

    financial_path = output_dir / "verified_financial_refresh_candidates.csv"
    financial_fields = [
        "stock_code",
        "name",
        "original_validation_level",
        "validation_level",
        "validated",
        "decision",
        "source_chain",
        "gm_latest_report",
        "gm_latest_notice",
        "eastmoney_status",
        "eastmoney_latest_report",
        "eastmoney_latest_notice",
        "eastmoney_source_url",
        "qmt_balance_records",
        "qmt_income_records",
        "qmt_cashflow_records",
        "qmt_latest_announce",
        "notes",
    ]
    write_csv(financial_validations, financial_path, financial_fields)

    refresh_symbols = sorted(row["stock_code"] for row in financial_validations if row["validated"] == "1")
    refresh_symbols_path = output_dir / "qmt_financial_refresh_symbols.txt"
    refresh_symbols_path.write_text("\n".join(refresh_symbols) + ("\n" if refresh_symbols else ""), encoding="utf-8")

    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "gm_overlay": str(overlay_path),
        "basic_dir": str(basic_dir),
        "instrument_details": str(instrument_path),
        "instrument_backup": backup_path,
        "open_date_repair_csv": str(open_repair_path),
        "financial_validation_csv": str(financial_path),
        "qmt_financial_refresh_symbols": str(refresh_symbols_path),
        "counts": {
            "open_date_candidates": len(open_repairs),
            "open_date_verified": sum(1 for row in open_repairs if row["verified_open_date"]),
            "open_date_patched": patched_open_dates,
            "financial_candidates": len(financial_validations),
            "financial_validated_for_qmt_refresh": len(refresh_symbols),
            "financial_held": sum(1 for row in financial_validations if row["validated"] != "1"),
        },
        "safety": {
            "raw_xtdata_cache_mutated": False,
            "qmt_native_financial_refresh_required": True,
            "open_date_cache_scope": "project_qmt_derived_instrument_details_csv",
        },
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
