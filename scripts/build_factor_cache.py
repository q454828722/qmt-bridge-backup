#!/usr/bin/env python3
"""Build research-side factor cache tables for parallel factor agents."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from time import perf_counter


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.lib.research_database import build_factor_price_cache, ensure_maintenance_layer  # noqa: E402


DEFAULT_DB_PATH = ROOT / "data" / "research" / "starbridge_quant_research.sqlite"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--source-view", default="v_factor_ready_daily_effective")
    parser.add_argument("--skip-analyze", action="store_true")
    return parser.parse_args()


def connect(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-300000")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def main() -> int:
    args = parse_args()
    db_path = Path(args.database_path)
    started = perf_counter()
    conn = connect(db_path)
    try:
        ensure_maintenance_layer(conn)
        row_count = build_factor_price_cache(conn, source_view=args.source_view)
        if not args.skip_analyze:
            conn.execute("ANALYZE factor_price_cache_daily")
            conn.commit()
        registry = conn.execute(
            """
            SELECT cache_name, source_view, built_at, row_count
            FROM factor_cache_registry
            WHERE cache_name = 'factor_price_cache_daily'
            """
        ).fetchone()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    result = {
        "database_path": str(db_path),
        "cache_name": registry[0] if registry else "factor_price_cache_daily",
        "source_view": registry[1] if registry else args.source_view,
        "built_at": registry[2] if registry else "",
        "row_count": int(registry[3]) if registry else row_count,
        "elapsed_seconds": round(perf_counter() - started, 3),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
