#!/usr/bin/env python3
"""Maintain the StarBridge SQLite research database incrementally."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.lib.research_database import (  # noqa: E402
    detect_source_conflicts,
    ensure_maintenance_layer,
    finish_batch,
    insert_source_evidence,
    normalize_date_text,
    normalize_value,
    start_batch,
    upsert_daily_bar_delta,
    utc_now_iso,
)


DEFAULT_DB_PATH = ROOT / "data" / "research" / "starbridge_quant_research.sqlite"
DEFAULT_SKIP_COLUMNS = {
    "symbol",
    "stock_code",
    "code",
    "trade_date",
    "date",
    "report_date",
    "announce_date",
    "source",
    "source_chain",
    "validation_status",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-path", default=str(DEFAULT_DB_PATH))
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="Create or migrate the maintenance layer.")
    subparsers.add_parser("status", help="Print maintenance-layer counts and latest conflicts.")

    evidence = subparsers.add_parser("ingest-evidence", help="Ingest field-level source evidence from CSV.")
    evidence.add_argument("--csv", required=True, dest="csv_path")
    evidence.add_argument("--domain", required=True)
    evidence.add_argument("--source", required=True)
    evidence.add_argument("--target-table", default="")
    evidence.add_argument("--symbol-col", default="symbol")
    evidence.add_argument("--date-col", default="")
    evidence.add_argument("--fields", default="", help="Comma-separated field list. Defaults to non-key CSV columns.")
    evidence.add_argument("--validation-status", default="observed")
    evidence.add_argument("--confidence", type=float, default=1.0)
    evidence.add_argument("--metadata-json", default="{}")
    evidence.add_argument("--skip-conflict-detection", action="store_true")

    delta = subparsers.add_parser("apply-daily-delta", help="Upsert verified daily bars into daily_bar_delta.")
    delta.add_argument("--csv", required=True, dest="csv_path")
    delta.add_argument("--source", required=True)
    delta.add_argument("--source-chain", default="")
    delta.add_argument("--validation-status", default="multi_source_verified")
    delta.add_argument("--symbol-col", default="symbol")
    delta.add_argument("--date-col", default="trade_date")
    delta.add_argument("--notes", default="")
    delta.add_argument("--applied-by", default="data_specialist")

    watermark = subparsers.add_parser("set-watermark", help="Update an incremental dataset watermark.")
    watermark.add_argument("--domain", required=True)
    watermark.add_argument("--source", required=True)
    watermark.add_argument("--target-table", required=True)
    watermark.add_argument("--last-success-date", required=True)
    watermark.add_argument("--row-count", type=int, default=0)
    watermark.add_argument("--notes", default="")

    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def parse_metadata(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid --metadata-json: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("--metadata-json must decode to an object")
    return value


def resolve_fields(rows: list[dict[str, str]], configured: str) -> list[str]:
    if configured.strip():
        return [item.strip() for item in configured.split(",") if item.strip()]
    fields: list[str] = []
    for row in rows:
        for column in row:
            if column in DEFAULT_SKIP_COLUMNS:
                continue
            if column not in fields:
                fields.append(column)
    return fields


def command_status(conn: sqlite3.Connection) -> dict[str, Any]:
    counts = {
        row[0]: int(row[1])
        for row in conn.execute("SELECT item, rows FROM v_data_maintenance_status ORDER BY item").fetchall()
    }
    factor_cache = {}
    if (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'factor_cache_registry'",
        ).fetchone()
        is not None
    ):
        factor_cache = {
            row[0]: {"source_view": row[1], "built_at": row[2], "row_count": int(row[3])}
            for row in conn.execute(
                """
                SELECT cache_name, source_view, built_at, row_count
                FROM factor_cache_registry
                ORDER BY cache_name
                """
            ).fetchall()
        }
    latest_conflicts = [
        {
            "domain": row[0],
            "target_table": row[1],
            "symbol": row[2],
            "record_date": row[3],
            "field_name": row[4],
            "candidate_values_json": row[5],
            "detected_at": row[6],
        }
        for row in conn.execute(
            """
            SELECT domain, target_table, symbol, record_date, field_name,
                   candidate_values_json, detected_at
            FROM v_open_source_conflicts
            ORDER BY detected_at DESC
            LIMIT 20
            """
        ).fetchall()
    ]
    return {"counts": counts, "factor_cache": factor_cache, "latest_open_conflicts": latest_conflicts}


def command_ingest_evidence(conn: sqlite3.Connection, args: argparse.Namespace) -> dict[str, Any]:
    csv_path = Path(args.csv_path)
    rows = read_csv_rows(csv_path)
    fields = resolve_fields(rows, args.fields)
    metadata = {**parse_metadata(args.metadata_json), "csv_path": str(csv_path)}
    batch_id = start_batch(
        conn,
        domain=args.domain,
        source=args.source,
        mode="ingest_evidence",
        metadata={"csv_path": str(csv_path), "fields": fields},
    )
    evidence_rows: list[dict[str, Any]] = []
    symbol_col = args.symbol_col
    date_col = args.date_col
    for row in rows:
        symbol = normalize_value(row.get(symbol_col) or row.get("stock_code") or row.get("code")).upper()
        record_date = normalize_date_text(row.get(date_col)) if date_col else ""
        if not symbol:
            continue
        for field_name in fields:
            observed_value = normalize_value(row.get(field_name))
            if observed_value == "":
                continue
            evidence_rows.append(
                {
                    "batch_id": batch_id,
                    "domain": args.domain,
                    "source": args.source,
                    "target_table": args.target_table or args.domain,
                    "symbol": symbol,
                    "record_date": record_date,
                    "field_name": field_name,
                    "observed_value": observed_value,
                    "value_kind": "numeric" if _looks_numeric(observed_value) else "text",
                    "confidence": args.confidence,
                    "validation_status": args.validation_status,
                    "metadata": metadata,
                }
            )
    evidence_ids = insert_source_evidence(conn, evidence_rows)
    conflict_count = 0 if args.skip_conflict_detection else detect_source_conflicts(conn)
    finish_batch(conn, batch_id, row_count=len(evidence_rows))
    ensure_maintenance_layer(conn)
    return {
        "batch_id": batch_id,
        "csv_path": str(csv_path),
        "input_rows": len(rows),
        "evidence_rows": len(evidence_rows),
        "evidence_ids": len(evidence_ids),
        "new_conflicts": conflict_count,
    }


def _looks_numeric(value: str) -> bool:
    try:
        float(value.replace(",", ""))
    except ValueError:
        return False
    return True


def command_apply_daily_delta(conn: sqlite3.Connection, args: argparse.Namespace) -> dict[str, Any]:
    csv_path = Path(args.csv_path)
    rows = read_csv_rows(csv_path)
    normalized_rows = []
    for row in rows:
        normalized = dict(row)
        normalized["symbol"] = row.get(args.symbol_col, row.get("symbol", ""))
        normalized["trade_date"] = row.get(args.date_col, row.get("trade_date", ""))
        normalized_rows.append(normalized)
    source_chain = args.source_chain or args.source
    batch_id = start_batch(
        conn,
        domain="daily_bar",
        source=args.source,
        mode="apply_daily_delta",
        metadata={"csv_path": str(csv_path), "source_chain": source_chain},
    )
    try:
        written = upsert_daily_bar_delta(
            conn,
            normalized_rows,
            source=args.source,
            source_chain=source_chain,
            validation_status=args.validation_status,
            batch_id=batch_id,
            applied_by=args.applied_by,
            notes=args.notes,
        )
        finish_batch(conn, batch_id, row_count=written)
    except Exception:
        finish_batch(conn, batch_id, row_count=0, status="failed")
        raise
    ensure_maintenance_layer(conn)
    return {"batch_id": batch_id, "csv_path": str(csv_path), "daily_bar_delta_rows": written}


def command_set_watermark(conn: sqlite3.Connection, args: argparse.Namespace) -> dict[str, Any]:
    now = utc_now_iso()
    conn.execute(
        """
        INSERT INTO dataset_watermarks(
            domain, source, target_table, last_success_date, last_success_at,
            row_count, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(domain, source, target_table) DO UPDATE SET
            last_success_date = excluded.last_success_date,
            last_success_at = excluded.last_success_at,
            row_count = excluded.row_count,
            notes = excluded.notes
        """,
        (
            args.domain,
            args.source,
            args.target_table,
            normalize_date_text(args.last_success_date),
            now,
            args.row_count,
            args.notes,
        ),
    )
    conn.commit()
    return {
        "domain": args.domain,
        "source": args.source,
        "target_table": args.target_table,
        "last_success_date": normalize_date_text(args.last_success_date),
        "last_success_at": now,
    }


def main() -> int:
    args = parse_args()
    db_path = Path(args.database_path)
    conn = connect(db_path)
    try:
        ensure_maintenance_layer(conn)
        if args.command == "init":
            result = {"database_path": str(db_path), "maintenance_layer": "ready"}
        elif args.command == "status":
            result = command_status(conn)
        elif args.command == "ingest-evidence":
            result = command_ingest_evidence(conn, args)
        elif args.command == "apply-daily-delta":
            result = command_apply_daily_delta(conn, args)
        elif args.command == "set-watermark":
            result = command_set_watermark(conn, args)
        else:
            raise ValueError(f"Unsupported command: {args.command}")
    finally:
        conn.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
