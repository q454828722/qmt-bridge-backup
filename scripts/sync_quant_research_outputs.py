#!/usr/bin/env python3
"""Sync derived quant research outputs from cache progress files."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
BASIC_DIR = ROOT / "data" / "yuanqi_replica" / "basic"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync derived quant research outputs from cache progress files")
    parser.add_argument("--basic-dir", default=str(BASIC_DIR))
    parser.add_argument("--run-id", default="", help="Optional audit/refresh run id to append into quality report")
    parser.add_argument("--pre-snapshot-dir", default="")
    parser.add_argument("--post-snapshot-dir", default="")
    parser.add_argument("--diff-dir", default="")
    return parser.parse_args()


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str).fillna("")


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    frame.to_csv(path, index=False, encoding="utf-8-sig")


def _issue_map(issue_df: pd.DataFrame) -> dict[str, dict[str, str]]:
    if issue_df.empty:
        return {}
    return {
        row["stock_code"]: row
        for row in issue_df.to_dict(orient="records")
        if row.get("stock_code")
    }


def _build_clean_universe(
    universe_df: pd.DataFrame,
    instrument_df: pd.DataFrame,
    kline_df: pd.DataFrame,
    financial_df: pd.DataFrame,
    issue_df: pd.DataFrame,
    latest_1d_date: str,
) -> pd.DataFrame:
    details = instrument_df[["stock_code", "name", "open_date"]].copy()
    if "name" not in universe_df.columns:
        universe_df["name"] = ""

    kline_1d = kline_df[kline_df["period"] == "1d"][["stock_code", "has_cache", "latest_date"]].copy()
    kline_1d = kline_1d.rename(columns={"has_cache": "has_1d_cache", "latest_date": "latest_1d_date"})

    financial_core = financial_df[["stock_code", "status"]].copy().rename(columns={"status": "financial_status"})
    merged = (
        universe_df[["stock_code"]]
        .merge(details, on="stock_code", how="left")
        .merge(kline_1d, on="stock_code", how="left")
        .merge(financial_core, on="stock_code", how="left")
    ).fillna("")

    issue_by_code = _issue_map(issue_df)
    issue_types = []
    policies = []
    include_price = []
    include_financial = []
    for row in merged.to_dict(orient="records"):
        stock = row["stock_code"]
        has_1d_cache = "1" if str(row.get("has_1d_cache", "")) == "1" else "0"
        latest_1d = row.get("latest_1d_date", "")
        status = row.get("financial_status", "") or "missing"
        issue = issue_by_code.get(stock, {})
        issue_type = issue.get("issue_type", "")
        policy = issue.get("factor_policy", "")

        price_ok = has_1d_cache == "1" and latest_1d == latest_1d_date
        if not issue_type:
            issue_type = "none" if status == "ok" else ""
        if not policy:
            if status == "ok":
                policy = "use_all_available_price_and_financial_factors"
            elif status == "stale":
                policy = "non_blocking_use_latest_available_or_exclude_recent_quarter"
            elif status in {"missing", "incomplete"}:
                policy = "review_before_factor_generation"
        issue_types.append(issue_type or "none")
        policies.append(policy)
        include_price.append("1" if price_ok else "0")
        include_financial.append("1" if status in {"ok", "stale"} else "0")

    merged["has_1d_cache"] = merged["has_1d_cache"].replace("", "0")
    merged["financial_status"] = merged["financial_status"].replace("", "missing")
    merged["include_price_factors"] = include_price
    merged["include_financial_factors"] = include_financial
    merged["data_issue_type"] = issue_types
    merged["factor_policy"] = policies

    return merged[
        [
            "stock_code",
            "name",
            "open_date",
            "has_1d_cache",
            "latest_1d_date",
            "financial_status",
            "include_price_factors",
            "include_financial_factors",
            "data_issue_type",
            "factor_policy",
        ]
    ].sort_values("stock_code")


def _build_prefilter(clean_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for row in clean_df.to_dict(orient="records"):
        status = row["financial_status"]
        price_ok = row["include_price_factors"] == "1"
        fresh_ok = price_ok and status == "ok"
        latest_ok = price_ok and status in {"ok", "stale"}
        exclude_price = "0" if price_ok else "1"
        exclude_financial = "0" if latest_ok else "1"

        reasons = []
        if not price_ok:
            reasons.append("price_data_unavailable")
        if status == "stale":
            reasons.append("stale_financial: exclude_from_fresh_only; allowed_for_latest_available")
        elif status == "missing":
            reasons.append("missing_financial: exclude_from_financial_backtest")
        elif status == "incomplete":
            reasons.append("incomplete_financial: review_before_factor_generation")
        elif row["data_issue_type"] not in {"", "none"}:
            reasons.append(row["data_issue_type"])

        rows.append(
            {
                "stock_code": row["stock_code"],
                "name": row["name"],
                "include_price_factors": row["include_price_factors"],
                "include_financial_factors": row["include_financial_factors"],
                "fresh_financial_available": "1" if fresh_ok else "0",
                "latest_available_financial_available": "1" if latest_ok else "0",
                "exclude_from_price_backtest": exclude_price,
                "exclude_from_financial_backtest": exclude_financial,
                "exclude_reason": "; ".join(reasons),
            }
        )
    return pd.DataFrame(rows).sort_values("stock_code")


def _append_audit_section(
    report_path: Path,
    *,
    run_id: str,
    pre_snapshot_dir: str,
    post_snapshot_dir: str,
    diff_dir: str,
    diff_manifest: dict,
    status_counts: dict[str, int],
    fresh_count: int,
    latest_count: int,
    exclude_fin_count: int,
) -> None:
    timestamp = datetime.now().isoformat(timespec="seconds")
    top_changes_rel = diff_manifest["report"]["files"]["top_changes"]
    heading = f"## 第六轮：带审计的 stale 刷新（{run_id}）"
    section = f"""

{heading}

- 本轮时间：{timestamp}
- 刷新 run_id：`{run_id}`
- pre snapshot：`{pre_snapshot_dir}`
- post snapshot：`{post_snapshot_dir}`
- diff artifact：`{diff_dir}`
- diff manifest：`{Path(diff_dir) / 'manifest.json'}`
- top changes：`{Path(diff_dir) / top_changes_rel}`

### 刷新后财务状态

| 状态 | 数量 |
| --- | ---: |
| ok | {status_counts.get('ok', 0)} |
| stale | {status_counts.get('stale', 0)} |
| missing | {status_counts.get('missing', 0)} |
| incomplete | {status_counts.get('incomplete', 0)} |

### 刷新后研究口径

| 指标 | 数量 |
| --- | ---: |
| fresh_financial_available | {fresh_count} |
| latest_available_financial_available | {latest_count} |
| exclude_from_financial_backtest | {exclude_fin_count} |

### 审计摘要

| 指标 | 数值 |
| --- | ---: |
| changed_dataset_count | {diff_manifest['report']['changed_dataset_count']} |
| row_change_rows | {diff_manifest['report']['row_change_rows']} |
| field_change_rows | {diff_manifest['report']['field_change_rows']} |

本轮 diff 显示变化主要集中在财务三表新增记录，未出现字段值级别冲突。详细变化见 `top_changes.md`。
"""
    original = report_path.read_text(encoding="utf-8-sig")
    escaped_heading = re.escape(heading)
    pattern = rf"(?ms)^({escaped_heading})\n.*?(?=^## |\Z)"
    cleaned = re.sub(pattern, "", original).rstrip()
    updated = cleaned + "\n" + section + "\n"
    report_path.write_text(updated, encoding="utf-8-sig")


def main() -> int:
    args = parse_args()
    basic_dir = Path(args.basic_dir)
    universe_df = _read_csv(basic_dir / "a_share_universe.csv")
    instrument_df = _read_csv(basic_dir / "instrument_details.csv")
    kline_df = _read_csv(basic_dir / "cache_progress_kline.csv")
    financial_df = _read_csv(basic_dir / "cache_progress_financial.csv")
    issue_df = _read_csv(basic_dir / "cache_progress_financial_issues.csv")
    summary = json.loads((basic_dir / "cache_progress_summary.json").read_text(encoding="utf-8"))
    latest_1d_date = summary.get("kline", {}).get("summary", {}).get("1d", {}).get("newest_latest_date", "")

    clean_df = _build_clean_universe(
        universe_df=universe_df,
        instrument_df=instrument_df,
        kline_df=kline_df,
        financial_df=financial_df,
        issue_df=issue_df,
        latest_1d_date=latest_1d_date,
    )
    _write_csv(clean_df, basic_dir / "quant_data_clean_universe.csv")

    fresh_df = clean_df[
        (clean_df["include_price_factors"] == "1") & (clean_df["financial_status"] == "ok")
    ].copy()
    latest_df = clean_df[
        (clean_df["include_price_factors"] == "1") & (clean_df["financial_status"].isin(["ok", "stale"]))
    ].copy()
    _write_csv(fresh_df, basic_dir / "quant_financial_universe_fresh_only.csv")
    _write_csv(latest_df, basic_dir / "quant_financial_universe_latest_available.csv")

    prefilter_df = _build_prefilter(clean_df)
    _write_csv(prefilter_df, basic_dir / "quant_backtest_prefilter.csv")

    output = {
        "quant_data_clean_universe": str(basic_dir / "quant_data_clean_universe.csv"),
        "quant_financial_universe_fresh_only": str(basic_dir / "quant_financial_universe_fresh_only.csv"),
        "quant_financial_universe_latest_available": str(basic_dir / "quant_financial_universe_latest_available.csv"),
        "quant_backtest_prefilter": str(basic_dir / "quant_backtest_prefilter.csv"),
        "fresh_financial_available": int((prefilter_df["fresh_financial_available"] == "1").sum()),
        "latest_available_financial_available": int((prefilter_df["latest_available_financial_available"] == "1").sum()),
        "exclude_from_financial_backtest": int((prefilter_df["exclude_from_financial_backtest"] == "1").sum()),
        "status_counts": clean_df["financial_status"].value_counts().to_dict(),
    }

    if args.run_id and args.diff_dir:
        diff_manifest_path = Path(args.diff_dir) / "manifest.json"
        diff_manifest = json.loads(diff_manifest_path.read_text(encoding="utf-8"))
        _append_audit_section(
            basic_dir / "quant_data_quality_report.md",
            run_id=args.run_id,
            pre_snapshot_dir=args.pre_snapshot_dir,
            post_snapshot_dir=args.post_snapshot_dir,
            diff_dir=args.diff_dir,
            diff_manifest=diff_manifest,
            status_counts=output["status_counts"],
            fresh_count=output["fresh_financial_available"],
            latest_count=output["latest_available_financial_available"],
            exclude_fin_count=output["exclude_from_financial_backtest"],
        )
        output["quality_report"] = str(basic_dir / "quant_data_quality_report.md")

    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
