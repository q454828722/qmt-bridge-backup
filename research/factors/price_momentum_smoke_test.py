#!/usr/bin/env python3
"""Standardized A-share cross-sectional price factor template."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.lib.qmt_gics4_industry import (
    DEFAULT_QMT_GICS4_CACHE,
    fetch_qmt_gics4_industry_map,
    load_cached_qmt_gics4_industry_map,
)
from research.lib.research_client import TushareResearchSource


DEFAULT_PREFILTER = Path("data/yuanqi_replica/basic/quant_backtest_prefilter.csv")
DEFAULT_FINANCIAL_UNIVERSE = Path("data/yuanqi_replica/basic/quant_financial_universe_fresh_only.csv")
DEFAULT_SNAPSHOT = Path(
    "research/output/snapshots/20260424_105437_post_refresh_audit_20260424_105123"
)
DEFAULT_OUTPUT_ROOT = Path("research/output/factor_tests")
DEFAULT_TUSHARE_CACHE = Path.home() / ".cache" / "qmt-bridge" / "tushare_stock_basic_L.csv"


@dataclass
class SmokeTestConfig:
    factor_name: str
    lookback_days: int
    holding_days: int
    min_listing_days: int
    min_cross_section: int
    liquidity_quantile: float
    winsor_lower_quantile: float
    winsor_upper_quantile: float
    bucket_count: int
    industry_source: str
    industry_neutralize: bool
    industry_cache_path: str
    industry_map_path: str
    prefilter_path: str
    financial_universe_path: str
    snapshot_dir: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefilter-path", type=Path, default=DEFAULT_PREFILTER)
    parser.add_argument("--financial-universe-path", type=Path, default=DEFAULT_FINANCIAL_UNIVERSE)
    parser.add_argument("--snapshot-dir", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--factor-name", default="mom_20d")
    parser.add_argument("--lookback-days", type=int, default=20)
    parser.add_argument("--holding-days", type=int, default=5)
    parser.add_argument("--min-listing-days", type=int, default=120)
    parser.add_argument("--min-cross-section", type=int, default=15)
    parser.add_argument("--liquidity-quantile", type=float, default=0.3)
    parser.add_argument("--winsor-lower-quantile", type=float, default=0.05)
    parser.add_argument("--winsor-upper-quantile", type=float, default=0.95)
    parser.add_argument("--bucket-count", type=int, default=5)
    parser.add_argument(
        "--industry-source",
        choices=["auto", "qmt", "tushare", "none"],
        default="auto",
        help="Industry mapping source. auto prefers QMT GICS4, then Tushare.",
    )
    parser.add_argument(
        "--disable-industry-neutralization",
        action="store_true",
        help="Skip industry de-meaning even if industry mapping is available.",
    )
    parser.add_argument("--industry-cache-path", type=Path, default=DEFAULT_TUSHARE_CACHE)
    parser.add_argument(
        "--industry-map-path",
        type=Path,
        default=None,
        help="Optional local CSV with symbol/stock_code and industry columns.",
    )
    return parser.parse_args()


def ensure_exists(path: Path) -> Path:
    if not path.exists():
        raise FileNotFoundError(path)
    return path


def load_inputs(
    prefilter_path: Path,
    financial_universe_path: Path,
    snapshot_dir: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    prefilter = pd.read_csv(ensure_exists(prefilter_path))
    financial_universe = pd.read_csv(ensure_exists(financial_universe_path))
    snapshot_dir = ensure_exists(snapshot_dir)
    daily_bar = pd.read_parquet(snapshot_dir / "daily_bar.parquet")
    instrument = pd.read_parquet(snapshot_dir / "instrument.parquet")
    manifest = json.loads((snapshot_dir / "manifest.json").read_text(encoding="utf-8"))
    return prefilter, financial_universe, daily_bar, instrument, manifest


def load_industry_reference(
    symbols: list[str],
    *,
    source: str,
    cache_path: Path,
    industry_map_path: Path | None = None,
) -> tuple[pd.DataFrame, dict]:
    resolved_local_path = industry_map_path
    if resolved_local_path is None and source in {"auto", "qmt"} and DEFAULT_QMT_GICS4_CACHE.exists():
        resolved_local_path = DEFAULT_QMT_GICS4_CACHE

    if resolved_local_path:
        cached_frame, cached_meta = load_cached_qmt_gics4_industry_map(resolved_local_path, symbols=symbols)
        if cached_meta.get("enabled"):
            return cached_frame, cached_meta
        if source not in {"auto", "qmt"}:
            return cached_frame, cached_meta

    if source == "none":
        return pd.DataFrame(columns=["symbol", "industry"]), {"enabled": False, "reason": "disabled_by_user"}

    if source in {"auto", "qmt"}:
        try:
            result, metadata = fetch_qmt_gics4_industry_map(symbols=symbols)
            if metadata.get("enabled"):
                return result[["symbol", "industry"]].copy(), metadata
            if source == "qmt":
                return pd.DataFrame(columns=["symbol", "industry"]), metadata
        except Exception as exc:  # pragma: no cover - runtime integration path
            if source == "qmt":
                return pd.DataFrame(columns=["symbol", "industry"]), {
                    "enabled": False,
                    "reason": "qmt_lookup_failed",
                    "error": str(exc),
                }

    try:
        adapter = TushareResearchSource(cache_path=str(cache_path))
        dataset = adapter.fetch_instrument_basics(symbols)
        frame = dataset.data.copy()
        if frame.empty or "industry" not in frame.columns:
            return pd.DataFrame(columns=["symbol", "industry"]), {
                "enabled": False,
                "reason": "industry_column_unavailable",
                "metadata": dataset.metadata,
            }
        result = frame[["symbol", "industry"]].copy()
        result["industry"] = result["industry"].fillna("").astype(str).str.strip()
        result = result[result["industry"] != ""].drop_duplicates("symbol")
        return result, {
            "enabled": not result.empty,
            "coverage_symbols": int(result["symbol"].nunique()),
            "metadata": dataset.metadata,
        }
    except Exception as exc:  # pragma: no cover - network/cache failure path
        return pd.DataFrame(columns=["symbol", "industry"]), {
            "enabled": False,
            "reason": "tushare_lookup_failed",
            "error": str(exc),
            "cache_path": str(cache_path),
        }


def build_base_universe(
    prefilter: pd.DataFrame,
    financial_universe: pd.DataFrame,
    instrument: pd.DataFrame,
    industry_map: pd.DataFrame,
) -> pd.DataFrame:
    pool = prefilter.loc[
        (prefilter["include_price_factors"] == 1)
        & (prefilter["exclude_from_price_backtest"] == 0),
        ["stock_code", "name"],
    ].drop_duplicates()
    fresh = financial_universe.loc[
        (financial_universe["include_price_factors"] == 1)
        & (financial_universe["has_1d_cache"] == 1)
        & (financial_universe["financial_status"] == "ok"),
        ["stock_code", "latest_1d_date", "factor_policy"],
    ].drop_duplicates("stock_code")
    instruments = instrument[["symbol", "name", "list_date", "exchange"]].copy()
    instruments["list_date"] = pd.to_numeric(instruments["list_date"], errors="coerce")
    base = instruments.merge(pool, left_on="symbol", right_on="stock_code", how="inner", suffixes=("", "_prefilter"))
    base = base.merge(fresh, left_on="symbol", right_on="stock_code", how="inner", suffixes=("", "_fresh"))
    base = base.drop(columns=["stock_code", "stock_code_fresh"])
    if "name_prefilter" in base.columns:
        base["name"] = base["name"].fillna(base["name_prefilter"])
        base = base.drop(columns=["name_prefilter"])
    if not industry_map.empty:
        base = base.merge(industry_map, on="symbol", how="left")
    else:
        base["industry"] = ""
    base["industry"] = base["industry"].fillna("").replace("", "UNKNOWN")
    return base.drop_duplicates("symbol")


def build_universe_diagnostics(
    prefilter: pd.DataFrame,
    financial_universe: pd.DataFrame,
    instrument: pd.DataFrame,
    manifest: dict,
    base_universe: pd.DataFrame,
    industry_meta: dict,
) -> dict:
    requested = set(manifest.get("query", {}).get("requested_symbols", []))
    prefilter_ok = set(
        prefilter.loc[
            (prefilter["include_price_factors"] == 1)
            & (prefilter["exclude_from_price_backtest"] == 0),
            "stock_code",
        ]
    )
    financial_ok = set(
        financial_universe.loc[
            (financial_universe["include_price_factors"] == 1)
            & (financial_universe["has_1d_cache"] == 1)
            & (financial_universe["financial_status"] == "ok"),
            "stock_code",
        ]
    )
    instrument_symbols = set(instrument["symbol"])
    base_symbols = set(base_universe["symbol"])
    with_industry = int((base_universe["industry"] != "UNKNOWN").sum()) if "industry" in base_universe.columns else 0
    return {
        "requested_symbols": len(requested),
        "snapshot_instrument_symbols": len(requested & instrument_symbols),
        "prefilter_price_ok_symbols": len(requested & prefilter_ok),
        "financial_fresh_ok_symbols": len(requested & financial_ok),
        "intersection_after_static_filters": len(base_symbols),
        "industry_covered_symbols": with_industry,
        "industry_lookup": industry_meta,
    }


def winsorize_series(series: pd.Series, lower_quantile: float, upper_quantile: float) -> pd.Series:
    valid = series.dropna()
    if valid.empty:
        return series
    lower = valid.quantile(lower_quantile)
    upper = valid.quantile(upper_quantile)
    return series.clip(lower=lower, upper=upper)


def zscore_series(series: pd.Series) -> pd.Series:
    valid = series.dropna()
    if valid.empty:
        return series
    std = valid.std(ddof=0)
    if pd.isna(std) or std == 0:
        return pd.Series(np.nan, index=series.index, dtype="float64")
    mean = valid.mean()
    return (series - mean) / std


def prepare_panel(
    daily_bar: pd.DataFrame,
    base_universe: pd.DataFrame,
    *,
    lookback_days: int,
    holding_days: int,
    min_listing_days: int,
    liquidity_quantile: float,
    winsor_lower_quantile: float,
    winsor_upper_quantile: float,
    industry_neutralize: bool,
) -> tuple[pd.DataFrame, dict]:
    df = daily_bar.merge(
        base_universe[["symbol", "list_date", "industry"]],
        on="symbol",
        how="inner",
    ).copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    df["list_date"] = pd.to_datetime(df["list_date"].astype("Int64").astype(str), format="%Y%m%d", errors="coerce")
    df = df.sort_values(["symbol", "trade_date"]).reset_index(drop=True)

    grouped = df.groupby("symbol", group_keys=False)
    df["days_since_list"] = (df["trade_date"] - df["list_date"]).dt.days
    df["factor_raw"] = grouped["close"].pct_change(lookback_days)
    df["amt_ma_20"] = grouped["amount"].transform(lambda s: s.rolling(lookback_days, min_periods=lookback_days).mean())
    df["next_open"] = grouped["open"].shift(-1)
    df["exit_close"] = grouped["close"].shift(-holding_days)
    df["exit_trade_date"] = grouped["trade_date"].shift(-holding_days)
    df["next_suspend"] = grouped["suspendFlag"].shift(-1)
    df["fwd_ret_5d_open_to_close"] = df["exit_close"] / df["next_open"] - 1.0
    df["liquidity_cut"] = df.groupby("trade_date")["amt_ma_20"].transform(
        lambda s: s.quantile(liquidity_quantile) if s.notna().sum() else np.nan
    )
    df["is_tradeable_base"] = (
        (df["days_since_list"] >= min_listing_days)
        & (df["suspendFlag"] == 0)
        & (df["next_suspend"] == 0)
        & df["factor_raw"].notna()
        & df["amt_ma_20"].notna()
        & df["next_open"].gt(0)
        & df["exit_close"].gt(0)
        & df["liquidity_cut"].notna()
        & df["amt_ma_20"].ge(df["liquidity_cut"])
    )

    df["factor_winsorized"] = np.nan
    df["factor_neutralized"] = np.nan
    df["factor_zscore"] = np.nan
    neutralization_days = 0
    neutralization_rows = 0

    for trade_date, day in df.loc[df["is_tradeable_base"]].groupby("trade_date"):
        raw = day["factor_raw"].astype(float)
        winsorized = winsorize_series(raw, winsor_lower_quantile, winsor_upper_quantile)
        neutralized = winsorized.copy()
        day_has_industry = day["industry"].fillna("").replace("", "UNKNOWN").nunique() >= 2
        day_can_neutralize = industry_neutralize and day_has_industry and (day["industry"] != "UNKNOWN").any()
        if day_can_neutralize:
            neutralized = winsorized - day.groupby("industry")["factor_raw"].transform(
                lambda s: winsorize_series(s.astype(float), winsor_lower_quantile, winsor_upper_quantile).mean()
            )
            neutralization_days += 1
            neutralization_rows += int(len(day))
        zscore = zscore_series(neutralized)
        idx = day.index
        df.loc[idx, "factor_winsorized"] = winsorized
        df.loc[idx, "factor_neutralized"] = neutralized
        df.loc[idx, "factor_zscore"] = zscore

    df["factor_value"] = df["factor_zscore"]
    df["is_tradeable"] = df["is_tradeable_base"] & df["factor_value"].notna()
    preprocess_summary = {
        "winsorization": {
            "lower_quantile": winsor_lower_quantile,
            "upper_quantile": winsor_upper_quantile,
        },
        "standardization": "cross_section_zscore",
        "industry_neutralization_requested": industry_neutralize,
        "industry_neutralization_applied_days": neutralization_days,
        "industry_neutralization_applied_rows": neutralization_rows,
        "eligible_rows_before_factor_processing": int(df["is_tradeable_base"].sum()),
        "eligible_rows_after_factor_processing": int(df["is_tradeable"].sum()),
    }
    return df, preprocess_summary


def safe_qcut(series: pd.Series, buckets: int = 5) -> pd.Series:
    valid = series.dropna()
    if valid.nunique() < buckets:
        return pd.Series(index=series.index, dtype="float64")
    ranks = valid.rank(method="first")
    cuts = pd.qcut(ranks, buckets, labels=False) + 1
    return cuts.reindex(series.index)


def compute_bucket_daily_returns(
    panel: pd.DataFrame,
    *,
    factor_column: str,
    bucket_count: int,
    min_cross_section: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    eligible = panel.loc[panel["is_tradeable"]].copy()
    eligible["bucket"] = eligible.groupby("trade_date")[factor_column].transform(
        lambda s: safe_qcut(s, buckets=bucket_count)
    )
    eligible = eligible.dropna(subset=["bucket"]).copy()
    eligible["bucket"] = eligible["bucket"].astype(int)

    cross_section = eligible.groupby("trade_date")["symbol"].size().rename("cross_section_size")
    valid_dates = cross_section[cross_section >= min_cross_section].index
    eligible = eligible[eligible["trade_date"].isin(valid_dates)].copy()

    bucket_daily = (
        eligible.groupby(["trade_date", "bucket"], as_index=False)
        .agg(
            symbol_count=("symbol", "size"),
            mean_factor_value=(factor_column, "mean"),
            mean_forward_return=("fwd_ret_5d_open_to_close", "mean"),
        )
        .sort_values(["trade_date", "bucket"])
    )
    return eligible, bucket_daily


def compute_daily_metrics(
    eligible: pd.DataFrame,
    bucket_daily: pd.DataFrame,
    *,
    factor_column: str,
    bucket_count: int,
) -> pd.DataFrame:
    bucket_pivot = bucket_daily.pivot(index="trade_date", columns="bucket", values="mean_forward_return")
    bucket_size_pivot = bucket_daily.pivot(index="trade_date", columns="bucket", values="symbol_count")

    daily_rows: list[dict] = []
    for trade_date, day in eligible.groupby("trade_date"):
        obs = day[[factor_column, "fwd_ret_5d_open_to_close", "bucket", "symbol"]].dropna()
        if len(obs) == 0 or obs["bucket"].nunique() < bucket_count:
            continue
        ic = obs[factor_column].corr(obs["fwd_ret_5d_open_to_close"], method="spearman")
        bottom = bucket_pivot.loc[trade_date, 1]
        top = bucket_pivot.loc[trade_date, bucket_count]
        daily_rows.append(
            {
                "trade_date": trade_date,
                "cross_section_size": int(len(obs)),
                "ic_spearman": float(ic) if pd.notna(ic) else np.nan,
                "bucket_1_ret": float(bottom),
                f"bucket_{bucket_count}_ret": float(top),
                "long_short_ret": float(top - bottom),
                "bucket_1_size": int(bucket_size_pivot.loc[trade_date, 1]),
                f"bucket_{bucket_count}_size": int(bucket_size_pivot.loc[trade_date, bucket_count]),
            }
        )
    result = pd.DataFrame(daily_rows).sort_values("trade_date")
    if not result.empty:
        result["cum_long_short"] = (1.0 + result["long_short_ret"].fillna(0.0)).cumprod() - 1.0
    return result


def build_bucket_summary(bucket_daily: pd.DataFrame) -> pd.DataFrame:
    if bucket_daily.empty:
        return pd.DataFrame(
            columns=[
                "bucket",
                "observation_days",
                "avg_symbol_count",
                "mean_forward_return",
                "positive_ratio",
                "final_signal_cumulative_return",
            ]
        )
    summary = (
        bucket_daily.groupby("bucket", as_index=False)
        .agg(
            observation_days=("trade_date", "nunique"),
            avg_symbol_count=("symbol_count", "mean"),
            mean_forward_return=("mean_forward_return", "mean"),
            positive_ratio=("mean_forward_return", lambda s: float((s > 0).mean())),
        )
        .sort_values("bucket")
    )
    finals = []
    for bucket, day in bucket_daily.groupby("bucket"):
        curve = (1.0 + day.sort_values("trade_date")["mean_forward_return"].fillna(0.0)).cumprod() - 1.0
        finals.append(
            {
                "bucket": bucket,
                "final_signal_cumulative_return": float(curve.iloc[-1]) if not curve.empty else np.nan,
            }
        )
    return summary.merge(pd.DataFrame(finals), on="bucket", how="left")


def build_cumulative_returns(bucket_daily: pd.DataFrame, daily_metrics: pd.DataFrame) -> pd.DataFrame:
    if bucket_daily.empty:
        return pd.DataFrame(columns=["trade_date", "long_short"])
    pivot = (
        bucket_daily.pivot(index="trade_date", columns="bucket", values="mean_forward_return")
        .sort_index()
        .fillna(0.0)
    )
    cumulative = pd.DataFrame(index=pivot.index)
    for bucket in pivot.columns:
        cumulative[f"bucket_{int(bucket)}_signal_curve"] = (1.0 + pivot[bucket]).cumprod() - 1.0
    if not daily_metrics.empty:
        long_short = daily_metrics.set_index("trade_date")["long_short_ret"].sort_index().fillna(0.0)
        cumulative["long_short_signal_curve"] = (1.0 + long_short).cumprod() - 1.0
    return cumulative.reset_index()


def build_factor_distribution(eligible: pd.DataFrame, factor_column: str) -> pd.DataFrame:
    if eligible.empty:
        return pd.DataFrame(
            columns=["trade_date", "cross_section_size", "mean", "std", "p05", "p50", "p95", "industry_count"]
        )
    rows = []
    for trade_date, day in eligible.groupby("trade_date"):
        factor = day[factor_column].dropna()
        rows.append(
            {
                "trade_date": trade_date,
                "cross_section_size": int(len(factor)),
                "mean": float(factor.mean()) if not factor.empty else np.nan,
                "std": float(factor.std(ddof=0)) if not factor.empty else np.nan,
                "p05": float(factor.quantile(0.05)) if not factor.empty else np.nan,
                "p50": float(factor.quantile(0.50)) if not factor.empty else np.nan,
                "p95": float(factor.quantile(0.95)) if not factor.empty else np.nan,
                "industry_count": int(day["industry"].where(day["industry"] != "UNKNOWN").dropna().nunique()),
            }
        )
    return pd.DataFrame(rows).sort_values("trade_date")


def summarize_results(
    panel: pd.DataFrame,
    eligible: pd.DataFrame,
    daily_metrics: pd.DataFrame,
    config: SmokeTestConfig,
    manifest: dict,
    preprocess_summary: dict,
) -> dict:
    summary = {
        "factor_name": config.factor_name,
        "snapshot_id": manifest.get("snapshot_id"),
        "signal_trade_date_start": None,
        "signal_trade_date_end": None,
        "requested_symbol_count": len(manifest.get("query", {}).get("requested_symbols", [])),
        "base_universe_count": int(panel["symbol"].nunique()),
        "tradeable_symbol_count": int(eligible["symbol"].nunique()),
        "tradeable_rows": int(len(eligible)),
        "daily_observations": int(len(daily_metrics)),
        "avg_cross_section_size": float(daily_metrics["cross_section_size"].mean()) if not daily_metrics.empty else np.nan,
        "mean_ic": float(daily_metrics["ic_spearman"].mean()) if not daily_metrics.empty else np.nan,
        "ic_ir": float(
            daily_metrics["ic_spearman"].mean() / daily_metrics["ic_spearman"].std(ddof=1)
        )
        if len(daily_metrics) > 1 and pd.notna(daily_metrics["ic_spearman"].std(ddof=1)) and daily_metrics["ic_spearman"].std(ddof=1) != 0
        else np.nan,
        "ic_positive_ratio": float((daily_metrics["ic_spearman"] > 0).mean()) if not daily_metrics.empty else np.nan,
        "mean_long_short_ret": float(daily_metrics["long_short_ret"].mean()) if not daily_metrics.empty else np.nan,
        "long_short_positive_ratio": float((daily_metrics["long_short_ret"] > 0).mean()) if not daily_metrics.empty else np.nan,
        "final_long_short_signal_curve": float(daily_metrics["cum_long_short"].iloc[-1]) if not daily_metrics.empty else np.nan,
        "mean_bucket_1_ret": float(daily_metrics["bucket_1_ret"].mean()) if not daily_metrics.empty else np.nan,
        f"mean_bucket_{config.bucket_count}_ret": float(daily_metrics[f"bucket_{config.bucket_count}_ret"].mean())
        if not daily_metrics.empty
        else np.nan,
        "preprocess_summary": preprocess_summary,
    }
    if not daily_metrics.empty:
        summary["signal_trade_date_start"] = daily_metrics["trade_date"].min().strftime("%Y-%m-%d")
        summary["signal_trade_date_end"] = daily_metrics["trade_date"].max().strftime("%Y-%m-%d")
    return summary


def build_bias_checks(panel: pd.DataFrame, config: SmokeTestConfig) -> dict:
    eligible = panel.loc[panel["is_tradeable"]].copy()
    overlap_days = (eligible["exit_trade_date"] - eligible["trade_date"]).dt.days.min()
    return {
        "factor_definition": f"close_t / close_t-{config.lookback_days} - 1",
        "forward_return_definition": f"close_t+{config.holding_days} / open_t+1 - 1",
        "factor_uses_only_dates_lte_t": True,
        "forward_window_starts_next_trading_day": bool((eligible["next_open"] > 0).all()) if not eligible.empty else False,
        "minimum_calendar_gap_trade_to_exit": int(overlap_days) if pd.notna(overlap_days) else None,
        "same_day_price_used_for_forward_return": False,
        "suspension_filter_applied_on_t_and_t_plus_1": True,
        "industry_neutralization_requested": config.industry_neutralize,
        "holding_windows_overlap": True,
    }


def build_report(
    output_dir: Path,
    config: SmokeTestConfig,
    summary: dict,
    bias_checks: dict,
    universe_diagnostics: dict,
    bucket_summary: pd.DataFrame,
    daily_metrics: pd.DataFrame,
) -> None:
    top_ic = daily_metrics.copy()
    bottom_ic = daily_metrics.copy()
    for frame in (top_ic, bottom_ic):
        if not frame.empty:
            frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.strftime("%Y-%m-%d")
    top_ic = top_ic.nlargest(5, "ic_spearman")[["trade_date", "cross_section_size", "ic_spearman", "long_short_ret"]]
    bottom_ic = bottom_ic.nsmallest(5, "ic_spearman")[["trade_date", "cross_section_size", "ic_spearman", "long_short_ret"]]
    preprocess = summary["preprocess_summary"]
    report = f"""# A股价格因子标准模板报告

## 数据口径

- 因子：{config.factor_name}，原始定义为过去 {config.lookback_days} 个交易日收盘动量。
- 输入清洗表：`{config.prefilter_path}`、`{config.financial_universe_path}`。
- 研究快照：`{config.snapshot_dir}`。
- 回报口径：`close_t+{config.holding_days} / open_t+1 - 1`，信号日收盘后生成，下一交易日开盘入场，持有 {config.holding_days} 个交易日后按收盘退出。

## 股票池筛选

- 仅保留清洗后 `include_price_factors=1` 且未被 `exclude_from_price_backtest` 排除的股票。
- 仅保留 `has_1d_cache=1`、`financial_status=ok` 的股票。
- 动态过滤：上市满 {config.min_listing_days} 天、信号日和下一交易日均未停牌、20 日平均成交额位于当日截面后 {int((1-config.liquidity_quantile)*100)}%。
- 本次模板测试基础股票池 {summary['base_universe_count']} 只，实际可交易样本 {summary['tradeable_symbol_count']} 只。

静态过滤诊断：

```json
{json.dumps(universe_diagnostics, ensure_ascii=False, indent=2)}
```

## 因子预处理

- Winsorize：按日截面对原始因子做 `{config.winsor_lower_quantile:.0%}` / `{config.winsor_upper_quantile:.0%}` 分位裁剪。
- 行业中性：{"启用" if preprocess["industry_neutralization_applied_days"] > 0 else "未启用或无足够行业覆盖"}。
- 标准化：按日截面对处理后的因子做 z-score。
- 行业中性生效交易日（预筛选阶段）：{preprocess['industry_neutralization_applied_days']}
- 行业中性生效行数：{preprocess['industry_neutralization_applied_rows']}

## 前视偏差控制

- 因子仅使用 `t` 及以前的收盘价。
- 未来收益从 `t+1` 开始，不使用 `t` 当日可见但不可成交的收盘价作为入场价。
- 停牌过滤覆盖 `t` 和 `t+1`，避免信号可见但次日无法成交。

## 主要指标

- 有效观测日：{summary['daily_observations']}
- 平均截面样本数：{summary['avg_cross_section_size']:.2f}
- 平均 Spearman IC：{summary['mean_ic']:.4f}
- IC IR：{summary['ic_ir']:.4f}
- IC 为正占比：{summary['ic_positive_ratio']:.2%}
- 多空组合平均收益（Q{config.bucket_count}-Q1）：{summary['mean_long_short_ret']:.4%}
- 多空收益为正占比：{summary['long_short_positive_ratio']:.2%}
- 多空信号曲线累计值：{summary['final_long_short_signal_curve']:.4%}

## 分层回测摘要

{bucket_summary.to_markdown(index=False) if not bucket_summary.empty else "暂无足够样本形成分层结果。"}

注：分层累计值基于重叠持有窗口的信号曲线，仅用于研究比较，不直接等同于可交易净值。

## 最好 / 最差 5 个截面日

### Top 5 IC

{top_ic.to_markdown(index=False) if not top_ic.empty else "暂无结果"}

### Bottom 5 IC

{bottom_ic.to_markdown(index=False) if not bottom_ic.empty else "暂无结果"}

## 偏差检查摘要

```json
{json.dumps(bias_checks, ensure_ascii=False, indent=2)}
```

## 输出文件

- `daily_metrics.csv`：按日 IC、多空收益和头尾分组收益
- `bucket_daily_returns.csv`：按日分层收益
- `bucket_summary.csv`：各分组平均收益和信号曲线累计值
- `cumulative_returns.csv`：分组与多空信号曲线
- `factor_distribution.csv`：按日因子分布统计
- `eligible_panel.parquet`：最终可交易面板
"""
    (output_dir / "report.md").write_text(report, encoding="utf-8")


def main() -> None:
    args = parse_args()
    prefilter, financial_universe, daily_bar, instrument, manifest = load_inputs(
        args.prefilter_path,
        args.financial_universe_path,
        args.snapshot_dir,
    )
    symbol_list = sorted(set(manifest.get("query", {}).get("requested_symbols", [])))
    industry_map, industry_meta = load_industry_reference(
        symbol_list,
        source=args.industry_source,
        cache_path=args.industry_cache_path,
        industry_map_path=args.industry_map_path,
    )
    base_universe = build_base_universe(prefilter, financial_universe, instrument, industry_map)

    config = SmokeTestConfig(
        factor_name=args.factor_name,
        lookback_days=args.lookback_days,
        holding_days=args.holding_days,
        min_listing_days=args.min_listing_days,
        min_cross_section=args.min_cross_section,
        liquidity_quantile=args.liquidity_quantile,
        winsor_lower_quantile=args.winsor_lower_quantile,
        winsor_upper_quantile=args.winsor_upper_quantile,
        bucket_count=args.bucket_count,
        industry_source=args.industry_source,
        industry_neutralize=not args.disable_industry_neutralization,
        industry_cache_path=str(args.industry_cache_path),
        industry_map_path=str(args.industry_map_path) if args.industry_map_path else "",
        prefilter_path=str(args.prefilter_path),
        financial_universe_path=str(args.financial_universe_path),
        snapshot_dir=str(args.snapshot_dir),
    )

    panel, preprocess_summary = prepare_panel(
        daily_bar=daily_bar,
        base_universe=base_universe,
        lookback_days=config.lookback_days,
        holding_days=config.holding_days,
        min_listing_days=config.min_listing_days,
        liquidity_quantile=config.liquidity_quantile,
        winsor_lower_quantile=config.winsor_lower_quantile,
        winsor_upper_quantile=config.winsor_upper_quantile,
        industry_neutralize=config.industry_neutralize,
    )
    eligible, bucket_daily = compute_bucket_daily_returns(
        panel,
        factor_column="factor_value",
        bucket_count=config.bucket_count,
        min_cross_section=config.min_cross_section,
    )
    daily_metrics = compute_daily_metrics(
        eligible,
        bucket_daily,
        factor_column="factor_value",
        bucket_count=config.bucket_count,
    )
    bucket_summary = build_bucket_summary(bucket_daily)
    cumulative_returns = build_cumulative_returns(bucket_daily, daily_metrics)
    factor_distribution = build_factor_distribution(eligible, "factor_value")
    summary = summarize_results(panel, eligible, daily_metrics, config, manifest, preprocess_summary)
    bias_checks = build_bias_checks(panel, config)
    universe_diagnostics = build_universe_diagnostics(
        prefilter=prefilter,
        financial_universe=financial_universe,
        instrument=instrument,
        manifest=manifest,
        base_universe=base_universe,
        industry_meta=industry_meta,
    )

    output_dir = args.output_root / manifest["snapshot_id"] / config.factor_name
    output_dir.mkdir(parents=True, exist_ok=True)

    eligible.to_parquet(output_dir / "eligible_panel.parquet", index=False)
    daily_metrics.to_csv(output_dir / "daily_metrics.csv", index=False)
    bucket_daily.to_csv(output_dir / "bucket_daily_returns.csv", index=False)
    bucket_summary.to_csv(output_dir / "bucket_summary.csv", index=False)
    cumulative_returns.to_csv(output_dir / "cumulative_returns.csv", index=False)
    factor_distribution.to_csv(output_dir / "factor_distribution.csv", index=False)
    base_universe.sort_values("symbol").to_csv(output_dir / "base_universe.csv", index=False)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "bias_checks.json").write_text(
        json.dumps(bias_checks, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "universe_diagnostics.json").write_text(
        json.dumps(universe_diagnostics, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "preprocess_summary.json").write_text(
        json.dumps(preprocess_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "config.json").write_text(
        json.dumps(asdict(config), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    build_report(
        output_dir,
        config,
        summary,
        bias_checks,
        universe_diagnostics,
        bucket_summary,
        daily_metrics,
    )

    print(json.dumps({"output_dir": str(output_dir), "summary": summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
