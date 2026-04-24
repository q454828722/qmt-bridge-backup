#!/usr/bin/env python3
"""CLI wrapper for writing research snapshots."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from research.lib import ResearchClient


def _parse_csv_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _load_symbols(symbols: str, symbols_file: str, symbol_column: str) -> list[str]:
    if symbols:
        return _parse_csv_list(symbols)
    if not symbols_file:
        raise SystemExit("One of --symbols or --symbols-file is required.")

    path = Path(symbols_file).expanduser()
    if not path.exists():
        raise SystemExit(f"Symbols file not found: {path}")

    if path.suffix.lower() in {".csv", ".tsv"}:
        sep = "\t" if path.suffix.lower() == ".tsv" else ","
        frame = pd.read_csv(path, sep=sep, dtype=str).fillna("")
        candidates = [symbol_column, "stock_code", "symbol", "ts_code"]
        column = next((name for name in candidates if name and name in frame.columns), None)
        if column is None:
            raise SystemExit(f"No symbol column found in {path}. Tried: {candidates}")
        return [value.strip() for value in frame[column].tolist() if str(value).strip()]

    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Write a research snapshot from QMT research client.")
    parser.add_argument("--symbols", default="", help="Comma-separated stock codes, e.g. 000001.SZ,600519.SH")
    parser.add_argument("--symbols-file", default="", help="Text/CSV/TSV file containing symbols")
    parser.add_argument("--symbol-column", default="stock_code", help="Column name to read from CSV/TSV")
    parser.add_argument("--snapshot-name", default="", help="Human-friendly snapshot name")
    parser.add_argument("--snapshot-root", default="", help="Optional snapshot root directory")
    parser.add_argument("--storage-format", default="parquet", choices=("parquet", "csv"))
    parser.add_argument("--period", default="1d")
    parser.add_argument("--start-date", default="")
    parser.add_argument("--end-date", default="")
    parser.add_argument("--fill-data", action="store_true", help="Enable forward-fill on QMT bars")
    parser.add_argument("--allow-fallback", action="store_true", help="Allow fallback sources")
    parser.add_argument("--dividend-type", default="none")
    parser.add_argument("--financial-tables", default="Balance,Income,CashFlow")
    parser.add_argument("--financial-report-type", default="report_time")
    parser.add_argument("--remote-qmt", action="store_true", help="Use QMT remote read instead of local cache")
    parser.add_argument("--no-instrument", dest="include_instrument", action="store_false")
    parser.add_argument("--no-financial", dest="include_financial", action="store_false")
    parser.set_defaults(include_instrument=True, include_financial=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    symbols = _load_symbols(args.symbols, args.symbols_file, args.symbol_column)
    client = ResearchClient()
    snapshot = client.write_snapshot(
        symbols,
        snapshot_name=args.snapshot_name,
        snapshot_root=args.snapshot_root or None,
        storage_format=args.storage_format,
        period=args.period,
        start_date=args.start_date,
        end_date=args.end_date,
        local_only=not args.remote_qmt,
        fill_data=args.fill_data,
        dividend_type=args.dividend_type,
        allow_fallback=args.allow_fallback,
        include_instrument=args.include_instrument,
        include_financial=args.include_financial,
        financial_tables=_parse_csv_list(args.financial_tables),
        financial_report_type=args.financial_report_type,
    )
    payload = {
        "snapshot_id": snapshot.snapshot_id,
        "snapshot_dir": str(snapshot.snapshot_dir),
        "manifest_path": str(snapshot.manifest_path),
        "daily_rows": len(snapshot.daily_bars.data) if snapshot.daily_bars is not None else 0,
        "instrument_rows": len(snapshot.instrument.data) if snapshot.instrument is not None else 0,
        "financial_tables": sorted(snapshot.financials.tables) if snapshot.financials is not None else [],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
