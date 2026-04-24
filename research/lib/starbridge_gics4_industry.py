"""QMT GICS4 industry mapping helpers for A-share research."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Sequence

import pandas as pd

from starbridge_quant.client_factory import make_starbridge_client


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QMT_GICS4_CACHE = ROOT / "research" / "reference" / "qmt_gics4_industry_map.csv"


def summary_path_for(output_path: str | Path) -> Path:
    path = Path(output_path)
    return path.with_name(f"{path.stem}_summary.json")


DEFAULT_QMT_GICS4_SUMMARY = summary_path_for(DEFAULT_QMT_GICS4_CACHE)


def _read_local_map(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype=str).fillna("")
    symbol_column = "symbol" if "symbol" in frame.columns else "stock_code" if "stock_code" in frame.columns else ""
    industry_column = "industry" if "industry" in frame.columns else ""
    if not symbol_column or not industry_column:
        raise ValueError(f"industry map at {path} must include symbol/stock_code and industry columns")
    result = frame.copy()
    if symbol_column != "symbol":
        result = result.rename(columns={symbol_column: "symbol"})
    if industry_column != "industry":
        result = result.rename(columns={industry_column: "industry"})
    result["symbol"] = result["symbol"].astype(str).str.strip()
    result["industry"] = result["industry"].astype(str).str.strip()
    result = result[(result["symbol"] != "") & (result["industry"] != "")]
    return result


def load_cached_qmt_gics4_industry_map(
    path: str | Path | None = None,
    *,
    symbols: Sequence[str] | None = None,
) -> tuple[pd.DataFrame, dict]:
    resolved = Path(path).expanduser() if path else DEFAULT_QMT_GICS4_CACHE
    if not resolved.exists():
        return pd.DataFrame(columns=["symbol", "industry"]), {
            "enabled": False,
            "reason": "cache_not_found",
            "path": str(resolved),
        }

    frame = _read_local_map(resolved)
    if symbols:
        symbol_set = {str(symbol).strip() for symbol in symbols if str(symbol).strip()}
        frame = frame[frame["symbol"].isin(symbol_set)].copy()

    summary_path = summary_path_for(resolved)
    summary = {}
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            summary = {}

    metadata = {
        "enabled": not frame.empty,
        "source": "local_qmt_gics4_cache",
        "path": str(resolved),
        "coverage_symbols": int(frame["symbol"].nunique()),
        "summary_path": str(summary_path),
    }
    if summary:
        metadata["cache_summary"] = {
            "generated_at": summary.get("generated_at", ""),
            "coverage_symbols": summary.get("coverage_symbols", 0),
            "candidate_sector_count": summary.get("candidate_sector_count", 0),
            "record_count": summary.get("record_count", 0),
            "multi_assigned_count": len(summary.get("multi_assigned_symbols", [])),
            "missing_symbol_count": len(summary.get("missing_symbols", [])),
        }
    return frame.drop_duplicates("symbol"), metadata


def fetch_qmt_gics4_industry_map(
    symbols: Sequence[str] | None = None,
) -> tuple[pd.DataFrame, dict]:
    client = make_starbridge_client()
    sector_list = client.get_sector_list()
    gics4_sectors = [sector for sector in sector_list if str(sector).startswith("GICS4")]
    symbol_set = {str(symbol).strip() for symbol in symbols if str(symbol).strip()} if symbols else None

    records: list[dict[str, str]] = []
    assignment_counts: dict[str, int] = {}
    for sector in gics4_sectors:
        stocks = client.get_sector_stocks_v2(sector)
        if symbol_set is not None:
            stocks = [stock for stock in stocks if stock in symbol_set]
        for stock in stocks:
            assignment_counts[stock] = assignment_counts.get(stock, 0) + 1
            records.append(
                {
                    "symbol": stock,
                    "industry": str(sector).removeprefix("GICS4"),
                    "sector_name": str(sector),
                }
            )

    if not records:
        missing = sorted(symbol_set) if symbol_set is not None else []
        return pd.DataFrame(columns=["symbol", "industry", "sector_name", "assignment_count", "is_unique_assignment"]), {
            "enabled": False,
            "reason": "qmt_gics4_no_matches",
            "candidate_sector_count": len(gics4_sectors),
            "missing_symbols": missing,
        }

    raw = pd.DataFrame(records).drop_duplicates()
    counts = raw.groupby("symbol").size().rename("assignment_count").reset_index()
    preferred = raw.sort_values(["symbol", "sector_name"]).drop_duplicates("symbol", keep="first")
    preferred = preferred.merge(counts, on="symbol", how="left")
    preferred["is_unique_assignment"] = preferred["assignment_count"] == 1

    coverage_symbols = int(preferred["symbol"].nunique())
    multi_assigned_symbols = (
        counts.loc[counts["assignment_count"] > 1, "symbol"].sort_values().tolist()
    )
    missing_symbols = sorted(symbol_set - set(preferred["symbol"])) if symbol_set is not None else []

    metadata = {
        "enabled": True,
        "source": "qmt_gics4",
        "coverage_symbols": coverage_symbols,
        "candidate_sector_count": len(gics4_sectors),
        "raw_match_rows": int(len(raw)),
        "multi_assigned_symbols": multi_assigned_symbols,
        "missing_symbols": missing_symbols,
    }
    return preferred.sort_values("symbol").reset_index(drop=True), metadata


def write_qmt_gics4_industry_cache(
    *,
    output_path: str | Path | None = None,
    summary_path: str | Path | None = None,
    symbols: Sequence[str] | None = None,
) -> dict:
    frame, metadata = fetch_qmt_gics4_industry_map(symbols=symbols)
    resolved_output = Path(output_path).expanduser() if output_path else DEFAULT_QMT_GICS4_CACHE
    resolved_summary = Path(summary_path).expanduser() if summary_path else summary_path_for(resolved_output)
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_summary.parent.mkdir(parents=True, exist_ok=True)

    frame.to_csv(resolved_output, index=False, encoding="utf-8-sig")
    multi_assigned_symbols = metadata.get("multi_assigned_symbols", [])
    missing_symbols = metadata.get("missing_symbols", [])
    summary = {
        **{k: v for k, v in metadata.items() if k not in {"multi_assigned_symbols", "missing_symbols"}},
        "multi_assigned_count": len(multi_assigned_symbols),
        "multi_assigned_examples": multi_assigned_symbols[:20],
        "missing_symbol_count": len(missing_symbols),
        "missing_symbol_examples": missing_symbols[:20],
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "output_path": str(resolved_output),
        "summary_path": str(resolved_summary),
        "record_count": int(len(frame)),
    }
    resolved_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


__all__ = [
    "DEFAULT_QMT_GICS4_CACHE",
    "DEFAULT_QMT_GICS4_SUMMARY",
    "fetch_qmt_gics4_industry_map",
    "load_cached_qmt_gics4_industry_map",
    "summary_path_for",
    "write_qmt_gics4_industry_cache",
]
