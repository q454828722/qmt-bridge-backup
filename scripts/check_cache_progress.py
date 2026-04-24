"""Check local QMT cache coverage for the A-share backfill plan.

The script only reads local xtdata cache. It does not trigger downloads.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from xtquant import xtdata


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_UNIVERSE = ROOT / "data" / "yuanqi_replica" / "basic" / "a_share_universe.csv"
DEFAULT_OUTPUT = ROOT / "data" / "yuanqi_replica" / "basic"
DEFAULT_INSTRUMENT_DETAILS = ROOT / "data" / "yuanqi_replica" / "basic" / "instrument_details.csv"
PROBE_BATCH_SIZE = 200
FINANCIAL_MIN_RECORDS = 8
FINANCIAL_STALE_DAYS = 90
NEW_LISTING_DAYS = 365


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="检查全 A 本地数据缓存覆盖率")
    parser.add_argument("--universe-file", default=str(DEFAULT_UNIVERSE))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--periods", default="1d,5m,1m")
    parser.add_argument("--tables", default="Balance,Income,CashFlow")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="仅检查前 N 只标的，用于小批量测试验证 (默认: 0 表示不限制)",
    )
    parser.add_argument("--skip-kline", action="store_true")
    parser.add_argument("--skip-financial", action="store_true")
    parser.add_argument(
        "--year-from",
        type=int,
        default=None,
        help="可选：检查每个周期从该年份至今的年度覆盖率，耗时更长",
    )
    return parser.parse_args()


def make_batches(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def load_universe(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    if not rows:
        return []
    field = "stock_code" if "stock_code" in rows[0] else reader.fieldnames[0]
    stocks = []
    seen = set()
    for row in rows:
        code = (row.get(field) or "").strip()
        if code and code not in seen:
            stocks.append(code)
            seen.add(code)
    return stocks


def load_instrument_details(path: Path = DEFAULT_INSTRUMENT_DETAILS) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return {
            row.get("stock_code", "").strip(): row
            for row in csv.DictReader(f)
            if row.get("stock_code", "").strip()
        }


def days_since_yyyymmdd(date_text: str) -> int | None:
    text = clean_date(date_text)
    if not text:
        return None
    try:
        return (datetime.now() - datetime.strptime(text, "%Y%m%d")).days
    except ValueError:
        return None


def classify_financial_issue(row: dict[str, Any], details: dict[str, str]) -> dict[str, Any]:
    status = row["status"]
    open_date = clean_date(details.get("open_date", ""))
    name = details.get("name", "")
    days_since_listing = days_since_yyyymmdd(open_date)

    if status == "missing" and days_since_listing is not None and days_since_listing <= NEW_LISTING_DAYS:
        issue_type = "new_listing_no_financial"
        factor_policy = "non_blocking_exclude_financial_factors"
    elif status == "missing":
        issue_type = "missing_financial"
        factor_policy = "review_before_factor_generation"
    elif status == "incomplete" and days_since_listing is not None and days_since_listing <= NEW_LISTING_DAYS:
        issue_type = "new_listing_incomplete_financial"
        factor_policy = "non_blocking_exclude_financial_factors"
    elif status == "incomplete":
        issue_type = "incomplete_financial"
        factor_policy = "review_before_factor_generation"
    elif status == "stale":
        issue_type = "stale_financial"
        factor_policy = "non_blocking_use_latest_available_or_exclude_recent_quarter"
    else:
        issue_type = ""
        factor_policy = ""

    return {
        **row,
        "name": name,
        "open_date": open_date,
        "days_since_listing": "" if days_since_listing is None else days_since_listing,
        "issue_type": issue_type,
        "factor_policy": factor_policy,
    }


def date_from_index(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    if isinstance(value, (int, float)):
        if value > 10_000_000_000:
            return datetime.fromtimestamp(value / 1000).strftime("%Y%m%d")
        return str(int(value))[:8]
    text = str(value)
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8:
        return digits[:8]
    try:
        return pd.Timestamp(value).strftime("%Y%m%d")
    except Exception:
        return text[:8]


def clean_date(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    text = str(value)
    if text.endswith(".0"):
        text = text[:-2]
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else text[:8]


def table_stats(df: Any) -> dict[str, Any]:
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return {"records": 0, "latest_report": "", "latest_announce": ""}
    latest_report = ""
    latest_announce = ""
    if "m_timetag" in df.columns:
        dates = [clean_date(v) for v in df["m_timetag"].dropna().tolist()]
        dates = [d for d in dates if d]
        latest_report = max(dates) if dates else ""
    if "m_anntime" in df.columns:
        dates = [clean_date(v) for v in df["m_anntime"].dropna().tolist()]
        dates = [d for d in dates if d]
        latest_announce = max(dates) if dates else ""
    return {
        "records": int(len(df)),
        "latest_report": latest_report,
        "latest_announce": latest_announce,
    }


def financial_status(per_table: dict[str, dict[str, Any]], tables: list[str], stale_cutoff: str) -> str:
    records = [int(per_table[t]["records"]) for t in tables]
    if all(count == 0 for count in records):
        return "missing"
    if any(count < FINANCIAL_MIN_RECORDS for count in records):
        return "incomplete"
    if any(not per_table[t]["latest_announce"] or per_table[t]["latest_announce"] < stale_cutoff for t in tables):
        return "stale"
    return "ok"


def check_kline(stocks: list[str], periods: list[str], output_dir: Path) -> dict[str, Any]:
    rows = []
    summary = {}
    errors = []
    for period in periods:
        latest_dates: dict[str, str] = {}
        for batch_no, batch in enumerate(make_batches(stocks, PROBE_BATCH_SIZE), start=1):
            try:
                data = xtdata.get_local_data(
                    field_list=[],
                    stock_list=batch,
                    period=period,
                    start_time="",
                    end_time="",
                    count=1,
                )
            except Exception as exc:
                errors.append({"period": period, "batch_no": batch_no, "error": repr(exc)})
                continue
            for stock, df in data.items():
                if df is not None and isinstance(df, pd.DataFrame) and not df.empty:
                    latest_dates[stock] = date_from_index(df.index[-1])

        dates = [d for d in latest_dates.values() if d]
        covered = len(latest_dates)
        summary[period] = {
            "covered": covered,
            "missing": len(stocks) - covered,
            "coverage_ratio": round(covered / len(stocks), 6) if stocks else 0,
            "oldest_latest_date": min(dates) if dates else "",
            "newest_latest_date": max(dates) if dates else "",
        }
        for stock in stocks:
            rows.append({
                "stock_code": stock,
                "period": period,
                "has_cache": "1" if stock in latest_dates else "0",
                "latest_date": latest_dates.get(stock, ""),
            })

    out = output_dir / "cache_progress_kline.csv"
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["stock_code", "period", "has_cache", "latest_date"])
        writer.writeheader()
        writer.writerows(rows)

    issue_rows = [row for row in rows if row["has_cache"] != "1"]
    issues_out = output_dir / "cache_progress_kline_issues.csv"
    with issues_out.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["stock_code", "period", "has_cache", "latest_date"])
        writer.writeheader()
        writer.writerows(issue_rows)
    return {
        "summary": summary,
        "errors": errors,
        "csv": str(out),
        "issues_csv": str(issues_out),
        "issue_count": len(issue_rows),
    }


def check_year_coverage(stocks: list[str], periods: list[str], year_from: int) -> dict[str, Any]:
    current_year = datetime.now().year
    years = list(range(year_from, current_year + 1))
    result: dict[str, dict[str, int]] = {}
    for period in periods:
        result[period] = {}
        for year in years:
            covered = set()
            for batch in make_batches(stocks, PROBE_BATCH_SIZE):
                try:
                    data = xtdata.get_local_data(
                        field_list=[],
                        stock_list=batch,
                        period=period,
                        start_time=f"{year}0101",
                        end_time=f"{year}1231",
                        count=1,
                    )
                except Exception:
                    continue
                for stock, df in data.items():
                    if df is not None and isinstance(df, pd.DataFrame) and not df.empty:
                        covered.add(stock)
            result[period][str(year)] = len(covered)
    return result


def check_financial(stocks: list[str], tables: list[str], output_dir: Path) -> dict[str, Any]:
    stale_cutoff = (datetime.now() - timedelta(days=FINANCIAL_STALE_DAYS)).strftime("%Y%m%d")
    instrument_details = load_instrument_details()
    rows = []
    errors = []
    for batch_no, batch in enumerate(make_batches(stocks, PROBE_BATCH_SIZE), start=1):
        try:
            data = xtdata.get_financial_data(batch, table_list=tables)
        except Exception as exc:
            errors.append({"batch_no": batch_no, "error": repr(exc)})
            data = {}
        for stock in batch:
            stock_tables = data.get(stock, {}) if isinstance(data, dict) else {}
            per_table = {}
            for table in tables:
                df = stock_tables.get(table) if isinstance(stock_tables, dict) else None
                per_table[table] = table_stats(df)
            status = financial_status(per_table, tables, stale_cutoff)
            row = {"stock_code": stock, "status": status}
            for table in tables:
                row[f"{table}_records"] = per_table[table]["records"]
                row[f"{table}_latest_report"] = per_table[table]["latest_report"]
                row[f"{table}_latest_announce"] = per_table[table]["latest_announce"]
            rows.append(row)

    status_counts = {}
    for row in rows:
        status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
    per_table = {}
    for table in tables:
        records = [int(r[f"{table}_records"]) for r in rows]
        fresh_complete = sum(
            1
            for r in rows
            if int(r[f"{table}_records"]) >= FINANCIAL_MIN_RECORDS
            and r[f"{table}_latest_announce"]
            and r[f"{table}_latest_announce"] >= stale_cutoff
        )
        per_table[table] = {
            "nonempty": sum(1 for value in records if value > 0),
            "complete_min_records": sum(1 for value in records if value >= FINANCIAL_MIN_RECORDS),
            "fresh_complete": fresh_complete,
        }

    fieldnames = ["stock_code", "status"]
    for table in tables:
        fieldnames.extend([f"{table}_records", f"{table}_latest_report", f"{table}_latest_announce"])
    out = output_dir / "cache_progress_financial.csv"
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    issue_rows = [
        classify_financial_issue(row, instrument_details.get(row["stock_code"], {}))
        for row in rows
        if row["status"] != "ok"
    ]
    issue_fieldnames = [
        "stock_code",
        "name",
        "open_date",
        "days_since_listing",
        "status",
        "issue_type",
        "factor_policy",
    ]
    for table in tables:
        issue_fieldnames.extend([f"{table}_records", f"{table}_latest_report", f"{table}_latest_announce"])
    issues_out = output_dir / "cache_progress_financial_issues.csv"
    with issues_out.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=issue_fieldnames)
        writer.writeheader()
        writer.writerows(issue_rows)

    issue_type_counts: dict[str, int] = {}
    factor_policy_counts: dict[str, int] = {}
    for row in issue_rows:
        issue_type = row.get("issue_type", "")
        policy = row.get("factor_policy", "")
        issue_type_counts[issue_type] = issue_type_counts.get(issue_type, 0) + 1
        factor_policy_counts[policy] = factor_policy_counts.get(policy, 0) + 1
    return {
        "status_counts": status_counts,
        "ok_ratio": round(status_counts.get("ok", 0) / len(stocks), 6) if stocks else 0,
        "per_table": per_table,
        "stale_cutoff": stale_cutoff,
        "issue_type_counts": issue_type_counts,
        "factor_policy_counts": factor_policy_counts,
        "errors": errors,
        "csv": str(out),
        "issues_csv": str(issues_out),
        "issue_count": len(issue_rows),
    }


def main() -> int:
    args = parse_args()
    universe_file = Path(args.universe_file)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    periods = [p.strip() for p in args.periods.split(",") if p.strip()]
    tables = [t.strip() for t in args.tables.split(",") if t.strip()]
    stocks = load_universe(universe_file)
    original_count = len(stocks)
    if args.limit > 0:
        stocks = stocks[: args.limit]

    summary: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "universe_file": str(universe_file),
        "universe_count": len(stocks),
        "original_universe_count": original_count,
        "limit": args.limit,
        "periods": periods,
        "tables": tables,
    }
    if not args.skip_kline:
        summary["kline"] = check_kline(stocks, periods, output_dir)
        if args.year_from:
            summary["kline_year_coverage"] = check_year_coverage(stocks, periods, args.year_from)
    if not args.skip_financial:
        summary["financial"] = check_financial(stocks, tables, output_dir)

    out = output_dir / "cache_progress_summary.json"
    summary["summary_json"] = str(out)
    with out.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
