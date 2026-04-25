#!/usr/bin/env python3
"""Build a SQLite research database from a cleaned StarBridge snapshot."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.lib.research_database import MAINTENANCE_TABLES, ensure_maintenance_layer  # noqa: E402

DEFAULT_SNAPSHOT_ROOT = ROOT / "research" / "output" / "snapshots"
DEFAULT_DB_PATH = ROOT / "data" / "research" / "starbridge_quant_research.sqlite"


CSV_TABLES = {
    "daily_bar": "daily_bar.csv",
    "instrument": "instrument.csv",
    "daily_bar_missing_symbols": "daily_bar_missing_symbols.csv",
    "financial_balance": "financial/Balance.csv",
    "financial_income": "financial/Income.csv",
    "financial_cashflow": "financial/CashFlow.csv",
    "clean_quant_backtest_prefilter": "clean_artifacts/quant_backtest_prefilter.csv",
    "clean_quant_data_clean_universe": "clean_artifacts/quant_data_clean_universe.csv",
    "clean_quant_financial_universe_fresh_only": "clean_artifacts/quant_financial_universe_fresh_only.csv",
    "clean_quant_financial_universe_latest_available": (
        "clean_artifacts/quant_financial_universe_latest_available.csv"
    ),
    "quality_cache_progress_kline": "clean_artifacts/cache_progress_kline.csv",
    "quality_cache_progress_financial": "clean_artifacts/cache_progress_financial.csv",
    "quality_cache_progress_kline_issues": "clean_artifacts/cache_progress_kline_issues.csv",
    "quality_cache_progress_financial_issues": "clean_artifacts/cache_progress_financial_issues.csv",
    "raw_a_share_universe": "clean_artifacts/a_share_universe.csv",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-dir", default="", help="Clean snapshot directory. Defaults to latest usable one.")
    parser.add_argument("--snapshot-root", default=str(DEFAULT_SNAPSHOT_ROOT))
    parser.add_argument("--database-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--replace", action="store_true", help="Replace the existing database file.")
    parser.add_argument("--chunk-size", type=int, default=100_000)
    parser.add_argument("--skip-analyze", action="store_true")
    return parser.parse_args()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_manifest(snapshot_dir: Path) -> dict[str, Any]:
    manifest_path = snapshot_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(manifest_path)
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def find_latest_snapshot(snapshot_root: Path) -> Path:
    candidates = []
    for path in snapshot_root.iterdir() if snapshot_root.exists() else []:
        if not path.is_dir():
            continue
        manifest_path = path / "manifest.json"
        daily_path = path / "daily_bar.csv"
        instrument_path = path / "instrument.csv"
        if manifest_path.exists() and daily_path.exists() and instrument_path.exists():
            candidates.append(path)
    if not candidates:
        raise FileNotFoundError(f"No usable cleaned snapshot found under {snapshot_root}")
    return max(candidates, key=lambda item: item.stat().st_mtime)


def connect_database(path: Path, *, replace: bool) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    if replace and path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-200000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def write_meta_tables(conn: sqlite3.Connection, snapshot_dir: Path, manifest: dict[str, Any]) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS database_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS snapshots (
            snapshot_id TEXT PRIMARY KEY,
            snapshot_name TEXT,
            snapshot_dir TEXT NOT NULL,
            storage_format TEXT NOT NULL,
            created_at TEXT,
            loaded_at TEXT NOT NULL,
            manifest_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dataset_registry (
            table_name TEXT PRIMARY KEY,
            relative_path TEXT NOT NULL,
            source_snapshot_id TEXT NOT NULL,
            loaded_rows INTEGER NOT NULL,
            loaded_at TEXT NOT NULL
        )
        """
    )
    loaded_at = utc_now_iso()
    meta = {
        "database_version": "research-sqlite-v1",
        "created_at": loaded_at,
        "primary_snapshot_id": manifest.get("snapshot_id", ""),
        "primary_snapshot_dir": str(snapshot_dir),
        "purpose": "QMT-safe research database for data steward maintenance and factor specialist reads",
    }
    conn.executemany(
        "INSERT OR REPLACE INTO database_meta(key, value) VALUES (?, ?)",
        [(key, str(value)) for key, value in meta.items()],
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO snapshots(
            snapshot_id, snapshot_name, snapshot_dir, storage_format, created_at, loaded_at, manifest_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            manifest.get("snapshot_id", ""),
            manifest.get("snapshot_name", ""),
            str(snapshot_dir),
            manifest.get("storage_format", ""),
            manifest.get("created_at", ""),
            loaded_at,
            json.dumps(manifest, ensure_ascii=False),
        ),
    )
    conn.commit()


def import_csv_table(
    conn: sqlite3.Connection,
    *,
    snapshot_dir: Path,
    table_name: str,
    relative_path: str,
    snapshot_id: str,
    chunk_size: int,
) -> int:
    path = snapshot_dir / relative_path
    if not path.exists():
        return 0
    conn.execute(f'DROP TABLE IF EXISTS "{table_name}"')
    loaded_rows = 0
    for chunk_no, chunk in enumerate(
        pd.read_csv(path, chunksize=chunk_size, low_memory=False, encoding="utf-8-sig"),
        start=1,
    ):
        chunk.to_sql(table_name, conn, if_exists="replace" if chunk_no == 1 else "append", index=False)
        loaded_rows += len(chunk)
    conn.execute(
        """
        INSERT OR REPLACE INTO dataset_registry(
            table_name, relative_path, source_snapshot_id, loaded_rows, loaded_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (table_name, relative_path, snapshot_id, loaded_rows, utc_now_iso()),
    )
    conn.commit()
    return loaded_rows


def table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    try:
        rows = conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
    except sqlite3.DatabaseError:
        return set()
    return {str(row[1]) for row in rows}


def create_index_if_columns(conn: sqlite3.Connection, table_name: str, index_name: str, columns: tuple[str, ...]) -> None:
    existing = table_columns(conn, table_name)
    if not set(columns).issubset(existing):
        return
    quoted_columns = ", ".join(f'"{column}"' for column in columns)
    conn.execute(f'CREATE INDEX IF NOT EXISTS "{index_name}" ON "{table_name}" ({quoted_columns})')


def create_indexes(conn: sqlite3.Connection) -> None:
    create_index_if_columns(conn, "daily_bar", "idx_daily_bar_symbol_date", ("symbol", "trade_date"))
    create_index_if_columns(conn, "daily_bar", "idx_daily_bar_date_symbol", ("trade_date", "symbol"))
    create_index_if_columns(conn, "instrument", "idx_instrument_symbol", ("symbol",))
    create_index_if_columns(conn, "daily_bar_missing_symbols", "idx_daily_missing_symbol", ("symbol",))
    for table in ("financial_balance", "financial_income", "financial_cashflow"):
        create_index_if_columns(conn, table, f"idx_{table}_symbol_report", ("symbol", "report_date"))
        create_index_if_columns(conn, table, f"idx_{table}_symbol_announce", ("symbol", "announce_date"))
    for table in (
        "clean_quant_backtest_prefilter",
        "clean_quant_data_clean_universe",
        "clean_quant_financial_universe_fresh_only",
        "clean_quant_financial_universe_latest_available",
        "quality_cache_progress_financial",
        "quality_cache_progress_kline",
    ):
        create_index_if_columns(conn, table, f"idx_{table}_stock_code", ("stock_code",))
    conn.commit()


def create_views(conn: sqlite3.Connection) -> None:
    for view in (
        "v_price_universe",
        "v_factor_ready_daily",
        "v_financial_fresh_universe",
        "v_financial_latest_available_universe",
        "v_data_quality_summary",
    ):
        conn.execute(f'DROP VIEW IF EXISTS "{view}"')
    fresh_columns = table_columns(conn, "clean_quant_financial_universe_fresh_only")
    if "fresh_financial_available" in fresh_columns:
        fresh_condition = "CAST(fresh_financial_available AS TEXT) = '1'"
    elif "financial_status" in fresh_columns:
        fresh_condition = "financial_status = 'ok'"
    else:
        fresh_condition = "1 = 1"

    latest_columns = table_columns(conn, "clean_quant_financial_universe_latest_available")
    if "latest_available_financial_available" in latest_columns:
        latest_condition = "CAST(latest_available_financial_available AS TEXT) = '1'"
    elif "financial_status" in latest_columns:
        latest_condition = "financial_status IN ('ok', 'stale')"
    else:
        latest_condition = "1 = 1"

    conn.execute(
        """
        CREATE VIEW v_price_universe AS
        SELECT
            p.stock_code AS symbol,
            COALESCE(i.name, p.name) AS name,
            i.list_date,
            i.exchange,
            p.include_price_factors,
            p.exclude_from_price_backtest,
            p.exclude_reason
        FROM clean_quant_backtest_prefilter p
        JOIN (SELECT DISTINCT symbol FROM daily_bar) d
            ON d.symbol = p.stock_code
        LEFT JOIN instrument i
            ON i.symbol = p.stock_code
        WHERE CAST(p.include_price_factors AS TEXT) = '1'
          AND CAST(p.exclude_from_price_backtest AS TEXT) = '0'
        """
    )
    conn.execute(
        """
        CREATE VIEW v_factor_ready_daily AS
        SELECT
            d.*,
            u.name,
            u.list_date,
            u.exchange
        FROM daily_bar d
        JOIN v_price_universe u
            ON u.symbol = d.symbol
        """
    )
    conn.execute(
        f"""
        CREATE VIEW v_financial_fresh_universe AS
        SELECT *
        FROM clean_quant_financial_universe_fresh_only
        WHERE CAST(include_price_factors AS TEXT) = '1'
          AND {fresh_condition}
        """
    )
    conn.execute(
        f"""
        CREATE VIEW v_financial_latest_available_universe AS
        SELECT *
        FROM clean_quant_financial_universe_latest_available
        WHERE CAST(include_price_factors AS TEXT) = '1'
          AND {latest_condition}
        """
    )
    conn.execute(
        """
        CREATE VIEW v_data_quality_summary AS
        SELECT 'daily_bar' AS domain, COUNT(*) AS rows, COUNT(DISTINCT symbol) AS symbols FROM daily_bar
        UNION ALL
        SELECT 'instrument' AS domain, COUNT(*) AS rows, COUNT(DISTINCT symbol) AS symbols FROM instrument
        UNION ALL
        SELECT 'financial_balance' AS domain, COUNT(*) AS rows, COUNT(DISTINCT symbol) AS symbols FROM financial_balance
        UNION ALL
        SELECT 'financial_income' AS domain, COUNT(*) AS rows, COUNT(DISTINCT symbol) AS symbols FROM financial_income
        UNION ALL
        SELECT 'financial_cashflow' AS domain, COUNT(*) AS rows, COUNT(DISTINCT symbol) AS symbols FROM financial_cashflow
        """
    )
    conn.commit()


def collect_summary(conn: sqlite3.Connection, db_path: Path, snapshot_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    table_rows = {
        row[0]: int(row[1])
        for row in conn.execute("SELECT table_name, loaded_rows FROM dataset_registry ORDER BY table_name").fetchall()
    }
    quality_rows = [
        {"domain": row[0], "rows": int(row[1]), "symbols": int(row[2])}
        for row in conn.execute("SELECT domain, rows, symbols FROM v_data_quality_summary").fetchall()
    ]
    view_counts = {
        "v_price_universe": conn.execute("SELECT COUNT(*) FROM v_price_universe").fetchone()[0],
        "v_factor_ready_daily": conn.execute("SELECT COUNT(*) FROM v_factor_ready_daily").fetchone()[0],
        "v_factor_ready_daily_effective": conn.execute("SELECT COUNT(*) FROM v_factor_ready_daily_effective").fetchone()[
            0
        ],
        "v_financial_fresh_universe": conn.execute("SELECT COUNT(*) FROM v_financial_fresh_universe").fetchone()[0],
        "v_financial_latest_available_universe": conn.execute(
            "SELECT COUNT(*) FROM v_financial_latest_available_universe"
        ).fetchone()[0],
    }
    maintenance_counts = {}
    for table in MAINTENANCE_TABLES:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
        if row:
            maintenance_counts[table] = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    return {
        "database_path": str(db_path),
        "database_size_bytes": db_path.stat().st_size if db_path.exists() else 0,
        "snapshot_id": manifest.get("snapshot_id", ""),
        "snapshot_dir": str(snapshot_dir),
        "loaded_tables": table_rows,
        "quality_summary": quality_rows,
        "view_counts": view_counts,
        "maintenance_counts": maintenance_counts,
    }


def write_report(summary: dict[str, Any], report_path: Path) -> None:
    lines = [
        "# StarBridge Quant Research Database",
        "",
        f"- Database: `{summary['database_path']}`",
        f"- Snapshot: `{summary['snapshot_id']}`",
        f"- Snapshot dir: `{summary['snapshot_dir']}`",
        f"- Size bytes: `{summary['database_size_bytes']}`",
        "",
        "## Loaded Tables",
        "",
        "| Table | Rows |",
        "| --- | ---: |",
    ]
    for table, rows in summary["loaded_tables"].items():
        lines.append(f"| `{table}` | {rows} |")
    lines.extend(["", "## Quality Views", "", "| View | Rows |", "| --- | ---: |"])
    for view, rows in summary["view_counts"].items():
        lines.append(f"| `{view}` | {rows} |")
    lines.extend(["", "## Maintenance Layer", "", "| Table | Rows |", "| --- | ---: |"])
    for table, rows in summary.get("maintenance_counts", {}).items():
        lines.append(f"| `{table}` | {rows} |")
    lines.extend(
        [
            "",
            "## Example Queries",
            "",
            "```sql",
            "SELECT * FROM v_factor_ready_daily",
            "WHERE trade_date BETWEEN 20250101 AND 20260421",
            "  AND symbol IN ('000001.SZ', '600519.SH');",
            "",
            "SELECT * FROM v_factor_ready_daily_effective",
            "WHERE trade_date BETWEEN 20250101 AND 20260421",
            "  AND symbol IN ('000001.SZ', '600519.SH');",
            "",
            "SELECT * FROM v_open_source_conflicts ORDER BY detected_at DESC LIMIT 50;",
            "",
            "SELECT symbol, report_date, announce_date, revenue, net_profit_incl_min_int_inc",
            "FROM financial_income",
            "WHERE symbol = '000001.SZ'",
            "ORDER BY report_date DESC;",
            "```",
            "",
            "## Maintenance Rule",
            "",
            "This SQLite database is the research-side mutable layer. Do not write external data into xtdata's "
            "private binary cache; rebuild or incrementally update this database from verified snapshots/overlays.",
        ]
    )
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    snapshot_dir = Path(args.snapshot_dir) if args.snapshot_dir else find_latest_snapshot(Path(args.snapshot_root))
    manifest = load_manifest(snapshot_dir)
    db_path = Path(args.database_path)
    conn = connect_database(db_path, replace=args.replace)
    try:
        write_meta_tables(conn, snapshot_dir, manifest)
        loaded = {}
        for table_name, relative_path in CSV_TABLES.items():
            loaded[table_name] = import_csv_table(
                conn,
                snapshot_dir=snapshot_dir,
                table_name=table_name,
                relative_path=relative_path,
                snapshot_id=manifest.get("snapshot_id", ""),
                chunk_size=args.chunk_size,
            )
        create_indexes(conn)
        create_views(conn)
        ensure_maintenance_layer(conn)
        if not args.skip_analyze:
            conn.execute("ANALYZE")
            conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        summary = collect_summary(conn, db_path, snapshot_dir, manifest)
    finally:
        conn.close()
    summary_path = db_path.with_suffix(".summary.json")
    report_path = db_path.with_suffix(".report.md")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(summary, report_path)
    print(json.dumps({**summary, "summary_path": str(summary_path), "report_path": str(report_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
