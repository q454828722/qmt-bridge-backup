#!/usr/bin/env python3
"""CLI wrapper for writing snapshot diff artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.lib import ResearchClient


def _parse_csv_list(value: str) -> list[str] | None:
    values = [item.strip() for item in value.split(",") if item.strip()]
    return values or None


def _parse_financial_fields_json(text: str):
    if not text:
        return None
    return json.loads(text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Write a snapshot diff artifact.")
    parser.add_argument("--left-snapshot", required=True, help="Left snapshot directory or manifest path")
    parser.add_argument("--right-snapshot", required=True, help="Right snapshot directory or manifest path")
    parser.add_argument("--diff-name", default="", help="Human-friendly diff name")
    parser.add_argument("--diff-root", default="", help="Optional diff output root")
    parser.add_argument("--storage-format", default="parquet", choices=("parquet", "csv"))
    parser.add_argument("--daily-fields", default="", help="Comma-separated daily bar fields to compare")
    parser.add_argument("--instrument-fields", default="", help="Comma-separated instrument fields to compare")
    parser.add_argument(
        "--financial-fields-json",
        default="",
        help='JSON dict or array for financial compare fields, e.g. {"Balance":["announce_date"]}',
    )
    parser.add_argument("--abs-tolerance", type=float, default=1e-8)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    client = ResearchClient()
    artifact = client.write_diff_report(
        args.left_snapshot,
        args.right_snapshot,
        diff_name=args.diff_name,
        diff_root=args.diff_root or None,
        storage_format=args.storage_format,
        daily_fields=_parse_csv_list(args.daily_fields),
        instrument_fields=_parse_csv_list(args.instrument_fields),
        financial_fields=_parse_financial_fields_json(args.financial_fields_json),
        abs_tolerance=args.abs_tolerance,
    )
    payload = {
        "diff_id": artifact.diff_id,
        "diff_dir": str(artifact.diff_dir),
        "manifest_path": str(artifact.manifest_path),
        "top_changes_path": str(artifact.top_changes_path),
        "has_changes": artifact.has_changes,
        "dataset_summary_rows": len(artifact.report.dataset_summary),
        "row_change_rows": len(artifact.report.row_changes),
        "field_change_rows": len(artifact.report.field_changes),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
