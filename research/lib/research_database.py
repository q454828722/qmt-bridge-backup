"""SQLite maintenance helpers for the StarBridge research database."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable


MAINTENANCE_TABLES = (
    "maintenance_batches",
    "source_evidence",
    "source_conflicts",
    "repair_log",
    "dataset_watermarks",
    "field_overrides",
    "daily_bar_delta",
    "instrument_delta",
    "financial_statement_delta",
)
FACTOR_CACHE_TABLES = (
    "factor_cache_registry",
    "factor_price_cache_daily",
)


CORE_DAILY_BAR_COLUMNS = ("open", "high", "low", "close", "volume", "amount")
CORE_INSTRUMENT_COLUMNS = ("name", "exchange", "list_date", "delist_date")
VERIFIED_STATUSES = (
    "multi_source_verified",
    "qmt_verified",
    "public_verified",
    "manual_verified",
    "validated",
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_date_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return ""
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else text


def normalize_value(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def stable_hash(payload: dict[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def stable_id(prefix: str, payload: dict[str, Any]) -> str:
    return f"{prefix}_{stable_hash(payload)[:24]}"


def table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    try:
        rows = conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
    except sqlite3.DatabaseError:
        return set()
    return {str(row[1]) for row in rows}


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
            (table_name,),
        ).fetchone()
        is not None
    )


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _numeric_or_none(value: Any) -> float | None:
    text = normalize_value(value)
    if not text:
        return None
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def ensure_maintenance_schema(conn: sqlite3.Connection) -> None:
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
        CREATE TABLE IF NOT EXISTS maintenance_batches (
            batch_id TEXT PRIMARY KEY,
            domain TEXT NOT NULL,
            source TEXT NOT NULL,
            mode TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            row_count INTEGER NOT NULL DEFAULT 0,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS source_evidence (
            evidence_id TEXT PRIMARY KEY,
            batch_id TEXT,
            domain TEXT NOT NULL,
            source TEXT NOT NULL,
            target_table TEXT NOT NULL,
            symbol TEXT NOT NULL,
            record_date TEXT NOT NULL DEFAULT '',
            field_name TEXT NOT NULL,
            observed_value TEXT NOT NULL DEFAULT '',
            observed_numeric REAL,
            value_kind TEXT NOT NULL DEFAULT 'text',
            confidence REAL NOT NULL DEFAULT 1.0,
            validation_status TEXT NOT NULL DEFAULT 'observed',
            source_record_hash TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE(domain, source, target_table, symbol, record_date, field_name, source_record_hash)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS source_conflicts (
            conflict_id TEXT PRIMARY KEY,
            domain TEXT NOT NULL,
            target_table TEXT NOT NULL,
            symbol TEXT NOT NULL,
            record_date TEXT NOT NULL DEFAULT '',
            field_name TEXT NOT NULL,
            qmt_value TEXT NOT NULL DEFAULT '',
            candidate_values_json TEXT NOT NULL,
            sources_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            severity TEXT NOT NULL DEFAULT 'medium',
            detected_at TEXT NOT NULL,
            resolved_at TEXT,
            resolution_note TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS repair_log (
            repair_id TEXT PRIMARY KEY,
            batch_id TEXT,
            action TEXT NOT NULL,
            domain TEXT NOT NULL,
            target_table TEXT NOT NULL,
            symbol TEXT NOT NULL,
            record_date TEXT NOT NULL DEFAULT '',
            field_name TEXT NOT NULL,
            old_value TEXT NOT NULL DEFAULT '',
            new_value TEXT NOT NULL DEFAULT '',
            source_chain TEXT NOT NULL DEFAULT '',
            validation_status TEXT NOT NULL DEFAULT '',
            evidence_ids_json TEXT NOT NULL DEFAULT '[]',
            conflict_id TEXT,
            applied_at TEXT NOT NULL,
            applied_by TEXT NOT NULL DEFAULT 'data_specialist',
            notes TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dataset_watermarks (
            domain TEXT NOT NULL,
            source TEXT NOT NULL,
            target_table TEXT NOT NULL,
            last_success_date TEXT NOT NULL DEFAULT '',
            last_success_at TEXT NOT NULL DEFAULT '',
            batch_id TEXT,
            row_count INTEGER NOT NULL DEFAULT 0,
            notes TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(domain, source, target_table)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS field_overrides (
            override_id TEXT PRIMARY KEY,
            domain TEXT NOT NULL,
            target_table TEXT NOT NULL,
            symbol TEXT NOT NULL,
            record_date TEXT NOT NULL DEFAULT '',
            field_name TEXT NOT NULL,
            value_text TEXT NOT NULL DEFAULT '',
            value_numeric REAL,
            value_kind TEXT NOT NULL DEFAULT 'text',
            source_chain TEXT NOT NULL DEFAULT '',
            validation_status TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            evidence_ids_json TEXT NOT NULL DEFAULT '[]',
            batch_id TEXT,
            created_at TEXT NOT NULL,
            superseded_at TEXT,
            notes TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_bar_delta (
            symbol TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL,
            amount REAL,
            source_chain TEXT NOT NULL DEFAULT '',
            validation_status TEXT NOT NULL DEFAULT '',
            batch_id TEXT,
            evidence_ids_json TEXT NOT NULL DEFAULT '[]',
            raw_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL,
            PRIMARY KEY(symbol, trade_date)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS instrument_delta (
            symbol TEXT PRIMARY KEY,
            name TEXT,
            exchange TEXT,
            list_date TEXT,
            delist_date TEXT,
            source_chain TEXT NOT NULL DEFAULT '',
            validation_status TEXT NOT NULL DEFAULT '',
            batch_id TEXT,
            raw_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS financial_statement_delta (
            financial_table TEXT NOT NULL,
            symbol TEXT NOT NULL,
            report_date TEXT NOT NULL,
            announce_date TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL,
            source_chain TEXT NOT NULL DEFAULT '',
            validation_status TEXT NOT NULL DEFAULT '',
            batch_id TEXT,
            evidence_ids_json TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL,
            PRIMARY KEY(financial_table, symbol, report_date)
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_field_overrides_active
        ON field_overrides(domain, target_table, symbol, record_date, field_name)
        WHERE status = 'active'
        """
    )
    for table in (
        "maintenance_batches",
        "source_evidence",
        "repair_log",
        "dataset_watermarks",
        "field_overrides",
        "daily_bar_delta",
        "instrument_delta",
        "financial_statement_delta",
    ):
        conn.execute(f'CREATE INDEX IF NOT EXISTS "idx_{table}_batch" ON "{table}" (batch_id)')
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_source_evidence_lookup
        ON source_evidence(domain, target_table, symbol, record_date, field_name, source)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_source_conflicts_open
        ON source_conflicts(status, domain, target_table, symbol, record_date, field_name)
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_repair_log_target ON repair_log(target_table, symbol, record_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_daily_bar_delta_date_symbol ON daily_bar_delta(trade_date, symbol)")
    conn.executemany(
        "INSERT OR REPLACE INTO database_meta(key, value) VALUES (?, ?)",
        [
            ("maintenance_schema_version", "research-maintenance-v1"),
            ("maintenance_schema_updated_at", utc_now_iso()),
        ],
    )
    conn.commit()


def _create_latest_evidence_view(conn: sqlite3.Connection) -> None:
    conn.execute('DROP VIEW IF EXISTS "v_source_evidence_latest"')
    conn.execute(
        """
        CREATE VIEW v_source_evidence_latest AS
        SELECT *
        FROM (
            SELECT
                e.*,
                ROW_NUMBER() OVER (
                    PARTITION BY domain, source, target_table, symbol, record_date, field_name
                    ORDER BY observed_at DESC, evidence_id DESC
                ) AS rn
            FROM source_evidence e
        )
        WHERE rn = 1
        """
    )


def _daily_effective_select(conn: sqlite3.Connection) -> str:
    base_columns = list(table_columns(conn, "daily_bar"))
    if not base_columns:
        return """
        SELECT symbol, trade_date, open, high, low, close, volume, amount,
               source_chain, validation_status, batch_id, updated_at
        FROM daily_bar_delta
        """
    ordered_columns = [
        row[1] for row in conn.execute('PRAGMA table_info("daily_bar")').fetchall()
    ]
    select_parts = []
    union_parts = []
    for column in ordered_columns:
        if column == "symbol":
            select_parts.append("d.symbol AS symbol")
            union_parts.append("dd.symbol AS symbol")
        elif column == "trade_date":
            select_parts.append("d.trade_date AS trade_date")
            union_parts.append("dd.trade_date AS trade_date")
        elif column in CORE_DAILY_BAR_COLUMNS:
            select_parts.append(f"COALESCE(dd.{_quote(column)}, d.{_quote(column)}) AS {_quote(column)}")
            union_parts.append(f"dd.{_quote(column)} AS {_quote(column)}")
        else:
            select_parts.append(f"d.{_quote(column)} AS {_quote(column)}")
            union_parts.append(f"NULL AS {_quote(column)}")
    return f"""
        SELECT {", ".join(select_parts)}
        FROM daily_bar d
        LEFT JOIN daily_bar_delta dd
            ON dd.symbol = d.symbol
           AND CAST(dd.trade_date AS TEXT) = CAST(d.trade_date AS TEXT)
        UNION ALL
        SELECT {", ".join(union_parts)}
        FROM daily_bar_delta dd
        LEFT JOIN daily_bar d
            ON d.symbol = dd.symbol
           AND CAST(d.trade_date AS TEXT) = CAST(dd.trade_date AS TEXT)
        WHERE d.symbol IS NULL
        """


def _instrument_effective_select(conn: sqlite3.Connection) -> str:
    base_columns = list(table_columns(conn, "instrument"))
    if not base_columns:
        return """
        SELECT symbol, name, exchange, list_date, delist_date,
               source_chain, validation_status, batch_id, updated_at
        FROM instrument_delta
        """
    ordered_columns = [
        row[1] for row in conn.execute('PRAGMA table_info("instrument")').fetchall()
    ]
    select_parts = []
    union_parts = []
    for column in ordered_columns:
        if column == "symbol":
            select_parts.append("i.symbol AS symbol")
            union_parts.append("id.symbol AS symbol")
        elif column in CORE_INSTRUMENT_COLUMNS:
            select_parts.append(f"COALESCE(id.{_quote(column)}, i.{_quote(column)}) AS {_quote(column)}")
            union_parts.append(f"id.{_quote(column)} AS {_quote(column)}")
        else:
            select_parts.append(f"i.{_quote(column)} AS {_quote(column)}")
            union_parts.append(f"NULL AS {_quote(column)}")
    return f"""
        SELECT {", ".join(select_parts)}
        FROM instrument i
        LEFT JOIN instrument_delta id
            ON id.symbol = i.symbol
        UNION ALL
        SELECT {", ".join(union_parts)}
        FROM instrument_delta id
        LEFT JOIN instrument i
            ON i.symbol = id.symbol
        WHERE i.symbol IS NULL
        """


def create_maintenance_views(conn: sqlite3.Connection) -> None:
    for view in (
        "v_factor_ready_daily_effective",
        "v_daily_bar_effective",
        "v_instrument_effective",
        "v_data_maintenance_status",
        "v_open_source_conflicts",
        "v_verified_field_overrides",
        "v_source_evidence_latest",
    ):
        conn.execute(f'DROP VIEW IF EXISTS "{view}"')
    _create_latest_evidence_view(conn)
    conn.execute(
        """
        CREATE VIEW v_open_source_conflicts AS
        SELECT *
        FROM source_conflicts
        WHERE status = 'open'
        """
    )
    conn.execute(
        f"""
        CREATE VIEW v_verified_field_overrides AS
        SELECT *
        FROM field_overrides
        WHERE status = 'active'
          AND validation_status IN ({", ".join(repr(item) for item in VERIFIED_STATUSES)})
        """
    )
    conn.execute(f'CREATE VIEW v_daily_bar_effective AS {_daily_effective_select(conn)}')
    if table_exists(conn, "v_price_universe"):
        conn.execute(
            """
            CREATE VIEW v_factor_ready_daily_effective AS
            SELECT
                d.*,
                u.name,
                u.list_date,
                u.exchange
            FROM v_daily_bar_effective d
            JOIN v_price_universe u
                ON u.symbol = d.symbol
            """
        )
    else:
        conn.execute(
            """
            CREATE VIEW v_factor_ready_daily_effective AS
            SELECT *
            FROM v_daily_bar_effective
            """
        )
    conn.execute(f'CREATE VIEW v_instrument_effective AS {_instrument_effective_select(conn)}')
    conn.execute(
        """
        CREATE VIEW v_data_maintenance_status AS
        SELECT 'source_evidence' AS item, COUNT(*) AS rows FROM source_evidence
        UNION ALL
        SELECT 'source_conflicts_open' AS item, COUNT(*) AS rows FROM v_open_source_conflicts
        UNION ALL
        SELECT 'repair_log' AS item, COUNT(*) AS rows FROM repair_log
        UNION ALL
        SELECT 'field_overrides_active' AS item, COUNT(*) AS rows FROM field_overrides WHERE status = 'active'
        UNION ALL
        SELECT 'daily_bar_delta' AS item, COUNT(*) AS rows FROM daily_bar_delta
        UNION ALL
        SELECT 'instrument_delta' AS item, COUNT(*) AS rows FROM instrument_delta
        UNION ALL
        SELECT 'financial_statement_delta' AS item, COUNT(*) AS rows FROM financial_statement_delta
        """
    )
    conn.commit()


def ensure_maintenance_layer(conn: sqlite3.Connection) -> None:
    ensure_maintenance_schema(conn)
    create_maintenance_views(conn)


def ensure_factor_cache_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS factor_cache_registry (
            cache_name TEXT PRIMARY KEY,
            source_view TEXT NOT NULL,
            built_at TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.commit()


def build_factor_price_cache(
    conn: sqlite3.Connection,
    *,
    source_view: str = "v_factor_ready_daily_effective",
) -> int:
    """重建日线因子缓存表。

    缓存表是研究侧派生数据，便于三个因子专员并行读取常用窗口特征。
    它不回写 QMT 原始缓存，可随时从 ``source_view`` 重建。
    """

    if not table_exists(conn, source_view):
        raise ValueError(f"source view does not exist: {source_view}")
    source_columns = table_columns(conn, source_view)
    if "symbol" not in source_columns or "trade_date" not in source_columns or "close" not in source_columns:
        raise ValueError(f"{source_view} must include symbol, trade_date, and close")

    def text_expr(column: str, alias: str | None = None) -> str:
        output = alias or column
        if column in source_columns:
            return f"CAST({_quote(column)} AS TEXT) AS {_quote(output)}"
        return f"NULL AS {_quote(output)}"

    def real_value(column: str) -> str:
        if column in source_columns:
            return f"CAST({_quote(column)} AS REAL)"
        return "NULL"

    close_value = real_value("close")
    volume_value = real_value("volume")
    amount_value = real_value("amount")
    suspend_expr = text_expr("suspendFlag", "suspend_flag")
    ensure_factor_cache_schema(conn)
    temp_table = "factor_price_cache_daily__build"
    built_at = utc_now_iso()
    conn.execute(f'DROP TABLE IF EXISTS "{temp_table}"')
    conn.execute(
        f"""
        CREATE TABLE "{temp_table}" AS
        WITH base AS (
            SELECT
                symbol,
                CAST(trade_date AS TEXT) AS trade_date,
                {text_expr("name")},
                {text_expr("list_date")},
                {text_expr("exchange")},
                {close_value} AS close,
                {volume_value} AS volume,
                {amount_value} AS amount,
                {suspend_expr},
                LAG({close_value}, 1) OVER symbol_order AS prev_close_1,
                LAG({close_value}, 5) OVER symbol_order AS prev_close_5,
                LAG({close_value}, 20) OVER symbol_order AS prev_close_20,
                AVG({close_value}) OVER win_5 AS ma_5,
                AVG({close_value}) OVER win_20 AS ma_20,
                AVG({close_value}) OVER win_60 AS ma_60,
                AVG({close_value}) OVER win_120 AS ma_120,
                AVG({amount_value}) OVER win_20 AS amount_ma_20,
                AVG({volume_value}) OVER win_20 AS volume_ma_20,
                MAX({close_value}) OVER win_20 AS close_high_20,
                MIN({close_value}) OVER win_20 AS close_low_20,
                COUNT(*) OVER win_20 AS window_count_20
            FROM "{source_view}"
            WHERE {_quote("close")} IS NOT NULL
              AND {close_value} > 0
            WINDOW
                symbol_order AS (
                    PARTITION BY symbol
                    ORDER BY CAST(trade_date AS INTEGER)
                ),
                win_5 AS (
                    PARTITION BY symbol
                    ORDER BY CAST(trade_date AS INTEGER)
                    ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
                ),
                win_20 AS (
                    PARTITION BY symbol
                    ORDER BY CAST(trade_date AS INTEGER)
                    ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                ),
                win_60 AS (
                    PARTITION BY symbol
                    ORDER BY CAST(trade_date AS INTEGER)
                    ROWS BETWEEN 59 PRECEDING AND CURRENT ROW
                ),
                win_120 AS (
                    PARTITION BY symbol
                    ORDER BY CAST(trade_date AS INTEGER)
                    ROWS BETWEEN 119 PRECEDING AND CURRENT ROW
                )
        ),
        returns AS (
            SELECT
                *,
                close / NULLIF(prev_close_1, 0) - 1.0 AS return_1d,
                close / NULLIF(prev_close_5, 0) - 1.0 AS return_5d,
                close / NULLIF(prev_close_20, 0) - 1.0 AS return_20d,
                close / NULLIF(ma_20, 0) - 1.0 AS close_to_ma_20,
                close / NULLIF(ma_60, 0) - 1.0 AS close_to_ma_60,
                close / NULLIF(close_high_20, 0) - 1.0 AS close_to_high_20,
                close / NULLIF(close_low_20, 0) - 1.0 AS close_to_low_20
            FROM base
        ),
        vol AS (
            SELECT
                *,
                AVG(return_1d) OVER win_20 AS return_1d_mean_20,
                AVG(return_1d * return_1d) OVER win_20 AS return_1d_square_mean_20,
                AVG(return_1d) OVER win_60 AS return_1d_mean_60,
                AVG(return_1d * return_1d) OVER win_60 AS return_1d_square_mean_60
            FROM returns
            WINDOW
                win_20 AS (
                    PARTITION BY symbol
                    ORDER BY CAST(trade_date AS INTEGER)
                    ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                ),
                win_60 AS (
                    PARTITION BY symbol
                    ORDER BY CAST(trade_date AS INTEGER)
                    ROWS BETWEEN 59 PRECEDING AND CURRENT ROW
                )
        )
        SELECT
            symbol,
            trade_date,
            name,
            list_date,
            exchange,
            close,
            volume,
            amount,
            suspend_flag,
            return_1d,
            return_5d,
            return_20d,
            ma_5,
            ma_20,
            ma_60,
            ma_120,
            close_to_ma_20,
            close_to_ma_60,
            close_to_high_20,
            close_to_low_20,
            amount_ma_20,
            volume_ma_20,
            close_high_20,
            close_low_20,
            CASE
                WHEN return_1d_square_mean_20 IS NULL THEN NULL
                ELSE sqrt(max(return_1d_square_mean_20 - return_1d_mean_20 * return_1d_mean_20, 0))
            END AS return_1d_vol_20,
            CASE
                WHEN return_1d_square_mean_60 IS NULL THEN NULL
                ELSE sqrt(max(return_1d_square_mean_60 - return_1d_mean_60 * return_1d_mean_60, 0))
            END AS return_1d_vol_60,
            window_count_20,
            '{built_at}' AS built_at
        FROM vol
        """
    )
    row_count = conn.execute(f'SELECT COUNT(*) FROM "{temp_table}"').fetchone()[0]
    conn.execute('DROP TABLE IF EXISTS "factor_price_cache_daily"')
    conn.execute(f'ALTER TABLE "{temp_table}" RENAME TO "factor_price_cache_daily"')
    conn.execute(
        'CREATE INDEX "idx_factor_price_cache_daily_symbol_date" '
        'ON "factor_price_cache_daily" (symbol, trade_date)'
    )
    conn.execute(
        'CREATE INDEX "idx_factor_price_cache_daily_date_symbol" '
        'ON "factor_price_cache_daily" (trade_date, symbol)'
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO factor_cache_registry(
            cache_name, source_view, built_at, row_count, metadata_json
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            "factor_price_cache_daily",
            source_view,
            built_at,
            int(row_count),
            _json(
                {
                    "windows": [5, 20, 60, 120],
                    "fields": [
                        "return_1d",
                        "return_5d",
                        "return_20d",
                        "ma_5",
                        "ma_20",
                        "ma_60",
                        "ma_120",
                        "return_1d_vol_20",
                        "return_1d_vol_60",
                    ],
                }
            ),
        ),
    )
    conn.commit()
    return int(row_count)


def start_batch(
    conn: sqlite3.Connection,
    *,
    domain: str,
    source: str,
    mode: str,
    metadata: dict[str, Any] | None = None,
) -> str:
    started_at = utc_now_iso()
    batch_id = stable_id(
        "batch",
        {
            "domain": domain,
            "source": source,
            "mode": mode,
            "started_at": started_at,
            "metadata": metadata or {},
        },
    )
    conn.execute(
        """
        INSERT INTO maintenance_batches(
            batch_id, domain, source, mode, status, started_at, metadata_json
        ) VALUES (?, ?, ?, ?, 'running', ?, ?)
        """,
        (batch_id, domain, source, mode, started_at, _json(metadata or {})),
    )
    conn.commit()
    return batch_id


def finish_batch(conn: sqlite3.Connection, batch_id: str, *, row_count: int, status: str = "success") -> None:
    conn.execute(
        """
        UPDATE maintenance_batches
        SET status = ?, finished_at = ?, row_count = ?
        WHERE batch_id = ?
        """,
        (status, utc_now_iso(), row_count, batch_id),
    )
    conn.commit()


def insert_source_evidence(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> list[str]:
    evidence_ids: list[str] = []
    now = utc_now_iso()
    for row in rows:
        payload = {
            "batch_id": normalize_value(row.get("batch_id")),
            "domain": normalize_value(row.get("domain")),
            "source": normalize_value(row.get("source")),
            "target_table": normalize_value(row.get("target_table")),
            "symbol": normalize_value(row.get("symbol")).upper(),
            "record_date": normalize_date_text(row.get("record_date")),
            "field_name": normalize_value(row.get("field_name")),
            "observed_value": normalize_value(row.get("observed_value")),
        }
        if not payload["domain"] or not payload["source"] or not payload["symbol"] or not payload["field_name"]:
            continue
        source_record_hash = stable_hash({**payload, "metadata": row.get("metadata") or {}})
        evidence_id = stable_id("ev", {**payload, "source_record_hash": source_record_hash})
        evidence_ids.append(evidence_id)
        conn.execute(
            """
            INSERT OR IGNORE INTO source_evidence(
                evidence_id, batch_id, domain, source, target_table, symbol, record_date,
                field_name, observed_value, observed_numeric, value_kind, confidence,
                validation_status, source_record_hash, observed_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence_id,
                payload["batch_id"],
                payload["domain"],
                payload["source"],
                payload["target_table"],
                payload["symbol"],
                payload["record_date"],
                payload["field_name"],
                payload["observed_value"],
                _numeric_or_none(payload["observed_value"]),
                normalize_value(row.get("value_kind")) or "text",
                float(row.get("confidence", 1.0) or 1.0),
                normalize_value(row.get("validation_status")) or "observed",
                source_record_hash,
                normalize_value(row.get("observed_at")) or now,
                _json(row.get("metadata") or {}),
            ),
        )
    conn.commit()
    return evidence_ids


def detect_source_conflicts(conn: sqlite3.Connection) -> int:
    groups = conn.execute(
        """
        SELECT domain, target_table, symbol, record_date, field_name
        FROM v_source_evidence_latest
        GROUP BY domain, target_table, symbol, record_date, field_name
        HAVING COUNT(DISTINCT observed_value) > 1
        """
    ).fetchall()
    inserted = 0
    now = utc_now_iso()
    for domain, target_table, symbol, record_date, field_name in groups:
        rows = conn.execute(
            """
            SELECT source, observed_value
            FROM v_source_evidence_latest
            WHERE domain = ?
              AND target_table = ?
              AND symbol = ?
              AND record_date = ?
              AND field_name = ?
            ORDER BY source
            """,
            (domain, target_table, symbol, record_date, field_name),
        ).fetchall()
        values: dict[str, list[str]] = {}
        qmt_value = ""
        for source, observed_value in rows:
            value = normalize_value(observed_value)
            values.setdefault(value, []).append(source)
            if source == "qmt":
                qmt_value = value
        conflict_id = stable_id(
            "conflict",
            {
                "domain": domain,
                "target_table": target_table,
                "symbol": symbol,
                "record_date": record_date,
                "field_name": field_name,
                "values": values,
            },
        )
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO source_conflicts(
                conflict_id, domain, target_table, symbol, record_date, field_name,
                qmt_value, candidate_values_json, sources_json, detected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                conflict_id,
                domain,
                target_table,
                symbol,
                record_date,
                field_name,
                qmt_value,
                _json(sorted(values)),
                _json(values),
                now,
            ),
        )
        inserted += max(cursor.rowcount, 0)
    conn.commit()
    return inserted


def _current_daily_value(conn: sqlite3.Connection, symbol: str, trade_date: str, field_name: str) -> str:
    if not table_exists(conn, "v_daily_bar_effective"):
        return ""
    if field_name not in table_columns(conn, "v_daily_bar_effective"):
        return ""
    row = conn.execute(
        f"""
        SELECT {_quote(field_name)}
        FROM v_daily_bar_effective
        WHERE symbol = ?
          AND CAST(trade_date AS TEXT) = ?
        """,
        (symbol, trade_date),
    ).fetchone()
    return normalize_value(row[0]) if row else ""


def upsert_daily_bar_delta(
    conn: sqlite3.Connection,
    rows: Iterable[dict[str, Any]],
    *,
    source: str,
    source_chain: str,
    validation_status: str,
    batch_id: str,
    applied_by: str = "data_specialist",
    notes: str = "",
) -> int:
    written = 0
    now = utc_now_iso()
    for row in rows:
        symbol = normalize_value(row.get("symbol") or row.get("stock_code")).upper()
        trade_date = normalize_date_text(row.get("trade_date") or row.get("date"))
        if not symbol or not trade_date:
            continue
        payload = {column: _numeric_or_none(row.get(column)) for column in CORE_DAILY_BAR_COLUMNS}
        raw_json = _json(row)
        evidence_rows = [
            {
                "batch_id": batch_id,
                "domain": "daily_bar",
                "source": source,
                "target_table": "daily_bar_delta",
                "symbol": symbol,
                "record_date": trade_date,
                "field_name": column,
                "observed_value": normalize_value(row.get(column)),
                "value_kind": "numeric",
                "validation_status": validation_status,
                "metadata": {"source_chain": source_chain},
            }
            for column in CORE_DAILY_BAR_COLUMNS
            if normalize_value(row.get(column))
        ]
        evidence_ids = insert_source_evidence(conn, evidence_rows)
        for column in CORE_DAILY_BAR_COLUMNS:
            new_value = normalize_value(row.get(column))
            if not new_value:
                continue
            old_value = _current_daily_value(conn, symbol, trade_date, column)
            if old_value == new_value:
                continue
            repair_id = stable_id(
                "repair",
                {
                    "batch_id": batch_id,
                    "target_table": "daily_bar_delta",
                    "symbol": symbol,
                    "record_date": trade_date,
                    "field_name": column,
                    "new_value": new_value,
                },
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO repair_log(
                    repair_id, batch_id, action, domain, target_table, symbol, record_date,
                    field_name, old_value, new_value, source_chain, validation_status,
                    evidence_ids_json, applied_at, applied_by, notes
                ) VALUES (?, ?, 'upsert_daily_bar_delta', 'daily_bar', 'daily_bar_delta',
                          ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    repair_id,
                    batch_id,
                    symbol,
                    trade_date,
                    column,
                    old_value,
                    new_value,
                    source_chain,
                    validation_status,
                    _json(evidence_ids),
                    now,
                    applied_by,
                    notes,
                ),
            )
        conn.execute(
            """
            INSERT INTO daily_bar_delta(
                symbol, trade_date, open, high, low, close, volume, amount,
                source_chain, validation_status, batch_id, evidence_ids_json, raw_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, trade_date) DO UPDATE SET
                open = COALESCE(excluded.open, daily_bar_delta.open),
                high = COALESCE(excluded.high, daily_bar_delta.high),
                low = COALESCE(excluded.low, daily_bar_delta.low),
                close = COALESCE(excluded.close, daily_bar_delta.close),
                volume = COALESCE(excluded.volume, daily_bar_delta.volume),
                amount = COALESCE(excluded.amount, daily_bar_delta.amount),
                source_chain = excluded.source_chain,
                validation_status = excluded.validation_status,
                batch_id = excluded.batch_id,
                evidence_ids_json = excluded.evidence_ids_json,
                raw_json = excluded.raw_json,
                updated_at = excluded.updated_at
            """,
            (
                symbol,
                trade_date,
                payload["open"],
                payload["high"],
                payload["low"],
                payload["close"],
                payload["volume"],
                payload["amount"],
                source_chain,
                validation_status,
                batch_id,
                _json(evidence_ids),
                raw_json,
                now,
            ),
        )
        written += 1
    conn.commit()
    return written
