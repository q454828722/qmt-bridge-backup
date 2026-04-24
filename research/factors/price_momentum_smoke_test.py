#!/usr/bin/env python3
"""Lightweight A-share price factor smoke test."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd


DEFAULT_PREFILTER = Path("data/yuanqi_replica/basic/quant_backtest_prefilter.csv")
DEFAULT_FINANCIAL_UNIVERSE = Path("data/yuanqi_replica/basic/quant_financial_universe_fresh_only.csv")
DEFAULT_SNAPSHOT = Path(
    "research/output/snapshots/20260424_105437_post_refresh_audit_20260424_105123"
)
DEFAULT_OUTPUT_ROOT = Path("research/output/factor_tests")


@dataclass
class SmokeTestConfig:
    factor_name: str
    lookback_days: int
    holding_days: int
    min_listing_days: int
    min_cross_section: int
    liquidity_quantile: float
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
    return parser.parse_args()


def ensure_exists(path: Path) -> Path:
    if not path.exists():
        raise FileNotFoundError(path)
    return path


def load_inputs(prefilter_path: Path, financial_universe_path: Path, snapshot_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict]:
    prefilter = pd.read_csv(ensure_exists(prefilter_path))
    financial_universe = pd.read_csv(ensure_exists(financial_universe_path))
    snapshot_dir = ensure_exists(snapshot_dir)
    daily_bar = pd.read_parquet(snapshot_dir / "daily_bar.parquet")
    instrument = pd.read_parquet(snapshot_dir / "instrument.parquet")
    manifest = json.loads((snapshot_dir / "manifest.json").read_text(encoding="utf-8"))
    return prefilter, financial_universe, daily_bar, instrument, manifest


def build_base_universe(prefilter: pd.DataFrame, financial_universe: pd.DataFrame, instrument: pd.DataFrame) -> pd.DataFrame:
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
    instruments = instrument[["symbol", "name", "list_date"]].copy()
    instruments["list_date"] = pd.to_numeric(instruments["list_date"], errors="coerce")
    base = instruments.merge(pool, left_on="symbol", right_on="stock_code", how="inner")
    base = base.merge(fresh, left_on="symbol", right_on="stock_code", how="inner", suffixes=("", "_fresh"))
    base = base.drop(columns=["stock_code", "stock_code_fresh"])
    return base.drop_duplicates("symbol")


def build_universe_diagnostics(
    prefilter: pd.DataFrame,
    financial_universe: pd.DataFrame,
    instrument: pd.DataFrame,
    manifest: dict,
    base_universe: pd.DataFrame,
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
    return {
        "requested_symbols": len(requested),
        "snapshot_instrument_symbols": len(requested & instrument_symbols),
        "prefilter_price_ok_symbols": len(requested & prefilter_ok),
        "financial_fresh_ok_symbols": len(requested & financial_ok),
        "intersection_after_static_filters": len(base_symbols),
    }


def prepare_panel(
    daily_bar: pd.DataFrame,
    base_universe: pd.DataFrame,
    lookback_days: int,
    holding_days: int,
    min_listing_days: int,
    liquidity_quantile: float,
) -> pd.DataFrame:
    df = daily_bar.merge(base_universe[["symbol", "list_date"]], on="symbol", how="inner").copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    df["list_date"] = pd.to_datetime(df["list_date"].astype("Int64").astype(str), format="%Y%m%d", errors="coerce")
    df = df.sort_values(["symbol", "trade_date"]).reset_index(drop=True)

    grouped = df.groupby("symbol", group_keys=False)
    df["days_since_list"] = (df["trade_date"] - df["list_date"]).dt.days
    df["mom_20d"] = grouped["close"].pct_change(lookback_days)
    df["amt_ma_20"] = grouped["amount"].transform(lambda s: s.rolling(lookback_days, min_periods=lookback_days).mean())
    df["next_open"] = grouped["open"].shift(-1)
    df["exit_close"] = grouped["close"].shift(-holding_days)
    df["exit_trade_date"] = grouped["trade_date"].shift(-holding_days)
    df["next_suspend"] = grouped["suspendFlag"].shift(-1)
    df["fwd_ret_5d_open_to_close"] = df["exit_close"] / df["next_open"] - 1.0

    df["liquidity_cut"] = df.groupby("trade_date")["amt_ma_20"].transform(
        lambda s: s.quantile(liquidity_quantile) if s.notna().sum() else np.nan
    )
    df["is_tradeable"] = (
        (df["days_since_list"] >= min_listing_days)
        & (df["suspendFlag"] == 0)
        & (df["next_suspend"] == 0)
        & df["mom_20d"].notna()
        & df["amt_ma_20"].notna()
        & df["next_open"].gt(0)
        & df["exit_close"].gt(0)
        & df["liquidity_cut"].notna()
        & df["amt_ma_20"].ge(df["liquidity_cut"])
    )
    return df


def safe_qcut(series: pd.Series, buckets: int = 5) -> pd.Series:
    valid = series.dropna()
    if valid.nunique() < buckets:
        return pd.Series(index=series.index, dtype="float64")
    ranks = valid.rank(method="first")
    cuts = pd.qcut(ranks, buckets, labels=False) + 1
    return cuts.reindex(series.index)


def compute_daily_metrics(panel: pd.DataFrame, factor_name: str, min_cross_section: int) -> pd.DataFrame:
    eligible = panel.loc[panel["is_tradeable"]].copy()
    eligible["bucket"] = eligible.groupby("trade_date")[factor_name].transform(safe_qcut)

    daily_rows: list[dict] = []
    for trade_date, day in eligible.groupby("trade_date"):
        obs = day[[factor_name, "fwd_ret_5d_open_to_close", "bucket", "symbol"]].dropna()
        if len(obs) < min_cross_section or obs["bucket"].nunique() < 5:
            continue
        ic = obs[factor_name].corr(obs["fwd_ret_5d_open_to_close"], method="spearman")
        top = obs.loc[obs["bucket"] == 5, "fwd_ret_5d_open_to_close"].mean()
        bottom = obs.loc[obs["bucket"] == 1, "fwd_ret_5d_open_to_close"].mean()
        daily_rows.append(
            {
                "trade_date": trade_date,
                "cross_section_size": int(len(obs)),
                "ic_spearman": float(ic) if pd.notna(ic) else np.nan,
                "bucket_1_ret": float(bottom),
                "bucket_5_ret": float(top),
                "long_short_ret": float(top - bottom),
            }
        )
    return pd.DataFrame(daily_rows).sort_values("trade_date")


def summarize_results(
    panel: pd.DataFrame,
    daily_metrics: pd.DataFrame,
    config: SmokeTestConfig,
    manifest: dict,
) -> dict:
    eligible = panel.loc[panel["is_tradeable"]].copy()
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
        if len(daily_metrics) > 1 and daily_metrics["ic_spearman"].std(ddof=1) not in (0, np.nan)
        else np.nan,
        "ic_positive_ratio": float((daily_metrics["ic_spearman"] > 0).mean()) if not daily_metrics.empty else np.nan,
        "mean_long_short_ret": float(daily_metrics["long_short_ret"].mean()) if not daily_metrics.empty else np.nan,
        "long_short_positive_ratio": float((daily_metrics["long_short_ret"] > 0).mean()) if not daily_metrics.empty else np.nan,
        "mean_bucket_5_ret": float(daily_metrics["bucket_5_ret"].mean()) if not daily_metrics.empty else np.nan,
        "mean_bucket_1_ret": float(daily_metrics["bucket_1_ret"].mean()) if not daily_metrics.empty else np.nan,
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
        "forward_return_definition": (
            f"close_t+{config.holding_days} / open_t+1 - 1"
        ),
        "factor_uses_only_dates_lte_t": True,
        "forward_window_starts_next_trading_day": bool((eligible["next_open"] > 0).all()),
        "minimum_calendar_gap_trade_to_exit": int(overlap_days) if pd.notna(overlap_days) else None,
        "same_day_price_used_for_forward_return": False,
        "suspension_filter_applied_on_t_and_t_plus_1": True,
    }


def build_report(
    output_dir: Path,
    config: SmokeTestConfig,
    summary: dict,
    bias_checks: dict,
    universe_diagnostics: dict,
    base_universe: pd.DataFrame,
    daily_metrics: pd.DataFrame,
) -> None:
    top_ic = daily_metrics.copy()
    bottom_ic = daily_metrics.copy()
    for frame in (top_ic, bottom_ic):
        if not frame.empty:
            frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.strftime("%Y-%m-%d")
    top_ic = top_ic.nlargest(5, "ic_spearman")[
        ["trade_date", "cross_section_size", "ic_spearman", "long_short_ret"]
    ]
    bottom_ic = bottom_ic.nsmallest(5, "ic_spearman")[
        ["trade_date", "cross_section_size", "ic_spearman", "long_short_ret"]
    ]
    report = f"""# A股价格因子 Smoke Test 报告

## 数据口径

- 因子：{config.factor_name}，定义为过去 {config.lookback_days} 个交易日收盘动量。
- 输入清洗表：`{config.prefilter_path}`、`{config.financial_universe_path}`。
- 研究快照：`{config.snapshot_dir}`。
- 回报口径：`close_t+{config.holding_days} / open_t+1 - 1`，信号日收盘后生成，下一交易日开盘入场，持有 {config.holding_days} 个交易日后按收盘退出。

## 股票池筛选

- 仅保留清洗后 `include_price_factors=1` 且未被 `exclude_from_price_backtest` 排除的股票。
- 仅保留 `has_1d_cache=1`、`financial_status=ok` 的股票。
- 动态过滤：上市满 {config.min_listing_days} 天、信号日和下一交易日均未停牌、20 日平均成交额位于当日截面后 {int((1-config.liquidity_quantile)*100)}%。
- 本次 smoke test 基础股票池 {summary['base_universe_count']} 只，实际可交易样本 {summary['tradeable_symbol_count']} 只。

静态过滤诊断：

```json
{json.dumps(universe_diagnostics, ensure_ascii=False, indent=2)}
```

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
- 多空组合平均收益（Q5-Q1）：{summary['mean_long_short_ret']:.4%}
- 多空收益为正占比：{summary['long_short_positive_ratio']:.2%}
- Q5 平均收益：{summary['mean_bucket_5_ret']:.4%}
- Q1 平均收益：{summary['mean_bucket_1_ret']:.4%}

## 最好 / 最差 5 个截面日

### Top 5 IC

{top_ic.to_markdown(index=False)}

### Bottom 5 IC

{bottom_ic.to_markdown(index=False)}

## 偏差检查摘要

```json
{json.dumps(bias_checks, ensure_ascii=False, indent=2)}
```
"""
    (output_dir / "report.md").write_text(report, encoding="utf-8")


def main() -> None:
    args = parse_args()
    prefilter, financial_universe, daily_bar, instrument, manifest = load_inputs(
        args.prefilter_path,
        args.financial_universe_path,
        args.snapshot_dir,
    )
    base_universe = build_base_universe(prefilter, financial_universe, instrument)
    panel = prepare_panel(
        daily_bar=daily_bar,
        base_universe=base_universe,
        lookback_days=args.lookback_days,
        holding_days=args.holding_days,
        min_listing_days=args.min_listing_days,
        liquidity_quantile=args.liquidity_quantile,
    )

    config = SmokeTestConfig(
        factor_name=args.factor_name,
        lookback_days=args.lookback_days,
        holding_days=args.holding_days,
        min_listing_days=args.min_listing_days,
        min_cross_section=args.min_cross_section,
        liquidity_quantile=args.liquidity_quantile,
        prefilter_path=str(args.prefilter_path),
        financial_universe_path=str(args.financial_universe_path),
        snapshot_dir=str(args.snapshot_dir),
    )
    daily_metrics = compute_daily_metrics(panel, config.factor_name, config.min_cross_section)
    summary = summarize_results(panel, daily_metrics, config, manifest)
    bias_checks = build_bias_checks(panel, config)
    universe_diagnostics = build_universe_diagnostics(
        prefilter=prefilter,
        financial_universe=financial_universe,
        instrument=instrument,
        manifest=manifest,
        base_universe=base_universe,
    )

    output_dir = args.output_root / manifest["snapshot_id"] / config.factor_name
    output_dir.mkdir(parents=True, exist_ok=True)

    panel.loc[panel["is_tradeable"]].to_parquet(output_dir / "eligible_panel.parquet", index=False)
    daily_metrics.to_csv(output_dir / "daily_metrics.csv", index=False)
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
    (output_dir / "config.json").write_text(
        json.dumps(asdict(config), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    build_report(output_dir, config, summary, bias_checks, universe_diagnostics, base_universe, daily_metrics)

    print(json.dumps({"output_dir": str(output_dir), "summary": summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
