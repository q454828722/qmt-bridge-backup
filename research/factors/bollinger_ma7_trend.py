#!/usr/bin/env python3
"""A股布林带 + 7日均线右侧趋势信号研究脚本。"""

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

from research.factors.price_momentum_smoke_test import (  # noqa: E402
    DEFAULT_FINANCIAL_UNIVERSE,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_PREFILTER,
    DEFAULT_SNAPSHOT,
    DEFAULT_TUSHARE_CACHE,
    build_base_universe,
    build_universe_diagnostics,
    load_industry_reference,
    load_inputs,
    safe_qcut,
    winsorize_series,
    zscore_series,
)


DEFAULT_SNAPSHOT_ROOT = Path("research/output/snapshots")
FACTOR_NAME = "bollinger_ma7_trend"


@dataclass
class BollingerMa7Config:
    factor_name: str
    bollinger_window: int
    bollinger_std_multiplier: float
    ma_window: int
    ma_slope_days: int
    holding_days: int
    min_listing_days: int
    min_cross_section: int
    liquidity_window: int
    liquidity_quantile: float
    bandwidth_min_quantile: float
    bandwidth_max_quantile: float
    max_next_open_gap: float
    min_signal_strength: float
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
    parser.add_argument(
        "--snapshot-dir",
        type=Path,
        default=DEFAULT_SNAPSHOT,
        help="研究快照目录；默认沿用价格动量研究模板的主快照。",
    )
    parser.add_argument("--snapshot-root", type=Path, default=DEFAULT_SNAPSHOT_ROOT)
    parser.add_argument(
        "--use-latest-snapshot",
        action="store_true",
        help="忽略 --snapshot-dir，自动选择 research/output/snapshots 下最新可用快照。",
    )
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--bollinger-window", type=int, default=20)
    parser.add_argument("--bollinger-std-multiplier", type=float, default=2.0)
    parser.add_argument("--ma-window", type=int, default=7)
    parser.add_argument("--ma-slope-days", type=int, default=3)
    parser.add_argument("--holding-days", type=int, default=5)
    parser.add_argument("--min-listing-days", type=int, default=120)
    parser.add_argument("--min-cross-section", type=int, default=3)
    parser.add_argument("--liquidity-window", type=int, default=20)
    parser.add_argument("--liquidity-quantile", type=float, default=0.3)
    parser.add_argument("--bandwidth-min-quantile", type=float, default=0.35)
    parser.add_argument("--bandwidth-max-quantile", type=float, default=0.95)
    parser.add_argument(
        "--max-next-open-gap",
        type=float,
        default=0.095,
        help="过滤次日开盘相对前收盘过高的样本，默认近似规避一字涨停或极端跳空。",
    )
    parser.add_argument("--min-signal-strength", type=float, default=0.0)
    parser.add_argument("--winsor-lower-quantile", type=float, default=0.05)
    parser.add_argument("--winsor-upper-quantile", type=float, default=0.95)
    parser.add_argument("--bucket-count", type=int, default=3)
    parser.add_argument(
        "--industry-source",
        choices=["auto", "qmt", "tushare", "none"],
        default="auto",
        help="行业映射来源；auto 优先 QMT GICS4 本地缓存，再回退到 Tushare 缓存。",
    )
    parser.add_argument(
        "--disable-industry-neutralization",
        action="store_true",
        help="即使行业映射可用，也跳过行业去均值。",
    )
    parser.add_argument("--industry-cache-path", type=Path, default=DEFAULT_TUSHARE_CACHE)
    parser.add_argument("--industry-map-path", type=Path, default=None)
    return parser.parse_args()


def find_latest_snapshot(snapshot_root: Path) -> Path:
    if not snapshot_root.exists():
        raise FileNotFoundError(snapshot_root)
    candidates = []
    for path in snapshot_root.iterdir():
        if not path.is_dir():
            continue
        manifest_path = path / "manifest.json"
        if not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        datasets = manifest.get("datasets", {})
        daily_path = datasets.get("daily_bar", {}).get("relative_path", "")
        instrument_path = datasets.get("instrument", {}).get("relative_path", "")
        if daily_path and instrument_path:
            required_files = [path / daily_path, path / instrument_path]
        else:
            suffix = "csv" if manifest.get("storage_format") == "csv" else "parquet"
            required_files = [path / f"daily_bar.{suffix}", path / f"instrument.{suffix}"]
        if all(file.exists() for file in required_files):
            candidates.append(path)
    if not candidates:
        raise FileNotFoundError(f"No usable snapshot found under {snapshot_root}")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _first_non_empty_text(series: pd.Series) -> str:
    values = series.dropna().astype(str).str.strip()
    values = values[values != ""]
    return values.iloc[0] if not values.empty else ""


def prepare_panel(
    daily_bar: pd.DataFrame,
    base_universe: pd.DataFrame,
    *,
    config: BollingerMa7Config,
) -> tuple[pd.DataFrame, dict]:
    df = daily_bar.merge(
        base_universe[["symbol", "name", "list_date", "industry"]],
        on="symbol",
        how="inner",
    ).copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    df["list_date"] = pd.to_datetime(df["list_date"].astype("Int64").astype(str), format="%Y%m%d", errors="coerce")
    numeric_columns = ["open", "high", "low", "close", "volume", "amount", "preClose", "suspendFlag"]
    for column in numeric_columns:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df.sort_values(["symbol", "trade_date"]).reset_index(drop=True)

    grouped = df.groupby("symbol", group_keys=False)
    close = grouped["close"]
    df["days_since_list"] = (df["trade_date"] - df["list_date"]).dt.days
    df["ma_mid"] = close.transform(
        lambda s: s.rolling(config.bollinger_window, min_periods=config.bollinger_window).mean()
    )
    df["bb_std"] = close.transform(
        lambda s: s.rolling(config.bollinger_window, min_periods=config.bollinger_window).std(ddof=0)
    )
    df["upper_band"] = df["ma_mid"] + config.bollinger_std_multiplier * df["bb_std"]
    df["lower_band"] = df["ma_mid"] - config.bollinger_std_multiplier * df["bb_std"]
    df["bandwidth"] = (df["upper_band"] - df["lower_band"]) / df["ma_mid"].replace(0, np.nan)
    df["ma7"] = close.transform(lambda s: s.rolling(config.ma_window, min_periods=config.ma_window).mean())
    df["ma7_prev"] = grouped["ma7"].shift(1)
    df["ma7_slope"] = grouped["ma7"].pct_change(config.ma_slope_days, fill_method=None)
    df["close_prev"] = grouped["close"].shift(1)
    df["upper_band_prev"] = grouped["upper_band"].shift(1)
    df["ma_mid_prev"] = grouped["ma_mid"].shift(1)
    df["amt_ma"] = grouped["amount"].transform(
        lambda s: s.rolling(config.liquidity_window, min_periods=config.liquidity_window).mean()
    )
    df["next_open"] = grouped["open"].shift(-1)
    df["next_preclose"] = grouped["preClose"].shift(-1)
    df["exit_close"] = grouped["close"].shift(-config.holding_days)
    df["exit_trade_date"] = grouped["trade_date"].shift(-config.holding_days)
    df["next_suspend"] = grouped["suspendFlag"].shift(-1)
    df["exit_suspend"] = grouped["suspendFlag"].shift(-config.holding_days)
    df["fwd_ret_open_to_close"] = df["exit_close"] / df["next_open"] - 1.0
    df["next_open_gap"] = df["next_open"] / df["next_preclose"].replace(0, np.nan) - 1.0

    df["liquidity_cut"] = df.groupby("trade_date")["amt_ma"].transform(
        lambda s: s.quantile(config.liquidity_quantile) if s.notna().sum() else np.nan
    )
    df["bandwidth_min_cut"] = df.groupby("trade_date")["bandwidth"].transform(
        lambda s: s.quantile(config.bandwidth_min_quantile) if s.notna().sum() else np.nan
    )
    df["bandwidth_max_cut"] = df.groupby("trade_date")["bandwidth"].transform(
        lambda s: s.quantile(config.bandwidth_max_quantile) if s.notna().sum() else np.nan
    )

    df["upper_breakout"] = (df["close"] > df["upper_band"]) & (df["close_prev"] <= df["upper_band_prev"])
    df["mid_reclaim"] = (
        (df["close"] > df["ma_mid"])
        & (df["close_prev"] <= df["ma_mid_prev"])
        & (df["ma7"] > df["ma_mid"])
    )
    df["ma7_up"] = (df["ma7"] > df["ma7_prev"]) & (df["ma7_slope"] > 0)
    df["close_above_ma7"] = df["close"] > df["ma7"]
    df["bandwidth_filter"] = (
        df["bandwidth"].notna()
        & df["bandwidth_min_cut"].notna()
        & df["bandwidth_max_cut"].notna()
        & (df["bandwidth"] >= df["bandwidth_min_cut"])
        & (df["bandwidth"] <= df["bandwidth_max_cut"])
    )
    df["trend_trigger"] = (df["upper_breakout"] | df["mid_reclaim"]) & df["close_above_ma7"] & df["ma7_up"]
    df["signal_type"] = np.select(
        [df["upper_breakout"] & df["mid_reclaim"], df["upper_breakout"], df["mid_reclaim"]],
        ["upper_and_mid", "upper_breakout", "mid_reclaim"],
        default="",
    )

    upper_gap = df["close"] / df["upper_band"].replace(0, np.nan) - 1.0
    mid_gap = df["close"] / df["ma_mid"].replace(0, np.nan) - 1.0
    ma7_gap = df["close"] / df["ma7"].replace(0, np.nan) - 1.0
    df["signal_strength_raw"] = (
        upper_gap.clip(lower=0).fillna(0.0) * 2.0
        + mid_gap.clip(lower=0).fillna(0.0)
        + ma7_gap.clip(lower=0).fillna(0.0)
        + df["ma7_slope"].clip(lower=0).fillna(0.0)
    )
    df.loc[~df["trend_trigger"], "signal_strength_raw"] = 0.0

    df["is_tradeable_base"] = (
        (df["days_since_list"] >= config.min_listing_days)
        & (df["suspendFlag"] == 0)
        & (df["next_suspend"] == 0)
        & (df["exit_suspend"] == 0)
        & df["close"].gt(0)
        & df["next_open"].gt(0)
        & df["exit_close"].gt(0)
        & df["ma_mid"].notna()
        & df["upper_band"].notna()
        & df["ma7"].notna()
        & df["ma7_slope"].notna()
        & df["amt_ma"].notna()
        & df["liquidity_cut"].notna()
        & df["amt_ma"].ge(df["liquidity_cut"])
        & df["next_open_gap"].le(config.max_next_open_gap)
    )
    df["is_signal_base"] = (
        df["is_tradeable_base"]
        & df["trend_trigger"]
        & df["bandwidth_filter"]
        & df["signal_strength_raw"].gt(config.min_signal_strength)
    )
    df["strength_bucket"] = pd.cut(
        df["signal_strength_raw"],
        bins=[-np.inf, 0.08, 0.25, np.inf],
        labels=["low", "medium", "high"],
    ).astype("object")
    df.loc[~df["is_signal_base"], "strength_bucket"] = ""

    df["signal_strength_winsorized"] = np.nan
    df["signal_strength_neutralized"] = np.nan
    df["factor_value"] = np.nan
    neutralization_days = 0
    neutralization_rows = 0
    signal_rows_before_processing = int(df["is_signal_base"].sum())

    for trade_date, day in df.loc[df["is_signal_base"]].groupby("trade_date"):
        raw = day["signal_strength_raw"].astype(float)
        winsorized = winsorize_series(raw, config.winsor_lower_quantile, config.winsor_upper_quantile)
        neutralized = winsorized.copy()
        day_has_industry = day["industry"].fillna("").replace("", "UNKNOWN").nunique() >= 2
        day_can_neutralize = config.industry_neutralize and day_has_industry and (day["industry"] != "UNKNOWN").any()
        if day_can_neutralize:
            neutralized = winsorized - day.groupby("industry")["signal_strength_raw"].transform(
                lambda s: winsorize_series(
                    s.astype(float),
                    config.winsor_lower_quantile,
                    config.winsor_upper_quantile,
                ).mean()
            )
            neutralization_days += 1
            neutralization_rows += int(len(day))
        zscore = zscore_series(neutralized)
        if zscore.notna().sum() == 0:
            zscore = neutralized.astype(float)
        idx = day.index
        df.loc[idx, "signal_strength_winsorized"] = winsorized
        df.loc[idx, "signal_strength_neutralized"] = neutralized
        df.loc[idx, "factor_value"] = zscore

    df["is_signal"] = df["is_signal_base"] & df["factor_value"].notna()
    preprocess_summary = {
        "winsorization": {
            "lower_quantile": config.winsor_lower_quantile,
            "upper_quantile": config.winsor_upper_quantile,
        },
        "standardization": "daily_signal_cross_section_zscore",
        "industry_neutralization_requested": config.industry_neutralize,
        "industry_neutralization_applied_days": neutralization_days,
        "industry_neutralization_applied_rows": neutralization_rows,
        "tradeable_rows_before_signal": int(df["is_tradeable_base"].sum()),
        "signal_rows_before_factor_processing": signal_rows_before_processing,
        "signal_rows_after_factor_processing": int(df["is_signal"].sum()),
        "signal_days_after_factor_processing": int(df.loc[df["is_signal"], "trade_date"].nunique()),
    }
    return df, preprocess_summary


def compute_signal_daily_returns(panel: pd.DataFrame) -> pd.DataFrame:
    signal_panel = panel.loc[panel["is_signal"]].copy()
    if signal_panel.empty:
        return pd.DataFrame(
            columns=[
                "trade_date",
                "signal_count",
                "mean_forward_return",
                "median_forward_return",
                "positive_ratio",
                "mean_factor_value",
                "mean_signal_strength_raw",
            ]
        )
    result = (
        signal_panel.groupby("trade_date", as_index=False)
        .agg(
            signal_count=("symbol", "size"),
            mean_forward_return=("fwd_ret_open_to_close", "mean"),
            median_forward_return=("fwd_ret_open_to_close", "median"),
            positive_ratio=("fwd_ret_open_to_close", lambda s: float((s > 0).mean())),
            mean_factor_value=("factor_value", "mean"),
            mean_signal_strength_raw=("signal_strength_raw", "mean"),
        )
        .sort_values("trade_date")
    )
    result["signal_curve"] = (1.0 + result["mean_forward_return"].fillna(0.0)).cumprod() - 1.0
    return result


def compute_bucket_daily_returns(
    panel: pd.DataFrame,
    *,
    bucket_count: int,
    min_cross_section: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    eligible = panel.loc[panel["is_signal"]].copy()
    if eligible.empty:
        return eligible, pd.DataFrame(
            columns=["trade_date", "bucket", "symbol_count", "mean_factor_value", "mean_forward_return"]
        )
    eligible["bucket"] = eligible.groupby("trade_date")["factor_value"].transform(
        lambda s: safe_qcut(s, buckets=bucket_count)
    )
    eligible = eligible.dropna(subset=["bucket"]).copy()
    if eligible.empty:
        return eligible, pd.DataFrame(
            columns=["trade_date", "bucket", "symbol_count", "mean_factor_value", "mean_forward_return"]
        )
    eligible["bucket"] = eligible["bucket"].astype(int)
    cross_section = eligible.groupby("trade_date")["symbol"].size().rename("cross_section_size")
    valid_dates = cross_section[cross_section >= min_cross_section].index
    eligible = eligible[eligible["trade_date"].isin(valid_dates)].copy()
    bucket_daily = (
        eligible.groupby(["trade_date", "bucket"], as_index=False)
        .agg(
            symbol_count=("symbol", "size"),
            mean_factor_value=("factor_value", "mean"),
            mean_forward_return=("fwd_ret_open_to_close", "mean"),
        )
        .sort_values(["trade_date", "bucket"])
    )
    return eligible, bucket_daily


def compute_daily_metrics(
    bucketed_signals: pd.DataFrame,
    bucket_daily: pd.DataFrame,
    *,
    bucket_count: int,
) -> pd.DataFrame:
    if bucketed_signals.empty or bucket_daily.empty:
        return pd.DataFrame(
            columns=[
                "trade_date",
                "cross_section_size",
                "ic_spearman",
                "bucket_1_ret",
                f"bucket_{bucket_count}_ret",
                "long_short_ret",
                "bucket_1_size",
                f"bucket_{bucket_count}_size",
            ]
        )
    bucket_pivot = bucket_daily.pivot(index="trade_date", columns="bucket", values="mean_forward_return")
    bucket_size_pivot = bucket_daily.pivot(index="trade_date", columns="bucket", values="symbol_count")

    daily_rows: list[dict] = []
    for trade_date, day in bucketed_signals.groupby("trade_date"):
        obs = day[["factor_value", "fwd_ret_open_to_close", "bucket", "symbol"]].dropna()
        if len(obs) == 0 or obs["bucket"].nunique() < bucket_count:
            continue
        ic = obs["factor_value"].corr(obs["fwd_ret_open_to_close"], method="spearman")
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


def build_cumulative_returns(
    bucket_daily: pd.DataFrame,
    signal_daily: pd.DataFrame,
    daily_metrics: pd.DataFrame,
) -> pd.DataFrame:
    frames = []
    if not bucket_daily.empty:
        pivot = (
            bucket_daily.pivot(index="trade_date", columns="bucket", values="mean_forward_return")
            .sort_index()
            .fillna(0.0)
        )
        cumulative = pd.DataFrame(index=pivot.index)
        for bucket in pivot.columns:
            cumulative[f"bucket_{int(bucket)}_signal_curve"] = (1.0 + pivot[bucket]).cumprod() - 1.0
        frames.append(cumulative)
    if not signal_daily.empty:
        signal_curve = (
            signal_daily.set_index("trade_date")["signal_curve"]
            .sort_index()
            .rename("all_signal_curve")
        )
        frames.append(signal_curve.to_frame())
    if not daily_metrics.empty and "long_short_ret" in daily_metrics:
        long_short = daily_metrics.set_index("trade_date")["long_short_ret"].sort_index().fillna(0.0)
        frames.append(
            ((1.0 + long_short).cumprod() - 1.0)
            .rename("long_short_signal_curve")
            .to_frame()
        )
    if not frames:
        return pd.DataFrame(columns=["trade_date"])
    return pd.concat(frames, axis=1).reset_index()


def build_factor_distribution(panel: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for trade_date, day in panel.loc[panel["is_tradeable_base"]].groupby("trade_date"):
        signals = day.loc[day["is_signal"]]
        rows.append(
            {
                "trade_date": trade_date,
                "tradeable_count": int(len(day)),
                "signal_count": int(len(signals)),
                "signal_rate": float(len(signals) / len(day)) if len(day) else np.nan,
                "mean_bandwidth": float(day["bandwidth"].mean()) if not day.empty else np.nan,
                "mean_ma7_slope": float(day["ma7_slope"].mean()) if not day.empty else np.nan,
                "mean_signal_strength_raw": float(signals["signal_strength_raw"].mean())
                if not signals.empty
                else np.nan,
                "industry_count": int(day["industry"].where(day["industry"] != "UNKNOWN").dropna().nunique()),
            }
        )
    return pd.DataFrame(rows).sort_values("trade_date")


def build_signal_events(panel: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "trade_date",
        "symbol",
        "name",
        "industry",
        "signal_type",
        "strength_bucket",
        "close",
        "ma_mid",
        "upper_band",
        "lower_band",
        "ma7",
        "ma7_slope",
        "bandwidth",
        "upper_breakout",
        "mid_reclaim",
        "signal_strength_raw",
        "factor_value",
        "next_open",
        "exit_trade_date",
        "exit_close",
        "fwd_ret_open_to_close",
    ]
    events = panel.loc[panel["is_signal"], columns].copy()
    return events.sort_values(["trade_date", "factor_value", "symbol"], ascending=[True, False, True])


def build_signal_group_summary(signal_events: pd.DataFrame) -> pd.DataFrame:
    if signal_events.empty:
        return pd.DataFrame(
            columns=[
                "group_type",
                "group_value",
                "signal_count",
                "signal_days",
                "symbol_count",
                "mean_forward_return",
                "median_forward_return",
                "positive_ratio",
                "final_signal_cumulative_return",
            ]
        )
    frames = []
    for group_type, column in [
        ("signal_type", "signal_type"),
        ("strength_bucket", "strength_bucket"),
    ]:
        grouped = (
            signal_events.groupby(column, dropna=False)
            .agg(
                signal_count=("symbol", "size"),
                signal_days=("trade_date", "nunique"),
                symbol_count=("symbol", "nunique"),
                mean_forward_return=("fwd_ret_open_to_close", "mean"),
                median_forward_return=("fwd_ret_open_to_close", "median"),
                positive_ratio=(
                    "fwd_ret_open_to_close",
                    lambda s: float((s > 0).mean()),
                ),
            )
            .reset_index()
            .rename(columns={column: "group_value"})
        )
        grouped["group_type"] = group_type
        finals = []
        for group_value, day in signal_events.groupby(column, dropna=False):
            daily = (
                day.groupby("trade_date")["fwd_ret_open_to_close"]
                .mean()
                .sort_index()
                .fillna(0.0)
            )
            finals.append(
                {
                    "group_value": group_value,
                    "final_signal_cumulative_return": float((1.0 + daily).cumprod().iloc[-1] - 1.0)
                    if not daily.empty
                    else np.nan,
                }
            )
        grouped = grouped.merge(pd.DataFrame(finals), on="group_value", how="left")
        frames.append(grouped)
    result = pd.concat(frames, ignore_index=True)
    return result[
        [
            "group_type",
            "group_value",
            "signal_count",
            "signal_days",
            "symbol_count",
            "mean_forward_return",
            "median_forward_return",
            "positive_ratio",
            "final_signal_cumulative_return",
        ]
    ].sort_values(["group_type", "group_value"])


def build_latest_signal_vector(
    signal_events: pd.DataFrame,
    *,
    config: BollingerMa7Config,
    manifest: dict,
    evidence_path: Path,
) -> pd.DataFrame:
    columns = [
        "stock_code",
        "trade_date",
        "signal_score",
        "confidence",
        "holding_days",
        "factor_family",
        "snapshot_id",
        "evidence_path",
        "risk_notes",
        "blocked_reason",
    ]
    if signal_events.empty:
        return pd.DataFrame(columns=columns)
    latest_date = signal_events["trade_date"].max()
    latest = signal_events.loc[signal_events["trade_date"] == latest_date].copy()
    max_abs = latest["factor_value"].abs().max()
    if pd.isna(max_abs) or max_abs == 0:
        latest["signal_score"] = 0.0
    else:
        latest["signal_score"] = (latest["factor_value"] / max_abs).clip(-1.0, 1.0)
    latest["confidence"] = np.minimum(
        0.65,
        0.35 + latest["signal_strength_raw"].clip(lower=0, upper=0.30),
    )
    latest["risk_notes"] = "技术趋势单因子；未计入交易成本、冲击成本和完整涨跌停可成交性"
    latest["blocked_reason"] = ""
    result = pd.DataFrame(
        {
            "stock_code": latest["symbol"],
            "trade_date": latest["trade_date"].dt.strftime("%Y-%m-%d"),
            "signal_score": latest["signal_score"],
            "confidence": latest["confidence"],
            "holding_days": config.holding_days,
            "factor_family": "price_trend_bollinger_ma",
            "snapshot_id": manifest.get("snapshot_id"),
            "evidence_path": str(evidence_path),
            "risk_notes": latest["risk_notes"],
            "blocked_reason": latest["blocked_reason"],
        }
    )
    return result[columns].sort_values("signal_score", ascending=False)


def summarize_results(
    panel: pd.DataFrame,
    signal_events: pd.DataFrame,
    signal_daily: pd.DataFrame,
    daily_metrics: pd.DataFrame,
    config: BollingerMa7Config,
    manifest: dict,
    preprocess_summary: dict,
) -> dict:
    tradeable = panel.loc[panel["is_tradeable_base"]]
    summary = {
        "factor_name": config.factor_name,
        "snapshot_id": manifest.get("snapshot_id"),
        "signal_trade_date_start": None,
        "signal_trade_date_end": None,
        "requested_symbol_count": len(manifest.get("query", {}).get("requested_symbols", [])),
        "base_universe_count": int(panel["symbol"].nunique()),
        "tradeable_symbol_count": int(tradeable["symbol"].nunique()),
        "tradeable_rows": int(len(tradeable)),
        "signal_symbol_count": int(signal_events["symbol"].nunique())
        if not signal_events.empty
        else 0,
        "signal_rows": int(len(signal_events)),
        "signal_days": int(signal_events["trade_date"].nunique()) if not signal_events.empty else 0,
        "mean_signal_count_per_day": float(signal_daily["signal_count"].mean())
        if not signal_daily.empty
        else np.nan,
        "mean_signal_forward_return": float(signal_daily["mean_forward_return"].mean())
        if not signal_daily.empty
        else np.nan,
        "signal_positive_day_ratio": float((signal_daily["mean_forward_return"] > 0).mean())
        if not signal_daily.empty
        else np.nan,
        "final_all_signal_curve": float(signal_daily["signal_curve"].iloc[-1]) if not signal_daily.empty else np.nan,
        "bucket_daily_observations": int(len(daily_metrics)),
        "avg_bucket_cross_section_size": float(daily_metrics["cross_section_size"].mean())
        if not daily_metrics.empty
        else np.nan,
        "mean_ic": float(daily_metrics["ic_spearman"].mean()) if not daily_metrics.empty else np.nan,
        "ic_ir": float(daily_metrics["ic_spearman"].mean() / daily_metrics["ic_spearman"].std(ddof=1))
        if len(daily_metrics) > 1
        and pd.notna(daily_metrics["ic_spearman"].std(ddof=1))
        and daily_metrics["ic_spearman"].std(ddof=1) != 0
        else np.nan,
        "ic_positive_ratio": float((daily_metrics["ic_spearman"] > 0).mean())
        if not daily_metrics.empty
        else np.nan,
        "mean_long_short_ret": float(daily_metrics["long_short_ret"].mean()) if not daily_metrics.empty else np.nan,
        "long_short_positive_ratio": float((daily_metrics["long_short_ret"] > 0).mean())
        if not daily_metrics.empty
        else np.nan,
        "final_long_short_signal_curve": float(daily_metrics["cum_long_short"].iloc[-1])
        if not daily_metrics.empty
        else np.nan,
        "preprocess_summary": preprocess_summary,
    }
    if not signal_events.empty:
        summary["signal_trade_date_start"] = signal_events["trade_date"].min().strftime("%Y-%m-%d")
        summary["signal_trade_date_end"] = signal_events["trade_date"].max().strftime("%Y-%m-%d")
    return summary


def build_bias_checks(config: BollingerMa7Config, signal_events: pd.DataFrame) -> dict:
    overlap_days = (signal_events["exit_trade_date"] - signal_events["trade_date"]).dt.days.min()
    return {
        "factor_definition": (
            f"BBANDS({config.bollinger_window}, {config.bollinger_std_multiplier}) + "
            f"MA{config.ma_window} right-side breakout/reclaim signal"
        ),
        "signal_conditions": [
            "close_t > upper_band_t and close_t-1 <= upper_band_t-1, "
            "or close_t crosses above middle band with ma7 above middle band",
            "close_t > ma7_t",
            f"ma7_t > ma7_t-1 and ma7 {config.ma_slope_days} day slope > 0",
            "bandwidth within daily cross-sectional quantile filter",
        ],
        "forward_return_definition": f"close_t+{config.holding_days} / open_t+1 - 1",
        "factor_uses_only_dates_lte_t": True,
        "forward_window_starts_next_trading_day": bool((signal_events["next_open"] > 0).all())
        if not signal_events.empty
        else False,
        "minimum_calendar_gap_trade_to_exit": int(overlap_days) if pd.notna(overlap_days) else None,
        "same_day_price_used_for_forward_return": False,
        "suspension_filter_applied_on_t_t_plus_1_and_exit": True,
        "next_open_extreme_gap_filter_applied": True,
        "holding_windows_overlap": True,
    }


def _format_float(value: object, fmt: str) -> str:
    if value is None or pd.isna(value):
        return "nan"
    return format(float(value), fmt)


def json_ready(value: object) -> object:
    if isinstance(value, dict):
        return {key: json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_ready(item) for item in value]
    if isinstance(value, tuple):
        return [json_ready(item) for item in value]
    if isinstance(value, float) and (pd.isna(value) or np.isinf(value)):
        return None
    if isinstance(value, np.floating):
        return None if pd.isna(value) or np.isinf(value) else float(value)
    if isinstance(value, np.integer):
        return int(value)
    if value is pd.NaT:
        return None
    return value


def build_report(
    output_dir: Path,
    config: BollingerMa7Config,
    summary: dict,
    bias_checks: dict,
    universe_diagnostics: dict,
    bucket_summary: pd.DataFrame,
    signal_group_summary: pd.DataFrame,
    signal_daily: pd.DataFrame,
    daily_metrics: pd.DataFrame,
    latest_signal_vector: pd.DataFrame,
) -> None:
    top_signal_days = (
        signal_daily.nlargest(5, "mean_forward_return").copy()
        if not signal_daily.empty
        else signal_daily
    )
    bottom_signal_days = (
        signal_daily.nsmallest(5, "mean_forward_return").copy()
        if not signal_daily.empty
        else signal_daily
    )
    for frame in (top_signal_days, bottom_signal_days):
        if not frame.empty:
            frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.strftime("%Y-%m-%d")
    latest_preview = latest_signal_vector.copy()
    if not latest_preview.empty:
        latest_preview = latest_preview[["stock_code", "trade_date", "signal_score", "confidence", "holding_days"]]

    report = f"""# 布林带 + MA7 右侧趋势研究报告

## 数据口径

- 因子：`{config.factor_name}`。
- 布林带：{config.bollinger_window} 日中轨，{config.bollinger_std_multiplier:g} 倍标准差上下轨。
- MA：{config.ma_window} 日均线，要求 MA 上行且收盘价站上 MA。
- 输入清洗表：`{config.prefilter_path}`、`{config.financial_universe_path}`。
- 研究快照：`{config.snapshot_dir}`。
- 回报口径：`close_t+{config.holding_days} / open_t+1 - 1`，信号日收盘后生成，下一交易日开盘入场，持有 {config.holding_days} 个交易日后按收盘退出。

## 信号定义

- 上轨突破：`close_t > upper_band_t` 且 `close_t-1 <= upper_band_t-1`。
- 中轨收复：`close_t > middle_band_t` 且 `close_t-1 <= middle_band_t-1`，并且 `ma7_t > middle_band_t`。
- 趋势叠加：`close_t > ma7_t`、`ma7_t > ma7_t-1`、MA7 的 {config.ma_slope_days} 日斜率为正。
- 波动过滤：布林带宽处于当日截面 `{config.bandwidth_min_quantile:.0%}` 到 `{config.bandwidth_max_quantile:.0%}` 分位之间。
- 可交易过滤：上市满 {config.min_listing_days} 天，信号日、次日、退出日均未停牌，
  20 日均成交额过当日 `{config.liquidity_quantile:.0%}` 分位，且次日开盘相对前收盘涨幅不高于 {config.max_next_open_gap:.2%}。

## 股票池筛选

- 静态口径沿用研究模板：`include_price_factors=1` 且 `exclude_from_price_backtest=0`，并要求 `has_1d_cache=1`、`financial_status=ok`。
- 基础股票池 {summary['base_universe_count']} 只，动态可交易样本 {summary['tradeable_symbol_count']} 只，
  信号覆盖 {summary['signal_symbol_count']} 只。

静态过滤诊断：

```json
{json.dumps(json_ready(universe_diagnostics), ensure_ascii=False, indent=2)}
```

## 主要指标

- 信号区间：{summary['signal_trade_date_start']} 至 {summary['signal_trade_date_end']}
- 信号行数：{summary['signal_rows']}
- 信号交易日：{summary['signal_days']}
- 平均每日信号数：{_format_float(summary['mean_signal_count_per_day'], '.2f')}
- 信号组合平均收益：{_format_float(summary['mean_signal_forward_return'], '.4%')}
- 信号组合正收益日占比：{_format_float(summary['signal_positive_day_ratio'], '.2%')}
- 信号曲线累计值：{_format_float(summary['final_all_signal_curve'], '.4%')}
- 分组有效观测日：{summary['bucket_daily_observations']}
- 平均 RankIC：{_format_float(summary['mean_ic'], '.4f')}
- RankIC IR：{_format_float(summary['ic_ir'], '.4f')}
- 多空组合平均收益（Q{config.bucket_count}-Q1）：{_format_float(summary['mean_long_short_ret'], '.4%')}
- 多空信号曲线累计值：{_format_float(summary['final_long_short_signal_curve'], '.4%')}

## 分组摘要

### 固定阈值分组

{signal_group_summary.to_markdown(index=False) if not signal_group_summary.empty else "暂无信号分组结果。"}

固定阈值分组不使用全样本分位，`strength_bucket` 规则为 `low < 0.08`、`0.08 <= medium < 0.25`、`high >= 0.25`。

### 按日截面分组

{bucket_summary.to_markdown(index=False) if not bucket_summary.empty else "暂无足够信号形成分组结果。"}

注：分组收益基于重叠持有窗口的信号曲线，仅用于研究比较，不直接等同于可交易净值。

## 最新信号向量

{latest_preview.to_markdown(index=False) if not latest_preview.empty else "最新信号日没有可输出信号向量。"}

## 最好 / 最差信号日

### Top 5

{top_signal_days.to_markdown(index=False) if not top_signal_days.empty else "暂无结果"}

### Bottom 5

{bottom_signal_days.to_markdown(index=False) if not bottom_signal_days.empty else "暂无结果"}

## 前视偏差与可交易性检查

```json
{json.dumps(json_ready(bias_checks), ensure_ascii=False, indent=2)}
```

## 局限

- 本脚本只使用快照内日线数据，不调用实盘交易接口。
- 未计入佣金、印花税、滑点和冲击成本。
- 涨跌停可成交性只用次日开盘极端跳空做保守近似，不等同于逐笔成交可用性。
- 若最新 snapshot 是轻量 smoke 样本，统计显著性不足，应切换到正式全市场 snapshot 复验。

## 输出文件

- `summary.json`：研究摘要
- `report.md`：本报告
- `signal_events.csv`：全部有效信号
- `latest_signal_vector.csv`：最近信号日的 `alpha.signal_vector` 摘要
- `signal_group_summary.csv`：按信号类型和固定强度阈值分组的无未来函数摘要
- `bucket_daily_returns.csv`：按日分组收益
- `bucket_summary.csv`：分组统计
- `daily_metrics.csv`：分组 IC 与多空收益
- `signal_daily_returns.csv`：信号组合按日收益
- `cumulative_returns.csv`：信号与分组累计曲线
- `factor_distribution.csv`：按日覆盖率和原始指标分布
- `eligible_panel.parquet`：最终研究面板
"""
    (output_dir / "report.md").write_text(report, encoding="utf-8")


def main() -> None:
    args = parse_args()
    snapshot_dir = find_latest_snapshot(args.snapshot_root) if args.use_latest_snapshot else args.snapshot_dir
    prefilter, financial_universe, daily_bar, instrument, manifest = load_inputs(
        args.prefilter_path,
        args.financial_universe_path,
        snapshot_dir,
    )
    symbol_list = sorted(set(manifest.get("query", {}).get("requested_symbols", [])))
    industry_map, industry_meta = load_industry_reference(
        symbol_list,
        source=args.industry_source,
        cache_path=args.industry_cache_path,
        industry_map_path=args.industry_map_path,
    )
    base_universe = build_base_universe(prefilter, financial_universe, instrument, industry_map)

    config = BollingerMa7Config(
        factor_name=FACTOR_NAME,
        bollinger_window=args.bollinger_window,
        bollinger_std_multiplier=args.bollinger_std_multiplier,
        ma_window=args.ma_window,
        ma_slope_days=args.ma_slope_days,
        holding_days=args.holding_days,
        min_listing_days=args.min_listing_days,
        min_cross_section=args.min_cross_section,
        liquidity_window=args.liquidity_window,
        liquidity_quantile=args.liquidity_quantile,
        bandwidth_min_quantile=args.bandwidth_min_quantile,
        bandwidth_max_quantile=args.bandwidth_max_quantile,
        max_next_open_gap=args.max_next_open_gap,
        min_signal_strength=args.min_signal_strength,
        winsor_lower_quantile=args.winsor_lower_quantile,
        winsor_upper_quantile=args.winsor_upper_quantile,
        bucket_count=args.bucket_count,
        industry_source=args.industry_source,
        industry_neutralize=not args.disable_industry_neutralization,
        industry_cache_path=str(args.industry_cache_path),
        industry_map_path=str(args.industry_map_path) if args.industry_map_path else "",
        prefilter_path=str(args.prefilter_path),
        financial_universe_path=str(args.financial_universe_path),
        snapshot_dir=str(snapshot_dir),
    )

    panel, preprocess_summary = prepare_panel(daily_bar=daily_bar, base_universe=base_universe, config=config)
    signal_events = build_signal_events(panel)
    signal_group_summary = build_signal_group_summary(signal_events)
    signal_daily = compute_signal_daily_returns(panel)
    bucketed_signals, bucket_daily = compute_bucket_daily_returns(
        panel,
        bucket_count=config.bucket_count,
        min_cross_section=config.min_cross_section,
    )
    daily_metrics = compute_daily_metrics(bucketed_signals, bucket_daily, bucket_count=config.bucket_count)
    bucket_summary = build_bucket_summary(bucket_daily)
    cumulative_returns = build_cumulative_returns(bucket_daily, signal_daily, daily_metrics)
    factor_distribution = build_factor_distribution(panel)
    summary = summarize_results(panel, signal_events, signal_daily, daily_metrics, config, manifest, preprocess_summary)
    bias_checks = build_bias_checks(config, signal_events)
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
    latest_signal_vector = build_latest_signal_vector(
        signal_events,
        config=config,
        manifest=manifest,
        evidence_path=output_dir / "report.md",
    )

    panel.to_parquet(output_dir / "eligible_panel.parquet", index=False)
    signal_events.to_csv(output_dir / "signal_events.csv", index=False)
    latest_signal_vector.to_csv(output_dir / "latest_signal_vector.csv", index=False)
    signal_group_summary.to_csv(output_dir / "signal_group_summary.csv", index=False)
    signal_daily.to_csv(output_dir / "signal_daily_returns.csv", index=False)
    bucket_daily.to_csv(output_dir / "bucket_daily_returns.csv", index=False)
    bucket_summary.to_csv(output_dir / "bucket_summary.csv", index=False)
    daily_metrics.to_csv(output_dir / "daily_metrics.csv", index=False)
    cumulative_returns.to_csv(output_dir / "cumulative_returns.csv", index=False)
    factor_distribution.to_csv(output_dir / "factor_distribution.csv", index=False)
    base_universe.sort_values("symbol").to_csv(output_dir / "base_universe.csv", index=False)
    (output_dir / "summary.json").write_text(
        json.dumps(json_ready(summary), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "bias_checks.json").write_text(
        json.dumps(json_ready(bias_checks), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "universe_diagnostics.json").write_text(
        json.dumps(json_ready(universe_diagnostics), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "preprocess_summary.json").write_text(
        json.dumps(json_ready(preprocess_summary), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "config.json").write_text(
        json.dumps(json_ready(asdict(config)), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    build_report(
        output_dir,
        config,
        summary,
        bias_checks,
        universe_diagnostics,
        bucket_summary,
        signal_group_summary,
        signal_daily,
        daily_metrics,
        latest_signal_vector,
    )

    print(json.dumps(json_ready({"output_dir": str(output_dir), "summary": summary}), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
