"""Unified research data client for QMT, Tushare, and AkShare.

Design rules:

1. QMT is the primary source for market bars and financial statements.
2. Tushare and AkShare are explicit public-reference sources, not silent replacements.
3. Fallback is opt-in and every returned dataset carries source metadata.
4. Reconciliation is a first-class workflow, separate from primary research reads.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Sequence

import pandas as pd

from starbridge_quant.client_factory import make_starbridge_client


class DataDomain(str, Enum):
    DAILY_BAR = "daily_bar"
    FINANCIAL = "financial"
    INSTRUMENT = "instrument"


class SourceName(str, Enum):
    QMT = "qmt"
    TUSHARE = "tushare"
    AKSHARE = "akshare"


@dataclass
class DomainPolicy:
    primary: SourceName
    fallbacks: tuple[SourceName, ...] = ()
    compare_fields: tuple[str, ...] = ()


@dataclass
class TabularDataset:
    domain: DataDomain
    source: SourceName
    data: pd.DataFrame
    requested_symbols: tuple[str, ...] = ()
    coverage_symbols: tuple[str, ...] = ()
    asof_date: str = ""
    fetch_time: str = ""
    version: str = "research-v1"
    is_fallback: bool = False
    quality_flags: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return self.data.empty


@dataclass
class FinancialDataset:
    source: SourceName
    tables: dict[str, pd.DataFrame]
    requested_symbols: tuple[str, ...] = ()
    coverage_symbols: tuple[str, ...] = ()
    asof_date: str = ""
    fetch_time: str = ""
    version: str = "research-v1"
    is_fallback: bool = False
    quality_flags: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not any(not frame.empty for frame in self.tables.values())


@dataclass
class ComparisonReport:
    domain: DataDomain
    primary_source: SourceName
    summaries: pd.DataFrame
    mismatches: pd.DataFrame
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def has_issues(self) -> bool:
        return not self.mismatches.empty


@dataclass
class SnapshotBundle:
    snapshot_id: str
    snapshot_dir: Path
    manifest_path: Path
    manifest: dict[str, Any]
    daily_bars: TabularDataset | None = None
    instrument: TabularDataset | None = None
    financials: FinancialDataset | None = None


@dataclass
class SnapshotDiffReport:
    left_snapshot_id: str
    right_snapshot_id: str
    dataset_summary: pd.DataFrame
    row_changes: pd.DataFrame
    field_changes: pd.DataFrame
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def has_changes(self) -> bool:
        if not self.dataset_summary.empty and "has_changes" in self.dataset_summary.columns:
            return bool(self.dataset_summary["has_changes"].astype(bool).any())
        return (not self.row_changes.empty) or (not self.field_changes.empty)


@dataclass
class SnapshotDiffArtifact:
    diff_id: str
    diff_dir: Path
    manifest_path: Path
    top_changes_path: Path
    manifest: dict[str, Any]
    report: SnapshotDiffReport

    @property
    def has_changes(self) -> bool:
        return self.report.has_changes


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _local_snapshot_stamp() -> str:
    return datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")


def _ensure_symbol_list(symbols: Sequence[str] | str) -> list[str]:
    if isinstance(symbols, str):
        return [symbols]
    return [symbol for symbol in symbols if symbol]


def _normalize_date_text(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return ""
    text = text.replace("-", "").replace("/", "").replace(":", "").replace(" ", "")
    return text[:8] if len(text) >= 8 else text


def _normalize_date_series(series: pd.Series) -> pd.Series:
    return series.map(_normalize_date_text)


def _coverage_symbols(frame: pd.DataFrame) -> tuple[str, ...]:
    if frame.empty or "symbol" not in frame.columns:
        return ()
    return tuple(sorted(frame["symbol"].dropna().astype(str).unique().tolist()))


def _max_asof_date(frame: pd.DataFrame, candidates: Sequence[str]) -> str:
    for column in candidates:
        if column in frame.columns and not frame.empty:
            values = _normalize_date_series(frame[column])
            values = values[values != ""]
            if not values.empty:
                return values.max()
    return ""


def _ts_to_simple_symbol(symbol: str) -> str:
    return symbol.split(".", 1)[0]


def _slugify_snapshot_name(value: str) -> str:
    slug = "".join(ch.lower() if ch.isalnum() else "_" for ch in value.strip())
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug.strip("_") or "research_snapshot"


def _json_safe(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _normalize_qmt_bar_frame(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    result = frame.copy()
    if not isinstance(result.index, pd.RangeIndex):
        result = result.reset_index()
    rename_map = {}
    if "date" in result.columns:
        rename_map["date"] = "trade_date"
    elif "time" in result.columns:
        rename_map["time"] = "trade_date"
    elif "index" in result.columns:
        rename_map["index"] = "trade_date"
    result = result.rename(columns=rename_map)
    if "trade_date" in result.columns:
        result["trade_date"] = _normalize_date_series(result["trade_date"])
    result.insert(0, "symbol", symbol)
    ordered = ["symbol", "trade_date", "open", "high", "low", "close", "volume", "amount"]
    keep = [column for column in ordered if column in result.columns]
    extra = [column for column in result.columns if column not in keep]
    return result[keep + extra]


def _normalize_tushare_bar_frame(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result = result.rename(columns={"ts_code": "symbol"})
    if "trade_date" in result.columns:
        result["trade_date"] = _normalize_date_series(result["trade_date"])
    ordered = ["symbol", "trade_date", "open", "high", "low", "close", "vol", "amount"]
    rename_map = {"vol": "volume"}
    result = result.rename(columns=rename_map)
    keep = [column for column in ["symbol", "trade_date", "open", "high", "low", "close", "volume", "amount"] if column in result.columns]
    extra = [column for column in result.columns if column not in keep]
    return result[keep + extra]


def _normalize_akshare_bar_frame(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    result = frame.copy()
    rename_map = {
        "日期": "trade_date",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "amount",
        "振幅": "amplitude",
        "涨跌幅": "pct_change",
        "涨跌额": "change",
        "换手率": "turnover_rate",
    }
    result = result.rename(columns=rename_map)
    if "trade_date" in result.columns:
        result["trade_date"] = _normalize_date_series(result["trade_date"])
    result.insert(0, "symbol", symbol)
    keep = [column for column in ["symbol", "trade_date", "open", "high", "low", "close", "volume", "amount"] if column in result.columns]
    extra = [column for column in result.columns if column not in keep]
    return result[keep + extra]


def _normalize_instrument_frame(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    rename_map = {
        "ts_code": "symbol",
        "InstrumentName": "name",
        "OpenDate": "list_date",
        "ExpireDate": "delist_date",
    }
    result = result.rename(columns=rename_map)
    if "list_date" in result.columns:
        result["list_date"] = _normalize_date_series(result["list_date"])
    if "delist_date" in result.columns:
        result["delist_date"] = _normalize_date_series(result["delist_date"])
    if "symbol" in result.columns:
        result["exchange"] = result["symbol"].astype(str).str.split(".").str[-1]
    ordered = ["symbol", "name", "list_date", "delist_date", "exchange"]
    keep = [column for column in ordered if column in result.columns]
    extra = [column for column in result.columns if column not in keep]
    return result[keep + extra]


def _normalize_financial_frame(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    result = frame.copy()
    result.insert(0, "symbol", symbol)
    rename_map = {
        "m_timetag": "report_date",
        "m_anntime": "announce_date",
    }
    result = result.rename(columns=rename_map)
    if "report_date" in result.columns:
        result["report_date"] = _normalize_date_series(result["report_date"])
    if "announce_date" in result.columns:
        result["announce_date"] = _normalize_date_series(result["announce_date"])
    ordered = ["symbol", "report_date", "announce_date"]
    keep = [column for column in ordered if column in result.columns]
    extra = [column for column in result.columns if column not in keep]
    return result[keep + extra]


def _empty_tabular_dataset(
    domain: DataDomain,
    source: SourceName,
    requested_symbols: Sequence[str],
    *,
    is_fallback: bool = False,
    quality_flags: Iterable[str] = (),
    metadata: dict[str, Any] | None = None,
) -> TabularDataset:
    return TabularDataset(
        domain=domain,
        source=source,
        data=pd.DataFrame(),
        requested_symbols=tuple(requested_symbols),
        coverage_symbols=(),
        asof_date="",
        fetch_time=_utc_now_iso(),
        is_fallback=is_fallback,
        quality_flags=tuple(quality_flags),
        metadata=metadata or {},
    )


def _write_frame(frame: pd.DataFrame, path: Path, storage_format: str) -> None:
    if storage_format == "parquet":
        frame.to_parquet(path, index=False)
        return
    if storage_format == "csv":
        frame.to_csv(path, index=False)
        return
    raise ValueError(f"Unsupported storage_format: {storage_format}")


def _read_frame(path: Path, storage_format: str) -> pd.DataFrame:
    if storage_format == "parquet":
        return pd.read_parquet(path)
    if storage_format == "csv":
        return pd.read_csv(path)
    raise ValueError(f"Unsupported storage_format: {storage_format}")


def _storage_suffix(storage_format: str) -> str:
    if storage_format == "parquet":
        return "parquet"
    if storage_format == "csv":
        return "csv"
    raise ValueError(f"Unsupported storage_format: {storage_format}")


def _dataset_manifest(dataset: TabularDataset, relative_path: str) -> dict[str, Any]:
    return {
        "domain": dataset.domain.value,
        "source": dataset.source.value,
        "relative_path": relative_path,
        "rows": int(len(dataset.data)),
        "columns": list(dataset.data.columns),
        "requested_symbols": list(dataset.requested_symbols),
        "coverage_symbols": list(dataset.coverage_symbols),
        "asof_date": dataset.asof_date,
        "fetch_time": dataset.fetch_time,
        "version": dataset.version,
        "is_fallback": dataset.is_fallback,
        "quality_flags": list(dataset.quality_flags),
        "metadata": _json_safe(dataset.metadata),
    }


def _financial_manifest(dataset: FinancialDataset, table_paths: dict[str, str]) -> dict[str, Any]:
    table_rows = {
        table: {
            "relative_path": table_paths[table],
            "rows": int(len(frame)),
            "columns": list(frame.columns),
        }
        for table, frame in dataset.tables.items()
    }
    return {
        "domain": DataDomain.FINANCIAL.value,
        "source": dataset.source.value,
        "tables": table_rows,
        "requested_symbols": list(dataset.requested_symbols),
        "coverage_symbols": list(dataset.coverage_symbols),
        "asof_date": dataset.asof_date,
        "fetch_time": dataset.fetch_time,
        "version": dataset.version,
        "is_fallback": dataset.is_fallback,
        "quality_flags": list(dataset.quality_flags),
        "metadata": _json_safe(dataset.metadata),
    }


def _tabular_from_manifest(snapshot_dir: Path, dataset_info: dict[str, Any], storage_format: str) -> TabularDataset:
    path = snapshot_dir / dataset_info["relative_path"]
    data = _read_frame(path, storage_format)
    return TabularDataset(
        domain=DataDomain(dataset_info["domain"]),
        source=SourceName(dataset_info["source"]),
        data=data,
        requested_symbols=tuple(dataset_info.get("requested_symbols", [])),
        coverage_symbols=tuple(dataset_info.get("coverage_symbols", [])),
        asof_date=dataset_info.get("asof_date", ""),
        fetch_time=dataset_info.get("fetch_time", ""),
        version=dataset_info.get("version", "research-v1"),
        is_fallback=bool(dataset_info.get("is_fallback", False)),
        quality_flags=tuple(dataset_info.get("quality_flags", [])),
        metadata=dataset_info.get("metadata", {}),
    )


def _financial_from_manifest(snapshot_dir: Path, dataset_info: dict[str, Any], storage_format: str) -> FinancialDataset:
    tables = {
        table: _read_frame(snapshot_dir / table_info["relative_path"], storage_format)
        for table, table_info in dataset_info.get("tables", {}).items()
    }
    return FinancialDataset(
        source=SourceName(dataset_info["source"]),
        tables=tables,
        requested_symbols=tuple(dataset_info.get("requested_symbols", [])),
        coverage_symbols=tuple(dataset_info.get("coverage_symbols", [])),
        asof_date=dataset_info.get("asof_date", ""),
        fetch_time=dataset_info.get("fetch_time", ""),
        version=dataset_info.get("version", "research-v1"),
        is_fallback=bool(dataset_info.get("is_fallback", False)),
        quality_flags=tuple(dataset_info.get("quality_flags", [])),
        metadata=dataset_info.get("metadata", {}),
    )


def load_snapshot(snapshot_path: str | Path) -> SnapshotBundle:
    snapshot_dir = Path(snapshot_path).expanduser()
    manifest_path = snapshot_dir / "manifest.json" if snapshot_dir.is_dir() else snapshot_dir
    if manifest_path.name != "manifest.json":
        raise FileNotFoundError(f"Expected manifest.json or snapshot directory, got: {snapshot_path}")
    snapshot_dir = manifest_path.parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    storage_format = manifest.get("storage_format", "parquet")
    datasets = manifest.get("datasets", {})

    daily_bars = (
        _tabular_from_manifest(snapshot_dir, datasets["daily_bar"], storage_format)
        if "daily_bar" in datasets
        else None
    )
    instrument = (
        _tabular_from_manifest(snapshot_dir, datasets["instrument"], storage_format)
        if "instrument" in datasets
        else None
    )
    financials = (
        _financial_from_manifest(snapshot_dir, datasets["financial"], storage_format)
        if "financial" in datasets
        else None
    )

    return SnapshotBundle(
        snapshot_id=manifest["snapshot_id"],
        snapshot_dir=snapshot_dir,
        manifest_path=manifest_path,
        manifest=manifest,
        daily_bars=daily_bars,
        instrument=instrument,
        financials=financials,
    )


def _diff_report_manifest(report: SnapshotDiffReport, relative_paths: dict[str, str]) -> dict[str, Any]:
    summary = report.dataset_summary.copy()
    changed_dataset_count = (
        int(summary["has_changes"].astype(bool).sum())
        if not summary.empty and "has_changes" in summary.columns
        else 0
    )
    return {
        "left_snapshot_id": report.left_snapshot_id,
        "right_snapshot_id": report.right_snapshot_id,
        "has_changes": report.has_changes,
        "dataset_count": int(len(summary)),
        "changed_dataset_count": changed_dataset_count,
        "row_change_rows": int(len(report.row_changes)),
        "field_change_rows": int(len(report.field_changes)),
        "files": relative_paths,
        "metadata": _json_safe(report.metadata),
    }


def _markdown_table(frame: pd.DataFrame, columns: Sequence[str]) -> str:
    available = [column for column in columns if column in frame.columns]
    if not available or frame.empty:
        return "_none_"
    subset = frame[available].fillna("").astype(str)
    header = "| " + " | ".join(available) + " |"
    divider = "| " + " | ".join(["---"] * len(available)) + " |"
    rows = ["| " + " | ".join(row) + " |" for row in subset.values.tolist()]
    return "\n".join([header, divider, *rows])


def _top_row_change_samples(frame: pd.DataFrame, limit: int = 8) -> pd.DataFrame:
    sample_columns = [column for column in ("dataset", "change_type", "symbol", "trade_date", "report_date", "announce_date", "index") if column in frame.columns]
    return frame[sample_columns].head(limit) if sample_columns else pd.DataFrame()


def _top_field_change_summary(frame: pd.DataFrame, limit: int = 10) -> pd.DataFrame:
    if frame.empty or "dataset" not in frame.columns or "field" not in frame.columns:
        return pd.DataFrame()
    summary = (
        frame.groupby(["dataset", "field"], dropna=False)
        .size()
        .reset_index(name="changed_cells")
        .sort_values(["changed_cells", "dataset", "field"], ascending=[False, True, True])
        .head(limit)
    )
    return summary


def _top_field_change_samples(frame: pd.DataFrame, limit: int = 8) -> pd.DataFrame:
    sample_columns = [
        column
        for column in ("dataset", "symbol", "trade_date", "report_date", "announce_date", "index", "field", "primary_value", "candidate_value")
        if column in frame.columns
    ]
    return frame[sample_columns].head(limit) if sample_columns else pd.DataFrame()


def _render_top_changes_md(report: SnapshotDiffReport, manifest: dict[str, Any]) -> str:
    lines = [
        "# Top Changes",
        "",
        f"- Diff ID: `{manifest['diff_id']}`",
        f"- Left Snapshot: `{report.left_snapshot_id}`",
        f"- Right Snapshot: `{report.right_snapshot_id}`",
        f"- Generated At: `{manifest['created_at']}`",
        f"- Has Changes: `{'yes' if report.has_changes else 'no'}`",
        f"- Dataset Count: `{manifest['report']['dataset_count']}`",
        f"- Changed Dataset Count: `{manifest['report']['changed_dataset_count']}`",
        f"- Row Change Rows: `{manifest['report']['row_change_rows']}`",
        f"- Field Change Rows: `{manifest['report']['field_change_rows']}`",
        "",
    ]

    changed_summary = report.dataset_summary
    if "has_changes" in changed_summary.columns:
        changed_summary = changed_summary[changed_summary["has_changes"].astype(bool)].copy()

    lines.append("## Changed Datasets")
    lines.append("")
    if changed_summary.empty:
        lines.append("本次 diff 没有发现数据变化。")
        lines.append("")
        return "\n".join(lines)

    dataset_columns = [
        "dataset",
        "left_rows",
        "right_rows",
        "left_only_keys",
        "right_only_keys",
        "field_mismatch_cells",
        "changed_keys",
    ]
    lines.append(_markdown_table(changed_summary, dataset_columns))
    lines.append("")

    if not report.row_changes.empty:
        lines.append("## Top Row Changes")
        lines.append("")
        row_samples = _top_row_change_samples(report.row_changes)
        lines.append(_markdown_table(row_samples, row_samples.columns.tolist()))
        lines.append("")

    if not report.field_changes.empty:
        lines.append("## Top Field Changes")
        lines.append("")
        field_summary = _top_field_change_summary(report.field_changes)
        lines.append(_markdown_table(field_summary, field_summary.columns.tolist()))
        lines.append("")
        field_samples = _top_field_change_samples(report.field_changes)
        lines.append("### Samples")
        lines.append("")
        lines.append(_markdown_table(field_samples, field_samples.columns.tolist()))
        lines.append("")

    return "\n".join(lines)


def _coerce_snapshot_bundle(snapshot: str | Path | SnapshotBundle) -> SnapshotBundle:
    if isinstance(snapshot, SnapshotBundle):
        return snapshot
    return load_snapshot(snapshot)


def _resolve_compare_fields(
    left: pd.DataFrame,
    right: pd.DataFrame,
    *,
    join_keys: Sequence[str],
    requested_fields: Sequence[str] | None = None,
) -> list[str]:
    if requested_fields is not None:
        return [field for field in requested_fields if field in left.columns and field in right.columns]
    return [
        column
        for column in left.columns
        if column in right.columns and column not in join_keys
    ]


def _presence_diff(
    left: pd.DataFrame,
    right: pd.DataFrame,
    *,
    join_keys: Sequence[str],
) -> tuple[dict[str, int], pd.DataFrame]:
    left_keys = (
        left[list(join_keys)].drop_duplicates()
        if not left.empty and all(key in left.columns for key in join_keys)
        else pd.DataFrame(columns=list(join_keys))
    )
    right_keys = (
        right[list(join_keys)].drop_duplicates()
        if not right.empty and all(key in right.columns for key in join_keys)
        else pd.DataFrame(columns=list(join_keys))
    )

    merged = left_keys.merge(
        right_keys,
        on=list(join_keys),
        how="outer",
        indicator=True,
    )
    summary = {
        "shared_keys": int((merged["_merge"] == "both").sum()),
        "left_only_keys": int((merged["_merge"] == "left_only").sum()),
        "right_only_keys": int((merged["_merge"] == "right_only").sum()),
    }
    changes = merged.loc[merged["_merge"] != "both", list(join_keys) + ["_merge"]].copy()
    if not changes.empty:
        changes = changes.rename(columns={"_merge": "change_type"})
        changes["change_type"] = changes["change_type"].map(
            {"left_only": "only_in_left", "right_only": "only_in_right"}
        )
    return summary, changes


def _changed_key_count(
    join_keys: Sequence[str],
    row_changes: pd.DataFrame,
    field_changes: pd.DataFrame,
) -> int:
    frames = []
    if not row_changes.empty:
        frames.append(row_changes[list(join_keys)].drop_duplicates())
    if not field_changes.empty:
        frames.append(field_changes[list(join_keys)].drop_duplicates())
    if not frames:
        return 0
    return int(pd.concat(frames, ignore_index=True).drop_duplicates().shape[0])


def _resolve_financial_join_keys(left: pd.DataFrame, right: pd.DataFrame) -> tuple[str, ...]:
    join_keys = ["symbol", "report_date"]
    for candidate in ("announce_date", "index"):
        if candidate in left.columns and candidate in right.columns:
            join_keys.append(candidate)
    return tuple(join_keys)


def _diff_dataset_frames(
    dataset_name: str,
    left: pd.DataFrame,
    right: pd.DataFrame,
    *,
    join_keys: Sequence[str],
    left_snapshot_id: str,
    right_snapshot_id: str,
    requested_fields: Sequence[str] | None = None,
    abs_tolerance: float = 1e-8,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    compare_fields = _resolve_compare_fields(
        left,
        right,
        join_keys=join_keys,
        requested_fields=requested_fields,
    )
    presence_summary, row_changes = _presence_diff(left, right, join_keys=join_keys)
    _, field_changes = _compare_frames(
        left,
        right,
        join_keys=join_keys,
        fields=compare_fields,
        primary_source=SourceName.QMT,
        candidate_source=SourceName.QMT,
        abs_tolerance=abs_tolerance,
    )

    if not row_changes.empty:
        row_changes.insert(0, "dataset", dataset_name)
        row_changes["left_snapshot_id"] = left_snapshot_id
        row_changes["right_snapshot_id"] = right_snapshot_id
    if not field_changes.empty:
        for column in ("primary_source", "candidate_source"):
            if column in field_changes.columns:
                field_changes = field_changes.drop(columns=column)
        field_changes.insert(0, "dataset", dataset_name)
        field_changes["left_snapshot_id"] = left_snapshot_id
        field_changes["right_snapshot_id"] = right_snapshot_id

    summary = pd.DataFrame([
        {
            "dataset": dataset_name,
            "left_rows": int(len(left)),
            "right_rows": int(len(right)),
            "shared_keys": presence_summary["shared_keys"],
            "left_only_keys": presence_summary["left_only_keys"],
            "right_only_keys": presence_summary["right_only_keys"],
            "field_mismatch_cells": int(len(field_changes)),
            "changed_keys": _changed_key_count(join_keys, row_changes, field_changes),
            "compare_fields": ",".join(compare_fields),
            "has_changes": bool(
                presence_summary["left_only_keys"]
                or presence_summary["right_only_keys"]
                or len(field_changes)
            ),
        }
    ])
    return summary, row_changes, field_changes


def diff_snapshots(
    left_snapshot: str | Path | SnapshotBundle,
    right_snapshot: str | Path | SnapshotBundle,
    *,
    daily_fields: Sequence[str] | None = None,
    instrument_fields: Sequence[str] | None = None,
    financial_fields: dict[str, Sequence[str]] | Sequence[str] | None = None,
    abs_tolerance: float = 1e-8,
) -> SnapshotDiffReport:
    left_bundle = _coerce_snapshot_bundle(left_snapshot)
    right_bundle = _coerce_snapshot_bundle(right_snapshot)

    dataset_summaries: list[pd.DataFrame] = []
    row_changes: list[pd.DataFrame] = []
    field_changes: list[pd.DataFrame] = []

    left_daily = left_bundle.daily_bars.data if left_bundle.daily_bars is not None else pd.DataFrame(columns=["symbol", "trade_date"])
    right_daily = right_bundle.daily_bars.data if right_bundle.daily_bars is not None else pd.DataFrame(columns=["symbol", "trade_date"])
    summary, rows, fields = _diff_dataset_frames(
        "daily_bar",
        left_daily,
        right_daily,
        join_keys=("symbol", "trade_date"),
        left_snapshot_id=left_bundle.snapshot_id,
        right_snapshot_id=right_bundle.snapshot_id,
        requested_fields=daily_fields,
        abs_tolerance=abs_tolerance,
    )
    dataset_summaries.append(summary)
    if not rows.empty:
        row_changes.append(rows)
    if not fields.empty:
        field_changes.append(fields)

    left_instrument = left_bundle.instrument.data if left_bundle.instrument is not None else pd.DataFrame(columns=["symbol"])
    right_instrument = right_bundle.instrument.data if right_bundle.instrument is not None else pd.DataFrame(columns=["symbol"])
    summary, rows, fields = _diff_dataset_frames(
        "instrument",
        left_instrument,
        right_instrument,
        join_keys=("symbol",),
        left_snapshot_id=left_bundle.snapshot_id,
        right_snapshot_id=right_bundle.snapshot_id,
        requested_fields=instrument_fields,
        abs_tolerance=abs_tolerance,
    )
    dataset_summaries.append(summary)
    if not rows.empty:
        row_changes.append(rows)
    if not fields.empty:
        field_changes.append(fields)

    left_tables = left_bundle.financials.tables if left_bundle.financials is not None else {}
    right_tables = right_bundle.financials.tables if right_bundle.financials is not None else {}
    table_names = sorted(set(left_tables) | set(right_tables))
    for table_name in table_names:
        requested = (
            list(financial_fields.get(table_name, ()))
            if isinstance(financial_fields, dict)
            else financial_fields
        )
        left_frame = left_tables.get(table_name, pd.DataFrame(columns=["symbol", "report_date"]))
        right_frame = right_tables.get(table_name, pd.DataFrame(columns=["symbol", "report_date"]))
        summary, rows, fields = _diff_dataset_frames(
            f"financial:{table_name}",
            left_frame,
            right_frame,
            join_keys=_resolve_financial_join_keys(left_frame, right_frame),
            left_snapshot_id=left_bundle.snapshot_id,
            right_snapshot_id=right_bundle.snapshot_id,
            requested_fields=requested,
            abs_tolerance=abs_tolerance,
        )
        dataset_summaries.append(summary)
        if not rows.empty:
            row_changes.append(rows)
        if not fields.empty:
            field_changes.append(fields)

    return SnapshotDiffReport(
        left_snapshot_id=left_bundle.snapshot_id,
        right_snapshot_id=right_bundle.snapshot_id,
        dataset_summary=pd.concat(dataset_summaries, ignore_index=True) if dataset_summaries else pd.DataFrame(),
        row_changes=pd.concat(row_changes, ignore_index=True) if row_changes else pd.DataFrame(),
        field_changes=pd.concat(field_changes, ignore_index=True) if field_changes else pd.DataFrame(),
        metadata={
            "left_snapshot_dir": str(left_bundle.snapshot_dir),
            "right_snapshot_dir": str(right_bundle.snapshot_dir),
            "generated_at": _utc_now_iso(),
            "abs_tolerance": abs_tolerance,
        },
    )


def write_diff_report(
    left_snapshot: str | Path | SnapshotBundle,
    right_snapshot: str | Path | SnapshotBundle,
    *,
    diff_name: str = "",
    diff_root: str | Path | None = None,
    storage_format: str = "parquet",
    daily_fields: Sequence[str] | None = None,
    instrument_fields: Sequence[str] | None = None,
    financial_fields: dict[str, Sequence[str]] | Sequence[str] | None = None,
    abs_tolerance: float = 1e-8,
    extra_metadata: dict[str, Any] | None = None,
) -> SnapshotDiffArtifact:
    left_bundle = _coerce_snapshot_bundle(left_snapshot)
    right_bundle = _coerce_snapshot_bundle(right_snapshot)
    report = diff_snapshots(
        left_bundle,
        right_bundle,
        daily_fields=daily_fields,
        instrument_fields=instrument_fields,
        financial_fields=financial_fields,
        abs_tolerance=abs_tolerance,
    )

    suffix = _storage_suffix(storage_format)
    default_name = f"{left_bundle.snapshot_id}_vs_{right_bundle.snapshot_id}"
    diff_id = f"{_local_snapshot_stamp()}_{_slugify_snapshot_name(diff_name or default_name)}"
    root = Path(diff_root).expanduser() if diff_root else Path("research/output/snapshot_diffs")
    diff_dir = root / diff_id
    diff_dir.mkdir(parents=True, exist_ok=False)

    relative_paths = {
        "dataset_summary": f"dataset_summary.{suffix}",
        "row_changes": f"row_changes.{suffix}",
        "field_changes": f"field_changes.{suffix}",
        "top_changes": "top_changes.md",
    }
    _write_frame(report.dataset_summary, diff_dir / relative_paths["dataset_summary"], storage_format)
    _write_frame(report.row_changes, diff_dir / relative_paths["row_changes"], storage_format)
    _write_frame(report.field_changes, diff_dir / relative_paths["field_changes"], storage_format)

    manifest = {
        "diff_id": diff_id,
        "diff_name": diff_name or default_name,
        "diff_version": "research-diff-v1",
        "created_at": _utc_now_iso(),
        "storage_format": storage_format,
        "diff_dir": str(diff_dir),
        "query": {
            "left_snapshot_id": left_bundle.snapshot_id,
            "right_snapshot_id": right_bundle.snapshot_id,
            "left_snapshot_dir": str(left_bundle.snapshot_dir),
            "right_snapshot_dir": str(right_bundle.snapshot_dir),
            "daily_fields": list(daily_fields) if daily_fields is not None else None,
            "instrument_fields": list(instrument_fields) if instrument_fields is not None else None,
            "financial_fields": _json_safe(financial_fields),
            "abs_tolerance": abs_tolerance,
        },
        "report": _diff_report_manifest(report, relative_paths),
        "metadata": _json_safe(extra_metadata or {}),
    }

    manifest_path = diff_dir / "manifest.json"
    top_changes_path = diff_dir / relative_paths["top_changes"]
    top_changes_path.write_text(
        _render_top_changes_md(report, manifest),
        encoding="utf-8",
    )
    manifest_path.write_text(
        json.dumps(_json_safe(manifest), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return SnapshotDiffArtifact(
        diff_id=diff_id,
        diff_dir=diff_dir,
        manifest_path=manifest_path,
        top_changes_path=top_changes_path,
        manifest=manifest,
        report=report,
    )


def _compare_frames(
    left: pd.DataFrame,
    right: pd.DataFrame,
    *,
    join_keys: Sequence[str],
    fields: Sequence[str],
    primary_source: SourceName,
    candidate_source: SourceName,
    abs_tolerance: float = 1e-8,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    usable_fields = [field for field in fields if field in left.columns and field in right.columns]
    if left.empty or right.empty or not usable_fields:
        return pd.DataFrame(), pd.DataFrame()

    merged = left[list(join_keys) + usable_fields].merge(
        right[list(join_keys) + usable_fields],
        on=list(join_keys),
        how="inner",
        suffixes=("_primary", "_candidate"),
    )
    if merged.empty:
        return pd.DataFrame(), pd.DataFrame()

    mismatch_frames: list[pd.DataFrame] = []
    summary_rows: list[dict[str, Any]] = []

    for field in usable_fields:
        left_col = f"{field}_primary"
        right_col = f"{field}_candidate"
        left_is_bool = pd.api.types.is_bool_dtype(merged[left_col])
        right_is_bool = pd.api.types.is_bool_dtype(merged[right_col])
        if left_is_bool or right_is_bool:
            left_num = pd.Series(index=merged.index, dtype="float64")
            right_num = pd.Series(index=merged.index, dtype="float64")
            numeric_mask = pd.Series(False, index=merged.index)
        else:
            left_num = pd.to_numeric(merged[left_col], errors="coerce")
            right_num = pd.to_numeric(merged[right_col], errors="coerce")
            numeric_mask = left_num.notna() & right_num.notna()
        mismatch_mask = pd.Series(False, index=merged.index)
        mismatch_mask.loc[numeric_mask] = (left_num[numeric_mask] - right_num[numeric_mask]).abs() > abs_tolerance
        mismatch_mask.loc[~numeric_mask] = (
            merged.loc[~numeric_mask, left_col].fillna("").astype(str)
            != merged.loc[~numeric_mask, right_col].fillna("").astype(str)
        )

        field_mismatches = merged.loc[mismatch_mask, list(join_keys) + [left_col, right_col]].copy()
        if not field_mismatches.empty:
            field_mismatches = field_mismatches.rename(
                columns={
                    left_col: "primary_value",
                    right_col: "candidate_value",
                }
            )
            field_mismatches.insert(len(join_keys), "field", field)
            field_mismatches["primary_source"] = primary_source.value
            field_mismatches["candidate_source"] = candidate_source.value
            mismatch_frames.append(field_mismatches)

        summary_rows.append(
            {
                "field": field,
                "compared_rows": len(merged),
                "mismatch_rows": int(mismatch_mask.sum()),
                "primary_source": primary_source.value,
                "candidate_source": candidate_source.value,
            }
        )

    mismatches = pd.concat(mismatch_frames, ignore_index=True) if mismatch_frames else pd.DataFrame()
    summaries = pd.DataFrame(summary_rows)
    return summaries, mismatches


class ResearchSourceAdapter:
    name: SourceName
    supports_daily_bars = False
    supports_financials = False
    supports_instrument = False


class QMTResearchSource(ResearchSourceAdapter):
    name = SourceName.QMT
    supports_daily_bars = True
    supports_financials = True
    supports_instrument = True

    def __init__(self, *, host: str | None = None, port: int | None = None, api_key: str | None = None):
        self.client = make_starbridge_client(host=host, port=port, api_key=api_key)

    def fetch_daily_bars(
        self,
        symbols: Sequence[str],
        *,
        period: str = "1d",
        start_date: str = "",
        end_date: str = "",
        local_only: bool = True,
        fill_data: bool = False,
        dividend_type: str = "none",
    ) -> TabularDataset:
        symbol_list = _ensure_symbol_list(symbols)
        raw = (
            self.client.get_local_data(
                symbol_list,
                period=period,
                start_time=start_date,
                end_time=end_date,
                fill_data=fill_data,
                dividend_type=dividend_type,
            )
            if local_only
            else self.client.get_history_ex(
                symbol_list,
                period=period,
                start_time=start_date,
                end_time=end_date,
                fill_data=fill_data,
                dividend_type=dividend_type,
            )
        )

        frames = []
        for symbol in symbol_list:
            frame = raw.get(symbol)
            if frame is None:
                continue
            normalized = _normalize_qmt_bar_frame(pd.DataFrame(frame), symbol)
            if not normalized.empty:
                frames.append(normalized)

        data = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        return TabularDataset(
            domain=DataDomain.DAILY_BAR,
            source=self.name,
            data=data,
            requested_symbols=tuple(symbol_list),
            coverage_symbols=_coverage_symbols(data),
            asof_date=_max_asof_date(data, ["trade_date"]),
            fetch_time=_utc_now_iso(),
            quality_flags=("primary", "local_cache" if local_only else "qmt_remote"),
            metadata={"period": period, "dividend_type": dividend_type, "fill_data": fill_data},
        )

    def fetch_instrument_basics(self, symbols: Sequence[str], *, iscomplete: bool = True) -> TabularDataset:
        symbol_list = _ensure_symbol_list(symbols)
        raw = self.client.get_batch_instrument_detail(symbol_list, iscomplete=iscomplete)
        data = pd.DataFrame.from_dict(raw, orient="index").reset_index().rename(columns={"index": "symbol"})
        data = _normalize_instrument_frame(data) if not data.empty else data
        return TabularDataset(
            domain=DataDomain.INSTRUMENT,
            source=self.name,
            data=data,
            requested_symbols=tuple(symbol_list),
            coverage_symbols=_coverage_symbols(data),
            asof_date=_max_asof_date(data, ["list_date", "delist_date"]),
            fetch_time=_utc_now_iso(),
            quality_flags=("primary", "qmt_detail"),
            metadata={"iscomplete": iscomplete},
        )

    def fetch_financials(
        self,
        symbols: Sequence[str],
        *,
        tables: Sequence[str] = ("Balance", "Income", "CashFlow"),
        start_date: str = "",
        end_date: str = "",
        report_type: str = "report_time",
    ) -> FinancialDataset:
        symbol_list = _ensure_symbol_list(symbols)
        raw = self.client.get_financial_data(
            symbol_list,
            tables=list(tables),
            start_time=start_date,
            end_time=end_date,
            report_type=report_type,
        )
        normalized_tables: dict[str, pd.DataFrame] = {}
        for table in tables:
            parts = []
            for symbol, table_map in raw.items():
                records = (table_map or {}).get(table, [])
                if not records:
                    continue
                frame = _normalize_financial_frame(pd.DataFrame(records), symbol)
                parts.append(frame)
            normalized_tables[table] = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()

        coverage: set[str] = set()
        asof_candidates = []
        for frame in normalized_tables.values():
            coverage.update(_coverage_symbols(frame))
            asof_candidates.append(_max_asof_date(frame, ["report_date", "announce_date", "announce_time", "endDate"]))

        return FinancialDataset(
            source=self.name,
            tables=normalized_tables,
            requested_symbols=tuple(symbol_list),
            coverage_symbols=tuple(sorted(coverage)),
            asof_date=max([value for value in asof_candidates if value], default=""),
            fetch_time=_utc_now_iso(),
            quality_flags=("primary", "qmt_financial_cache"),
            metadata={"tables": list(tables), "report_type": report_type},
        )


class TushareResearchSource(ResearchSourceAdapter):
    name = SourceName.TUSHARE
    supports_daily_bars = True
    supports_instrument = True

    def __init__(
        self,
        *,
        token: str | None = None,
        list_status: str = "L",
        cache_path: str | None = None,
    ):
        self.token = token
        self.list_status = list_status
        self._instrument_cache: pd.DataFrame | None = None
        default_cache = Path.home() / ".cache" / "starbridge-quant" / f"tushare_stock_basic_{list_status}.csv"
        self.cache_path = Path(cache_path).expanduser() if cache_path else default_cache

    def _pro(self):
        import tushare as ts

        return ts.pro_api(self.token) if self.token else ts.pro_api()

    def _load_instrument_cache(self) -> tuple[pd.DataFrame, str]:
        if self._instrument_cache is not None:
            return self._instrument_cache.copy(), "memory"
        if self.cache_path.exists():
            self._instrument_cache = pd.read_csv(self.cache_path, dtype=str).fillna("")
            return self._instrument_cache.copy(), "disk"

        fields = "ts_code,symbol,name,area,industry,market,list_date,delist_date"
        frame = self._pro().stock_basic(
            exchange="",
            list_status=self.list_status,
            fields=fields,
        )
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(self.cache_path, index=False)
        self._instrument_cache = frame
        return frame.copy(), "remote"

    def fetch_instrument_basics(self, symbols: Sequence[str]) -> TabularDataset:
        symbol_list = _ensure_symbol_list(symbols)
        frame, cache_source = self._load_instrument_cache()
        if symbol_list:
            frame = frame[frame["ts_code"].isin(symbol_list)].copy()
        data = _normalize_instrument_frame(frame) if not frame.empty else frame
        return TabularDataset(
            domain=DataDomain.INSTRUMENT,
            source=self.name,
            data=data,
            requested_symbols=tuple(symbol_list),
            coverage_symbols=_coverage_symbols(data),
            asof_date=_max_asof_date(data, ["list_date", "delist_date"]),
            fetch_time=_utc_now_iso(),
            quality_flags=("public_reference",),
            metadata={"list_status": self.list_status, "cache_source": cache_source, "cache_path": str(self.cache_path)},
        )

    def fetch_daily_bars(
        self,
        symbols: Sequence[str],
        *,
        start_date: str = "",
        end_date: str = "",
    ) -> TabularDataset:
        symbol_list = _ensure_symbol_list(symbols)
        pro = self._pro()
        frames = []
        for symbol in symbol_list:
            frame = pro.daily(ts_code=symbol, start_date=start_date, end_date=end_date)
            if frame is None or frame.empty:
                continue
            normalized = _normalize_tushare_bar_frame(frame)
            frames.append(normalized)
        data = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        return TabularDataset(
            domain=DataDomain.DAILY_BAR,
            source=self.name,
            data=data,
            requested_symbols=tuple(symbol_list),
            coverage_symbols=_coverage_symbols(data),
            asof_date=_max_asof_date(data, ["trade_date"]),
            fetch_time=_utc_now_iso(),
            quality_flags=("public_reference", "tushare_pro"),
        )


class AkshareResearchSource(ResearchSourceAdapter):
    name = SourceName.AKSHARE
    supports_daily_bars = True

    def fetch_daily_bars(
        self,
        symbols: Sequence[str],
        *,
        start_date: str = "",
        end_date: str = "",
        adjust: str = "",
    ) -> TabularDataset:
        import akshare as ak

        symbol_list = _ensure_symbol_list(symbols)
        frames = []
        for symbol in symbol_list:
            simple_symbol = _ts_to_simple_symbol(symbol)
            frame = ak.stock_zh_a_hist(
                symbol=simple_symbol,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust=adjust,
            )
            if frame is None or frame.empty:
                continue
            frames.append(_normalize_akshare_bar_frame(frame, symbol))
        data = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        return TabularDataset(
            domain=DataDomain.DAILY_BAR,
            source=self.name,
            data=data,
            requested_symbols=tuple(symbol_list),
            coverage_symbols=_coverage_symbols(data),
            asof_date=_max_asof_date(data, ["trade_date"]),
            fetch_time=_utc_now_iso(),
            quality_flags=("public_reference", "akshare_em"),
            metadata={"adjust": adjust},
        )


class ResearchClient:
    """Research-side unified client with explicit primary/fallback/reconcile semantics."""

    def __init__(
        self,
        *,
        qmt: QMTResearchSource | None = None,
        tushare: TushareResearchSource | None = None,
        akshare: AkshareResearchSource | None = None,
        policies: dict[DataDomain, DomainPolicy] | None = None,
    ):
        self.sources: dict[SourceName, ResearchSourceAdapter] = {
            SourceName.QMT: qmt or QMTResearchSource(),
            SourceName.TUSHARE: tushare or TushareResearchSource(),
            SourceName.AKSHARE: akshare or AkshareResearchSource(),
        }
        self.policies = policies or {
            DataDomain.DAILY_BAR: DomainPolicy(
                primary=SourceName.QMT,
                fallbacks=(SourceName.AKSHARE,),
                compare_fields=("close", "volume"),
            ),
            DataDomain.FINANCIAL: DomainPolicy(
                primary=SourceName.QMT,
                fallbacks=(),
                compare_fields=(),
            ),
            DataDomain.INSTRUMENT: DomainPolicy(
                primary=SourceName.QMT,
                fallbacks=(SourceName.TUSHARE,),
                compare_fields=("name", "list_date"),
            ),
        }

    def describe_policy(self) -> pd.DataFrame:
        rows = []
        for domain, policy in self.policies.items():
            rows.append(
                {
                    "domain": domain.value,
                    "primary": policy.primary.value,
                    "fallbacks": ",".join(source.value for source in policy.fallbacks),
                    "compare_fields": ",".join(policy.compare_fields),
                }
            )
        return pd.DataFrame(rows)

    def _resolve_source_order(
        self,
        domain: DataDomain,
        *,
        source: SourceName | None = None,
        allow_fallback: bool = False,
    ) -> list[SourceName]:
        if source is not None:
            return [source]
        policy = self.policies[domain]
        order = [policy.primary]
        if allow_fallback:
            order.extend(policy.fallbacks)
        return order

    def get_daily_bars(
        self,
        symbols: Sequence[str] | str,
        *,
        period: str = "1d",
        start_date: str = "",
        end_date: str = "",
        local_only: bool = True,
        fill_data: bool = False,
        dividend_type: str = "none",
        source: SourceName | None = None,
        allow_fallback: bool = False,
    ) -> TabularDataset:
        symbol_list = _ensure_symbol_list(symbols)
        errors: dict[str, str] = {}
        order = self._resolve_source_order(DataDomain.DAILY_BAR, source=source, allow_fallback=allow_fallback)
        for index, source_name in enumerate(order):
            adapter = self.sources[source_name]
            if not adapter.supports_daily_bars:
                continue
            try:
                if source_name == SourceName.QMT:
                    dataset = adapter.fetch_daily_bars(  # type: ignore[attr-defined]
                        symbol_list,
                        period=period,
                        start_date=start_date,
                        end_date=end_date,
                        local_only=local_only,
                        fill_data=fill_data,
                        dividend_type=dividend_type,
                    )
                elif source_name == SourceName.AKSHARE:
                    adjust = ""
                    if dividend_type in {"front", "front_ratio"}:
                        adjust = "qfq"
                    elif dividend_type in {"back", "back_ratio"}:
                        adjust = "hfq"
                    dataset = adapter.fetch_daily_bars(  # type: ignore[attr-defined]
                        symbol_list,
                        start_date=start_date,
                        end_date=end_date,
                        adjust=adjust,
                    )
                else:
                    dataset = adapter.fetch_daily_bars(  # type: ignore[attr-defined]
                        symbol_list,
                        start_date=start_date,
                        end_date=end_date,
                    )
            except Exception as exc:
                errors[source_name.value] = str(exc)
                continue

            dataset.is_fallback = index > 0
            dataset.metadata.setdefault("errors", {}).update(errors)
            if not dataset.empty or index == len(order) - 1:
                return dataset

        return _empty_tabular_dataset(
            DataDomain.DAILY_BAR,
            order[0],
            symbol_list,
            quality_flags=("unavailable",),
            metadata={"errors": errors},
        )

    def get_instrument_basics(
        self,
        symbols: Sequence[str] | str,
        *,
        source: SourceName | None = None,
        allow_fallback: bool = False,
    ) -> TabularDataset:
        symbol_list = _ensure_symbol_list(symbols)
        errors: dict[str, str] = {}
        order = self._resolve_source_order(DataDomain.INSTRUMENT, source=source, allow_fallback=allow_fallback)
        for index, source_name in enumerate(order):
            adapter = self.sources[source_name]
            if not adapter.supports_instrument:
                continue
            try:
                dataset = adapter.fetch_instrument_basics(symbol_list)  # type: ignore[attr-defined]
            except Exception as exc:
                errors[source_name.value] = str(exc)
                continue

            dataset.is_fallback = index > 0
            dataset.metadata.setdefault("errors", {}).update(errors)
            if not dataset.empty or index == len(order) - 1:
                return dataset

        return _empty_tabular_dataset(
            DataDomain.INSTRUMENT,
            order[0],
            symbol_list,
            quality_flags=("unavailable",),
            metadata={"errors": errors},
        )

    def get_financials(
        self,
        symbols: Sequence[str] | str,
        *,
        tables: Sequence[str] = ("Balance", "Income", "CashFlow"),
        start_date: str = "",
        end_date: str = "",
        report_type: str = "report_time",
    ) -> FinancialDataset:
        symbol_list = _ensure_symbol_list(symbols)
        adapter = self.sources[self.policies[DataDomain.FINANCIAL].primary]
        if not adapter.supports_financials:
            return FinancialDataset(source=self.policies[DataDomain.FINANCIAL].primary, tables={})
        return adapter.fetch_financials(  # type: ignore[attr-defined]
            symbol_list,
            tables=tables,
            start_date=start_date,
            end_date=end_date,
            report_type=report_type,
        )

    def reconcile_daily_bars(
        self,
        symbols: Sequence[str] | str,
        *,
        start_date: str = "",
        end_date: str = "",
        candidate_sources: Sequence[SourceName] | None = None,
        fields: Sequence[str] | None = None,
        local_only: bool = True,
        fill_data: bool = False,
        dividend_type: str = "none",
        abs_tolerance: float = 1e-8,
    ) -> ComparisonReport:
        symbol_list = _ensure_symbol_list(symbols)
        policy = self.policies[DataDomain.DAILY_BAR]
        field_list = tuple(fields or policy.compare_fields)
        primary = self.get_daily_bars(
            symbol_list,
            start_date=start_date,
            end_date=end_date,
            local_only=local_only,
            fill_data=fill_data,
            dividend_type=dividend_type,
            source=policy.primary,
        )

        summaries = []
        mismatches = []
        compare_sources = list(candidate_sources or policy.fallbacks)
        for candidate_source in compare_sources:
            candidate = self.get_daily_bars(
                symbol_list,
                start_date=start_date,
                end_date=end_date,
                dividend_type=dividend_type,
                source=candidate_source,
            )
            summary, mismatch = _compare_frames(
                primary.data,
                candidate.data,
                join_keys=("symbol", "trade_date"),
                fields=field_list,
                primary_source=policy.primary,
                candidate_source=candidate_source,
                abs_tolerance=abs_tolerance,
            )
            if not summary.empty:
                summary.insert(0, "domain", DataDomain.DAILY_BAR.value)
                summaries.append(summary)
            if not mismatch.empty:
                mismatch.insert(0, "domain", DataDomain.DAILY_BAR.value)
                mismatches.append(mismatch)

        return ComparisonReport(
            domain=DataDomain.DAILY_BAR,
            primary_source=policy.primary,
            summaries=pd.concat(summaries, ignore_index=True) if summaries else pd.DataFrame(),
            mismatches=pd.concat(mismatches, ignore_index=True) if mismatches else pd.DataFrame(),
            metadata={"requested_symbols": symbol_list},
        )

    def reconcile_instrument_basics(
        self,
        symbols: Sequence[str] | str,
        *,
        candidate_sources: Sequence[SourceName] | None = None,
        fields: Sequence[str] | None = None,
    ) -> ComparisonReport:
        symbol_list = _ensure_symbol_list(symbols)
        policy = self.policies[DataDomain.INSTRUMENT]
        field_list = tuple(fields or policy.compare_fields)
        primary = self.get_instrument_basics(symbol_list, source=policy.primary)

        summaries = []
        mismatches = []
        compare_sources = list(candidate_sources or policy.fallbacks)
        for candidate_source in compare_sources:
            candidate = self.get_instrument_basics(symbol_list, source=candidate_source)
            summary, mismatch = _compare_frames(
                primary.data,
                candidate.data,
                join_keys=("symbol",),
                fields=field_list,
                primary_source=policy.primary,
                candidate_source=candidate_source,
            )
            if not summary.empty:
                summary.insert(0, "domain", DataDomain.INSTRUMENT.value)
                summaries.append(summary)
            if not mismatch.empty:
                mismatch.insert(0, "domain", DataDomain.INSTRUMENT.value)
                mismatches.append(mismatch)

        return ComparisonReport(
            domain=DataDomain.INSTRUMENT,
            primary_source=policy.primary,
            summaries=pd.concat(summaries, ignore_index=True) if summaries else pd.DataFrame(),
            mismatches=pd.concat(mismatches, ignore_index=True) if mismatches else pd.DataFrame(),
            metadata={"requested_symbols": symbol_list},
        )

    def write_snapshot(
        self,
        symbols: Sequence[str] | str,
        *,
        snapshot_name: str = "",
        snapshot_root: str | Path | None = None,
        storage_format: str = "parquet",
        period: str = "1d",
        start_date: str = "",
        end_date: str = "",
        local_only: bool = True,
        fill_data: bool = False,
        dividend_type: str = "none",
        allow_fallback: bool = False,
        include_instrument: bool = True,
        include_financial: bool = True,
        financial_tables: Sequence[str] = ("Balance", "Income", "CashFlow"),
        financial_report_type: str = "report_time",
        extra_metadata: dict[str, Any] | None = None,
    ) -> SnapshotBundle:
        symbol_list = _ensure_symbol_list(symbols)
        suffix = _storage_suffix(storage_format)
        snapshot_id = f"{_local_snapshot_stamp()}_{_slugify_snapshot_name(snapshot_name or 'research_snapshot')}"
        root = Path(snapshot_root).expanduser() if snapshot_root else Path("research/output/snapshots")
        snapshot_dir = root / snapshot_id
        snapshot_dir.mkdir(parents=True, exist_ok=False)

        daily_bars = self.get_daily_bars(
            symbol_list,
            period=period,
            start_date=start_date,
            end_date=end_date,
            local_only=local_only,
            fill_data=fill_data,
            dividend_type=dividend_type,
            allow_fallback=allow_fallback,
        )
        daily_rel = f"daily_bar.{suffix}"
        _write_frame(daily_bars.data, snapshot_dir / daily_rel, storage_format)

        instrument = None
        instrument_rel = None
        if include_instrument:
            instrument = self.get_instrument_basics(symbol_list, allow_fallback=allow_fallback)
            instrument_rel = f"instrument.{suffix}"
            _write_frame(instrument.data, snapshot_dir / instrument_rel, storage_format)

        financials = None
        financial_paths: dict[str, str] = {}
        if include_financial:
            financials = self.get_financials(
                symbol_list,
                tables=financial_tables,
                start_date=start_date,
                end_date=end_date,
                report_type=financial_report_type,
            )
            financial_dir = snapshot_dir / "financial"
            financial_dir.mkdir(exist_ok=True)
            for table, frame in financials.tables.items():
                rel_path = f"financial/{table}.{suffix}"
                financial_paths[table] = rel_path
                _write_frame(frame, snapshot_dir / rel_path, storage_format)

        manifest: dict[str, Any] = {
            "snapshot_id": snapshot_id,
            "snapshot_name": snapshot_name or "research_snapshot",
            "snapshot_version": "research-snapshot-v1",
            "created_at": _utc_now_iso(),
            "storage_format": storage_format,
            "snapshot_dir": str(snapshot_dir),
            "query": {
                "requested_symbols": symbol_list,
                "period": period,
                "start_date": start_date,
                "end_date": end_date,
                "local_only": local_only,
                "fill_data": fill_data,
                "dividend_type": dividend_type,
                "allow_fallback": allow_fallback,
                "include_instrument": include_instrument,
                "include_financial": include_financial,
                "financial_tables": list(financial_tables),
                "financial_report_type": financial_report_type,
            },
            "policies": self.describe_policy().to_dict(orient="records"),
            "datasets": {
                "daily_bar": _dataset_manifest(daily_bars, daily_rel),
            },
            "metadata": _json_safe(extra_metadata or {}),
        }
        if instrument is not None and instrument_rel is not None:
            manifest["datasets"]["instrument"] = _dataset_manifest(instrument, instrument_rel)
        if financials is not None:
            manifest["datasets"]["financial"] = _financial_manifest(financials, financial_paths)

        manifest_path = snapshot_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(_json_safe(manifest), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return SnapshotBundle(
            snapshot_id=snapshot_id,
            snapshot_dir=snapshot_dir,
            manifest_path=manifest_path,
            manifest=manifest,
            daily_bars=daily_bars,
            instrument=instrument,
            financials=financials,
        )

    def load_snapshot(self, snapshot_path: str | Path) -> SnapshotBundle:
        return load_snapshot(snapshot_path)

    def diff_snapshots(
        self,
        left_snapshot: str | Path | SnapshotBundle,
        right_snapshot: str | Path | SnapshotBundle,
        *,
        daily_fields: Sequence[str] | None = None,
        instrument_fields: Sequence[str] | None = None,
        financial_fields: dict[str, Sequence[str]] | Sequence[str] | None = None,
        abs_tolerance: float = 1e-8,
    ) -> SnapshotDiffReport:
        return diff_snapshots(
            left_snapshot,
            right_snapshot,
            daily_fields=daily_fields,
            instrument_fields=instrument_fields,
            financial_fields=financial_fields,
            abs_tolerance=abs_tolerance,
        )

    def write_diff_report(
        self,
        left_snapshot: str | Path | SnapshotBundle,
        right_snapshot: str | Path | SnapshotBundle,
        *,
        diff_name: str = "",
        diff_root: str | Path | None = None,
        storage_format: str = "parquet",
        daily_fields: Sequence[str] | None = None,
        instrument_fields: Sequence[str] | None = None,
        financial_fields: dict[str, Sequence[str]] | Sequence[str] | None = None,
        abs_tolerance: float = 1e-8,
        extra_metadata: dict[str, Any] | None = None,
    ) -> SnapshotDiffArtifact:
        return write_diff_report(
            left_snapshot,
            right_snapshot,
            diff_name=diff_name,
            diff_root=diff_root,
            storage_format=storage_format,
            daily_fields=daily_fields,
            instrument_fields=instrument_fields,
            financial_fields=financial_fields,
            abs_tolerance=abs_tolerance,
            extra_metadata=extra_metadata,
        )
