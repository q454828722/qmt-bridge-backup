from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from research.lib.research_database import (
    build_factor_price_cache,
    detect_source_conflicts,
    ensure_maintenance_layer,
    insert_source_evidence,
)
from research.lib.research_database_client import ResearchDatabaseClient


def _build_small_database(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        pd.DataFrame(
            [
                {
                    "symbol": "000001.SZ",
                    "trade_date": f"202604{day:02d}",
                    "close": float(index + 10),
                    "amount": float((index + 1) * 1000),
                    "volume": float((index + 1) * 100),
                }
                for index, day in enumerate(range(17, 22))
            ]
        ).to_sql("daily_bar", conn, index=False)
        ensure_maintenance_layer(conn)
        build_factor_price_cache(conn)
    finally:
        conn.close()


def test_readonly_client_loads_price_panel_from_factor_cache(tmp_path: Path) -> None:
    db_path = tmp_path / "research.sqlite"
    _build_small_database(db_path)

    with ResearchDatabaseClient(db_path) as client:
        status = client.database_status()
        frame = client.load_price_panel(
            symbols="000001.SZ",
            start_date="20260418",
            columns=["symbol", "trade_date", "close", "return_1d", "ma_5"],
        )

        assert status["query_only"] == 1
        assert status["factor_price_cache_rows"] == 5
        assert frame["trade_date"].tolist() == ["20260418", "20260419", "20260420", "20260421"]
        assert frame.loc[0, "return_1d"] == pytest.approx(0.1)
        with pytest.raises(sqlite3.OperationalError):
            client.connection.execute("CREATE TABLE should_fail(value TEXT)")


def test_readonly_client_reports_open_conflicts(tmp_path: Path) -> None:
    db_path = tmp_path / "research.sqlite"
    _build_small_database(db_path)
    conn = sqlite3.connect(db_path)
    try:
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
                    "observed_value": "14",
                },
                {
                    "domain": "daily_bar",
                    "source": "gm",
                    "target_table": "daily_bar",
                    "symbol": "000001.SZ",
                    "record_date": "20260421",
                    "field_name": "close",
                    "observed_value": "14.5",
                },
            ],
        )
        detect_source_conflicts(conn)
    finally:
        conn.close()

    with ResearchDatabaseClient(db_path) as client:
        conflicts = client.check_open_conflicts(symbols="000001.SZ", fields="close")

    assert len(conflicts) == 1
    assert conflicts.loc[0, "symbol"] == "000001.SZ"
