from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pandas as pd


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "build_research_database.py"
    spec = importlib.util.spec_from_file_location("build_research_database", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")


def test_build_research_database_creates_factor_views(tmp_path: Path) -> None:
    module = _load_module()
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    manifest = {
        "snapshot_id": "unit_snapshot",
        "snapshot_name": "unit",
        "storage_format": "csv",
        "created_at": "2026-04-25T00:00:00+00:00",
    }
    (snapshot / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    _write_csv(
        snapshot / "daily_bar.csv",
        [
            {"symbol": "000001.SZ", "trade_date": "20260421", "open": 1.0, "close": 1.1},
            {"symbol": "000002.SZ", "trade_date": "20260421", "open": 2.0, "close": 2.1},
        ],
    )
    _write_csv(
        snapshot / "instrument.csv",
        [
            {"symbol": "000001.SZ", "name": "平安银行", "list_date": "19910403", "exchange": "SZ"},
            {"symbol": "000002.SZ", "name": "万科A", "list_date": "19910129", "exchange": "SZ"},
        ],
    )
    _write_csv(snapshot / "daily_bar_missing_symbols.csv", [{"symbol": "", "name": "", "list_date": ""}])
    _write_csv(snapshot / "financial" / "Balance.csv", [{"symbol": "000001.SZ", "report_date": "20251231"}])
    _write_csv(snapshot / "financial" / "Income.csv", [{"symbol": "000001.SZ", "report_date": "20251231"}])
    _write_csv(snapshot / "financial" / "CashFlow.csv", [{"symbol": "000001.SZ", "report_date": "20251231"}])
    _write_csv(
        snapshot / "clean_artifacts" / "quant_backtest_prefilter.csv",
        [
            {
                "stock_code": "000001.SZ",
                "name": "平安银行",
                "include_price_factors": "1",
                "exclude_from_price_backtest": "0",
                "exclude_reason": "",
            },
            {
                "stock_code": "000002.SZ",
                "name": "万科A",
                "include_price_factors": "0",
                "exclude_from_price_backtest": "1",
                "exclude_reason": "filtered",
            },
        ],
    )
    _write_csv(
        snapshot / "clean_artifacts" / "quant_financial_universe_fresh_only.csv",
        [{"stock_code": "000001.SZ", "include_price_factors": "1", "financial_status": "ok"}],
    )
    _write_csv(
        snapshot / "clean_artifacts" / "quant_financial_universe_latest_available.csv",
        [{"stock_code": "000001.SZ", "include_price_factors": "1", "financial_status": "stale"}],
    )
    for name in (
        "quant_data_clean_universe.csv",
        "cache_progress_kline.csv",
        "cache_progress_financial.csv",
        "cache_progress_kline_issues.csv",
        "cache_progress_financial_issues.csv",
        "a_share_universe.csv",
    ):
        _write_csv(snapshot / "clean_artifacts" / name, [{"stock_code": "000001.SZ"}])

    db_path = tmp_path / "research.sqlite"
    conn = module.connect_database(db_path, replace=True)
    try:
        module.write_meta_tables(conn, snapshot, manifest)
        for table, relative_path in module.CSV_TABLES.items():
            module.import_csv_table(
                conn,
                snapshot_dir=snapshot,
                table_name=table,
                relative_path=relative_path,
                snapshot_id=manifest["snapshot_id"],
                chunk_size=2,
            )
        module.create_indexes(conn)
        module.create_views(conn)
        module.ensure_maintenance_layer(conn)
        count = conn.execute("SELECT COUNT(*) FROM v_factor_ready_daily").fetchone()[0]
        effective_count = conn.execute("SELECT COUNT(*) FROM v_factor_ready_daily_effective").fetchone()[0]
        symbol = conn.execute("SELECT symbol FROM v_price_universe").fetchone()[0]
    finally:
        conn.close()

    assert count == 1
    assert effective_count == 1
    assert symbol == "000001.SZ"
    with sqlite3.connect(db_path) as check:
        assert check.execute("SELECT COUNT(*) FROM daily_bar").fetchone()[0] == 2
        assert check.execute("SELECT COUNT(*) FROM maintenance_batches").fetchone()[0] == 0
