from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from research.lib.research_database import (
    detect_source_conflicts,
    ensure_maintenance_layer,
    finish_batch,
    insert_source_evidence,
    start_batch,
    upsert_daily_bar_delta,
)


def test_maintenance_layer_applies_daily_delta_without_touching_base_table(tmp_path: Path) -> None:
    db_path = tmp_path / "research.sqlite"
    conn = sqlite3.connect(db_path)
    try:
        pd.DataFrame(
            [
                {"symbol": "000001.SZ", "trade_date": "20260421", "open": 1.0, "close": 1.1},
            ]
        ).to_sql("daily_bar", conn, index=False)
        ensure_maintenance_layer(conn)
        batch_id = start_batch(conn, domain="daily_bar", source="gm", mode="apply_daily_delta")
        written = upsert_daily_bar_delta(
            conn,
            [{"symbol": "000001.SZ", "trade_date": "20260421", "close": "1.2"}],
            source="gm",
            source_chain="qmt,gm,akshare",
            validation_status="multi_source_verified",
            batch_id=batch_id,
        )
        finish_batch(conn, batch_id, row_count=written)
        ensure_maintenance_layer(conn)

        assert written == 1
        assert conn.execute("SELECT close FROM daily_bar").fetchone()[0] == 1.1
        assert conn.execute("SELECT close FROM v_daily_bar_effective").fetchone()[0] == 1.2
        assert conn.execute("SELECT COUNT(*) FROM repair_log").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM source_evidence").fetchone()[0] == 1
    finally:
        conn.close()


def test_maintenance_layer_detects_source_conflict(tmp_path: Path) -> None:
    db_path = tmp_path / "research.sqlite"
    conn = sqlite3.connect(db_path)
    try:
        ensure_maintenance_layer(conn)
        insert_source_evidence(
            conn,
            [
                {
                    "domain": "daily_bar",
                    "source": "qmt",
                    "target_table": "daily_bar",
                    "symbol": "000001.SZ",
                    "record_date": "20260421",
                    "field_name": "close",
                    "observed_value": "1.1",
                },
                {
                    "domain": "daily_bar",
                    "source": "gm",
                    "target_table": "daily_bar",
                    "symbol": "000001.SZ",
                    "record_date": "20260421",
                    "field_name": "close",
                    "observed_value": "1.2",
                },
            ],
        )
        conflicts = detect_source_conflicts(conn)
        ensure_maintenance_layer(conn)

        assert conflicts == 1
        assert conn.execute("SELECT COUNT(*) FROM v_open_source_conflicts").fetchone()[0] == 1
    finally:
        conn.close()
