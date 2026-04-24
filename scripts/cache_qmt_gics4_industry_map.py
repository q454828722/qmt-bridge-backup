#!/usr/bin/env python3
"""Build and cache the local QMT GICS4 industry map for research use."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.lib.qmt_gics4_industry import (  # noqa: E402
    DEFAULT_QMT_GICS4_CACHE,
    DEFAULT_QMT_GICS4_SUMMARY,
    write_qmt_gics4_industry_cache,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-path", type=Path, default=DEFAULT_QMT_GICS4_CACHE)
    parser.add_argument("--summary-path", type=Path, default=DEFAULT_QMT_GICS4_SUMMARY)
    parser.add_argument("--symbols-file", type=Path, default=None, help="Optional CSV file used to limit cache scope.")
    parser.add_argument("--symbol-column", default="stock_code", help="Symbol column name in --symbols-file.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    symbols = None
    if args.symbols_file:
        frame = pd.read_csv(args.symbols_file, dtype=str).fillna("")
        if args.symbol_column not in frame.columns:
            raise ValueError(f"{args.symbol_column} not found in {args.symbols_file}")
        symbols = frame[args.symbol_column].astype(str).str.strip()
        symbols = [symbol for symbol in symbols if symbol]

    summary = write_qmt_gics4_industry_cache(
        output_path=args.output_path,
        summary_path=args.summary_path,
        symbols=symbols,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
