"""Read-only client for the StarBridge SQLite research database."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable, Sequence

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = ROOT / "data" / "research" / "starbridge_quant_research.sqlite"
DEFAULT_INDUSTRY_MAP_PATH = ROOT / "research" / "reference" / "qmt_gics4_industry_map.csv"


def normalize_symbols(symbols: Sequence[str] | str | None) -> tuple[str, ...]:
    if symbols is None:
        return ()
    if isinstance(symbols, str):
        return (symbols.upper(),)
    return tuple(str(symbol).upper() for symbol in symbols if str(symbol).strip())


def normalize_date(value: str | int | None) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else text


def _readonly_uri(path: Path) -> str:
    return f"{path.resolve().as_uri()}?mode=ro"


def connect_readonly(database_path: str | Path = DEFAULT_DB_PATH, *, timeout: float = 30.0) -> sqlite3.Connection:
    """Open a SQLite connection that cannot write to the research database."""

    path = Path(database_path)
    if not path.exists():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(_readonly_uri(path), uri=True, timeout=timeout)
    conn.execute("PRAGMA query_only=ON")
    conn.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
    return conn


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
            (name,),
        ).fetchone()
        is not None
    )


def table_columns(conn: sqlite3.Connection, name: str) -> tuple[str, ...]:
    return tuple(str(row[1]) for row in conn.execute(f'PRAGMA table_info("{name}")').fetchall())


def _query_filters(
    *,
    symbols: Sequence[str] | str | None = None,
    date_column: str = "",
    start_date: str | int | None = None,
    end_date: str | int | None = None,
) -> tuple[list[str], list[Any]]:
    where: list[str] = []
    params: list[Any] = []
    normalized_symbols = normalize_symbols(symbols)
    if normalized_symbols:
        where.append("symbol IN (" + ",".join(["?"] * len(normalized_symbols)) + ")")
        params.extend(normalized_symbols)
    start = normalize_date(start_date)
    end = normalize_date(end_date)
    if date_column and start:
        where.append(f"CAST({date_column} AS TEXT) >= ?")
        params.append(start)
    if date_column and end:
        where.append(f"CAST({date_column} AS TEXT) <= ?")
        params.append(end)
    return where, params


def _select_columns(conn: sqlite3.Connection, table_name: str, columns: Sequence[str] | None) -> str:
    available = set(table_columns(conn, table_name))
    if columns:
        missing = [column for column in columns if column not in available]
        if missing:
            raise ValueError(f"{table_name} missing columns: {', '.join(missing)}")
        selected = list(columns)
    else:
        selected = list(table_columns(conn, table_name))
    return ", ".join(f'"{column}"' for column in selected)


class ResearchDatabaseClient:
    """Small read-only helper for parallel factor research workers."""

    def __init__(
        self,
        database_path: str | Path = DEFAULT_DB_PATH,
        *,
        timeout: float = 30.0,
        industry_map_path: str | Path = DEFAULT_INDUSTRY_MAP_PATH,
    ) -> None:
        self.database_path = Path(database_path)
        self.timeout = timeout
        self.industry_map_path = Path(industry_map_path)
        self.conn: sqlite3.Connection | None = None

    def __enter__(self) -> "ResearchDatabaseClient":
        self.open()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def open(self) -> sqlite3.Connection:
        if self.conn is None:
            self.conn = connect_readonly(self.database_path, timeout=self.timeout)
        return self.conn

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    @property
    def connection(self) -> sqlite3.Connection:
        return self.open()

    def database_status(self) -> dict[str, Any]:
        conn = self.connection
        cache_rows = 0
        cache_built_at = ""
        if table_exists(conn, "factor_cache_registry"):
            row = conn.execute(
                """
                SELECT row_count, built_at
                FROM factor_cache_registry
                WHERE cache_name = 'factor_price_cache_daily'
                """
            ).fetchone()
            if row:
                cache_rows = int(row[0])
                cache_built_at = str(row[1])
        return {
            "database_path": str(self.database_path),
            "journal_mode": conn.execute("PRAGMA journal_mode").fetchone()[0],
            "query_only": conn.execute("PRAGMA query_only").fetchone()[0],
            "open_conflicts": conn.execute("SELECT COUNT(*) FROM v_open_source_conflicts").fetchone()[0]
            if table_exists(conn, "v_open_source_conflicts")
            else 0,
            "factor_price_cache_rows": cache_rows,
            "factor_price_cache_built_at": cache_built_at,
        }

    def check_open_conflicts(
        self,
        *,
        symbols: Sequence[str] | str | None = None,
        domains: Sequence[str] | str | None = None,
        fields: Sequence[str] | str | None = None,
    ) -> pd.DataFrame:
        conn = self.connection
        if not table_exists(conn, "v_open_source_conflicts"):
            return pd.DataFrame()
        where, params = _query_filters(symbols=symbols)
        normalized_domains = normalize_symbols(domains)
        if normalized_domains:
            where.append("domain IN (" + ",".join(["?"] * len(normalized_domains)) + ")")
            params.extend(item.lower() for item in normalized_domains)
        normalized_fields = normalize_symbols(fields)
        if normalized_fields:
            where.append("field_name IN (" + ",".join(["?"] * len(normalized_fields)) + ")")
            params.extend(item.lower() for item in normalized_fields)
        sql = "SELECT * FROM v_open_source_conflicts"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY detected_at DESC"
        return pd.read_sql_query(sql, conn, params=params)

    def load_price_panel(
        self,
        *,
        symbols: Sequence[str] | str | None = None,
        start_date: str | int | None = None,
        end_date: str | int | None = None,
        columns: Sequence[str] | None = None,
        use_factor_cache: bool = True,
    ) -> pd.DataFrame:
        conn = self.connection
        table_name = (
            "factor_price_cache_daily"
            if use_factor_cache and table_exists(conn, "factor_price_cache_daily")
            else "v_factor_ready_daily_effective"
        )
        selected = _select_columns(conn, table_name, columns)
        where, params = _query_filters(
            symbols=symbols,
            date_column="trade_date",
            start_date=start_date,
            end_date=end_date,
        )
        sql = f'SELECT {selected} FROM "{table_name}"'
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY symbol, CAST(trade_date AS INTEGER)"
        return pd.read_sql_query(sql, conn, params=params)

    def load_financial_panel(
        self,
        *,
        symbols: Sequence[str] | str | None = None,
        statement: str = "income",
        start_report_date: str | int | None = None,
        end_report_date: str | int | None = None,
        columns: Sequence[str] | None = None,
        universe: str = "fresh",
    ) -> pd.DataFrame:
        statement_tables = {
            "balance": "financial_balance",
            "income": "financial_income",
            "cashflow": "financial_cashflow",
        }
        statements = tuple(statement_tables) if statement == "all" else (statement,)
        frames: list[pd.DataFrame] = []
        for item in statements:
            table_name = statement_tables.get(item)
            if not table_name:
                raise ValueError(f"unsupported statement: {item}")
            selected = _select_columns(conn := self.connection, table_name, columns)
            where, params = _query_filters(
                symbols=symbols,
                date_column="report_date",
                start_date=start_report_date,
                end_date=end_report_date,
            )
            if universe in {"fresh", "latest"}:
                universe_view = (
                    "v_financial_fresh_universe"
                    if universe == "fresh"
                    else "v_financial_latest_available_universe"
                )
                if table_exists(conn, universe_view):
                    where.append(
                        f"""
                        EXISTS (
                            SELECT 1 FROM {universe_view} u
                            WHERE u.stock_code = {table_name}.symbol
                        )
                        """
                    )
            sql = f"SELECT {selected} FROM {table_name}"
            if where:
                sql += " WHERE " + " AND ".join(where)
            sql += " ORDER BY symbol, CAST(report_date AS INTEGER)"
            frame = pd.read_sql_query(sql, conn, params=params)
            frame.insert(0, "financial_table", item)
            frames.append(frame)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def load_style_panel(
        self,
        *,
        symbols: Sequence[str] | str | None = None,
        start_date: str | int | None = None,
        end_date: str | int | None = None,
        include_industry: bool = True,
    ) -> pd.DataFrame:
        columns = [
            "symbol",
            "trade_date",
            "name",
            "list_date",
            "exchange",
            "close",
            "amount",
            "volume",
            "return_1d",
            "return_20d",
            "ma_20",
            "ma_60",
            "close_to_ma_20",
            "return_1d_vol_20",
        ]
        available = set(table_columns(self.connection, "factor_price_cache_daily")) if table_exists(
            self.connection, "factor_price_cache_daily"
        ) else set(table_columns(self.connection, "v_factor_ready_daily_effective"))
        selected = [column for column in columns if column in available]
        frame = self.load_price_panel(
            symbols=symbols,
            start_date=start_date,
            end_date=end_date,
            columns=selected,
            use_factor_cache=True,
        )
        if include_industry and self.industry_map_path.exists() and not frame.empty:
            industry = pd.read_csv(self.industry_map_path, dtype=str, encoding="utf-8-sig")
            keep = [column for column in ("symbol", "industry", "sector_name") if column in industry.columns]
            if "symbol" in keep:
                frame = frame.merge(industry[keep].drop_duplicates("symbol"), on="symbol", how="left")
        return frame


def iter_chunks(frame: pd.DataFrame, *, chunk_size: int) -> Iterable[pd.DataFrame]:
    for start in range(0, len(frame), chunk_size):
        yield frame.iloc[start : start + chunk_size].copy()
