#!/usr/bin/env python3
"""Write a batch-safe full research snapshot from the latest cleaned data."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.lib import ResearchClient  # noqa: E402
from research.lib.research_client import _normalize_instrument_frame  # noqa: E402


BASIC_DIR = ROOT / "data" / "yuanqi_replica" / "basic"
SNAPSHOT_ROOT = ROOT / "research" / "output" / "snapshots"
CLEAN_ARTIFACTS = (
    "a_share_universe.csv",
    "instrument_details.csv",
    "cache_progress_summary.json",
    "cache_progress_kline.csv",
    "cache_progress_kline_issues.csv",
    "cache_progress_financial.csv",
    "cache_progress_financial_issues.csv",
    "quant_data_clean_universe.csv",
    "quant_financial_universe_fresh_only.csv",
    "quant_financial_universe_latest_available.csv",
    "quant_backtest_prefilter.csv",
    "quant_data_quality_report.md",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--basic-dir", default=str(BASIC_DIR))
    parser.add_argument("--snapshot-root", default=str(SNAPSHOT_ROOT))
    parser.add_argument("--snapshot-name", default="full_clean_snapshot")
    parser.add_argument("--symbols-file", default=str(BASIC_DIR / "quant_data_clean_universe.csv"))
    parser.add_argument("--symbol-column", default="stock_code")
    parser.add_argument("--period", default="1d")
    parser.add_argument("--start-date", default="20190101")
    parser.add_argument("--end-date", default="")
    parser.add_argument("--batch-size", type=int, default=120)
    parser.add_argument("--financial-batch-size", type=int, default=200)
    parser.add_argument("--financial-tables", default="Balance,Income,CashFlow")
    parser.add_argument("--dividend-type", default="none")
    parser.add_argument("--fill-data", action="store_true")
    parser.add_argument("--skip-financial", action="store_true")
    return parser.parse_args()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def local_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def slugify(value: str) -> str:
    allowed = []
    for char in value.lower().strip():
        if char.isalnum():
            allowed.append(char)
        elif char in {"-", "_", " "}:
            allowed.append("_")
    slug = "".join(allowed).strip("_")
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug or "full_clean_snapshot"


def read_symbols(path: Path, column: str) -> list[str]:
    frame = pd.read_csv(path, dtype=str).fillna("")
    symbol_column = column if column in frame.columns else "stock_code"
    if symbol_column not in frame.columns:
        raise ValueError(f"No symbol column found in {path}: {column}/stock_code")
    symbols = []
    seen = set()
    for value in frame[symbol_column].astype(str):
        symbol = value.strip().upper()
        if symbol and symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
    return symbols


def batches(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def append_csv(frame: pd.DataFrame, path: Path, *, wrote_header: bool) -> bool:
    if frame.empty:
        return wrote_header
    frame.to_csv(path, index=False, mode="a", header=not wrote_header, encoding="utf-8-sig")
    return True


def copy_clean_artifacts(basic_dir: Path, snapshot_dir: Path) -> dict[str, str]:
    output_dir = snapshot_dir / "clean_artifacts"
    output_dir.mkdir(exist_ok=True)
    copied = {}
    for name in CLEAN_ARTIFACTS:
        source = basic_dir / name
        if not source.exists():
            continue
        target = output_dir / name
        shutil.copy2(source, target)
        copied[name] = str(target.relative_to(snapshot_dir))
    return copied


def write_clean_instrument(basic_dir: Path, snapshot_dir: Path, symbols: list[str]) -> dict[str, Any]:
    source = basic_dir / "instrument_details.csv"
    frame = pd.read_csv(source, dtype=str).fillna("")
    frame = frame[frame["stock_code"].isin(symbols)].copy()
    frame = frame.rename(
        columns={
            "stock_code": "symbol",
            "open_date": "list_date",
            "expire_date": "delist_date",
        }
    )
    normalized = _normalize_instrument_frame(frame)
    path = snapshot_dir / "instrument.csv"
    normalized.to_csv(path, index=False, encoding="utf-8-sig")
    return {
        "relative_path": "instrument.csv",
        "rows": int(len(normalized)),
        "columns": list(normalized.columns),
        "coverage_symbols": sorted(normalized["symbol"].dropna().astype(str).unique().tolist())
        if "symbol" in normalized.columns
        else [],
        "source": "qmt_cleaned_derived_cache",
        "quality_flags": ["primary", "cleaned_metadata"],
    }


def write_daily_bars(
    client: ResearchClient,
    snapshot_dir: Path,
    symbols: list[str],
    *,
    period: str,
    start_date: str,
    end_date: str,
    batch_size: int,
    fill_data: bool,
    dividend_type: str,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    path = snapshot_dir / "daily_bar.csv"
    wrote_header = False
    coverage = set()
    asof_dates = []
    errors: list[dict[str, str]] = []
    rows = 0
    columns: list[str] = []
    for batch_no, batch in enumerate(batches(symbols, batch_size), start=1):
        try:
            dataset = client.get_daily_bars(
                batch,
                period=period,
                start_date=start_date,
                end_date=end_date,
                local_only=True,
                fill_data=fill_data,
                dividend_type=dividend_type,
            )
        except Exception as exc:
            errors.append({"domain": "daily_bar", "batch_no": str(batch_no), "error": repr(exc)})
            continue
        frame = dataset.data
        if not frame.empty:
            columns = list(frame.columns)
            rows += len(frame)
            if "symbol" in frame.columns:
                coverage.update(frame["symbol"].dropna().astype(str).unique().tolist())
            if "trade_date" in frame.columns:
                asof_dates.extend(frame["trade_date"].dropna().astype(str).tolist())
            wrote_header = append_csv(frame, path, wrote_header=wrote_header)
    if not wrote_header:
        pd.DataFrame().to_csv(path, index=False, encoding="utf-8-sig")
    return (
        {
            "relative_path": "daily_bar.csv",
            "rows": int(rows),
            "columns": columns,
            "requested_symbols": symbols,
            "coverage_symbols": sorted(coverage),
            "asof_date": max(asof_dates) if asof_dates else "",
            "source": "qmt",
            "quality_flags": ["primary", "local_cache", "batched_snapshot"],
            "metadata": {
                "period": period,
                "start_date": start_date,
                "end_date": end_date,
                "batch_size": batch_size,
                "fill_data": fill_data,
                "dividend_type": dividend_type,
            },
        },
        errors,
    )


def write_financials(
    client: ResearchClient,
    snapshot_dir: Path,
    symbols: list[str],
    *,
    tables: list[str],
    batch_size: int,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    financial_dir = snapshot_dir / "financial"
    financial_dir.mkdir(exist_ok=True)
    wrote_headers = {table: False for table in tables}
    rows = {table: 0 for table in tables}
    columns = {table: [] for table in tables}
    coverage = set()
    asof_dates = []
    errors: list[dict[str, str]] = []
    for batch_no, batch in enumerate(batches(symbols, batch_size), start=1):
        try:
            dataset = client.get_financials(batch, tables=tables)
        except Exception as exc:
            errors.append({"domain": "financial", "batch_no": str(batch_no), "error": repr(exc)})
            continue
        for table in tables:
            frame = dataset.tables.get(table, pd.DataFrame())
            if frame.empty:
                continue
            path = financial_dir / f"{table}.csv"
            columns[table] = list(frame.columns)
            rows[table] += len(frame)
            if "symbol" in frame.columns:
                coverage.update(frame["symbol"].dropna().astype(str).unique().tolist())
            for date_col in ("report_date", "announce_date", "announce_time"):
                if date_col in frame.columns:
                    asof_dates.extend(frame[date_col].dropna().astype(str).tolist())
            wrote_headers[table] = append_csv(frame, path, wrote_header=wrote_headers[table])
    table_meta = {}
    for table in tables:
        path = financial_dir / f"{table}.csv"
        if not path.exists():
            pd.DataFrame().to_csv(path, index=False, encoding="utf-8-sig")
        table_meta[table] = {
            "relative_path": f"financial/{table}.csv",
            "rows": int(rows[table]),
            "columns": columns[table],
        }
    return (
        {
            "domain": "financial",
            "source": "qmt",
            "tables": table_meta,
            "requested_symbols": symbols,
            "coverage_symbols": sorted(coverage),
            "asof_date": max(asof_dates) if asof_dates else "",
            "quality_flags": ["primary", "qmt_financial_cache", "batched_snapshot"],
            "metadata": {"batch_size": batch_size, "tables": tables},
        },
        errors,
    )


def main() -> int:
    args = parse_args()
    basic_dir = Path(args.basic_dir)
    symbols = read_symbols(Path(args.symbols_file), args.symbol_column)
    snapshot_id = f"{local_stamp()}_{slugify(args.snapshot_name)}"
    snapshot_dir = Path(args.snapshot_root) / snapshot_id
    snapshot_dir.mkdir(parents=True, exist_ok=False)

    copied_artifacts = copy_clean_artifacts(basic_dir, snapshot_dir)
    instrument_meta = write_clean_instrument(basic_dir, snapshot_dir, symbols)
    client = ResearchClient()
    daily_meta, daily_errors = write_daily_bars(
        client,
        snapshot_dir,
        symbols,
        period=args.period,
        start_date=args.start_date,
        end_date=args.end_date,
        batch_size=args.batch_size,
        fill_data=args.fill_data,
        dividend_type=args.dividend_type,
    )

    financial_meta = None
    financial_errors: list[dict[str, str]] = []
    tables = [item.strip() for item in args.financial_tables.split(",") if item.strip()]
    if not args.skip_financial:
        financial_meta, financial_errors = write_financials(
            client,
            snapshot_dir,
            symbols,
            tables=tables,
            batch_size=args.financial_batch_size,
        )

    quality_summary_path = basic_dir / "cache_progress_summary.json"
    quality_summary = {}
    if quality_summary_path.exists():
        quality_summary = json.loads(quality_summary_path.read_text(encoding="utf-8"))

    manifest = {
        "snapshot_id": snapshot_id,
        "snapshot_name": args.snapshot_name,
        "snapshot_version": "clean-full-snapshot-v1",
        "created_at": utc_now_iso(),
        "storage_format": "csv",
        "snapshot_dir": str(snapshot_dir),
        "query": {
            "symbols_file": str(args.symbols_file),
            "requested_symbol_count": len(symbols),
            "period": args.period,
            "start_date": args.start_date,
            "end_date": args.end_date,
            "daily_batch_size": args.batch_size,
            "financial_batch_size": args.financial_batch_size,
            "financial_tables": tables,
            "local_only": True,
            "fill_data": args.fill_data,
            "dividend_type": args.dividend_type,
        },
        "datasets": {
            "daily_bar": daily_meta,
            "instrument": instrument_meta,
        },
        "clean_artifacts": copied_artifacts,
        "data_quality_summary": quality_summary,
        "errors": [*daily_errors, *financial_errors],
    }
    if financial_meta is not None:
        manifest["datasets"]["financial"] = financial_meta
    manifest_path = snapshot_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "snapshot_id": snapshot_id,
        "snapshot_dir": str(snapshot_dir),
        "manifest_path": str(manifest_path),
        "daily_rows": daily_meta["rows"],
        "daily_coverage_symbols": len(daily_meta["coverage_symbols"]),
        "instrument_rows": instrument_meta["rows"],
        "financial_tables": sorted((financial_meta or {}).get("tables", {}).keys()),
        "errors": len(manifest["errors"]),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
