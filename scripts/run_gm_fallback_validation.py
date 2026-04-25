#!/usr/bin/env python3
"""Use GM Windows SDK as a read-only backup source for failed cache items."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUN_DIR = ROOT / "research/output/data_cleaning/20260424_204851_qmt_financial_refresh_supervision"
TABLES = ("Balance", "Income", "CashFlow")

sys.path.insert(0, str(ROOT))

from research.lib import GmResearchSource  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run GM fallback validation for failed cache candidates.")
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--limit-per-category", type=int, default=0, help="0 means all candidates.")
    parser.add_argument("--start-date", default="20190101")
    parser.add_argument("--end-date", default=datetime.now().strftime("%Y%m%d"))
    parser.add_argument("--windows-python", default="")
    return parser.parse_args()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")


def _limit(frame: pd.DataFrame, limit: int) -> pd.DataFrame:
    if limit and limit > 0:
        return frame.head(limit).copy()
    return frame.copy()


def _stock_records(frame: pd.DataFrame, category: str) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    records = frame.to_dict(orient="records")
    for record in records:
        record["category"] = category
    return records


def _financial_table_summary(dataset) -> dict[str, str]:
    summary: dict[str, str] = {}
    for table in TABLES:
        frame = dataset.tables.get(table, pd.DataFrame())
        prefix = table.lower()
        summary[f"gm_{prefix}_records"] = str(len(frame))
        summary[f"gm_{prefix}_latest_report"] = ""
        summary[f"gm_{prefix}_latest_announce"] = ""
        if frame.empty:
            continue
        if "report_date" in frame.columns:
            values = frame["report_date"].fillna("").astype(str)
            summary[f"gm_{prefix}_latest_report"] = values[values != ""].max() if (values != "").any() else ""
        if "announce_date" in frame.columns:
            values = frame["announce_date"].fillna("").astype(str)
            summary[f"gm_{prefix}_latest_announce"] = values[values != ""].max() if (values != "").any() else ""
    return summary


def _build_financial_suggestion(record: dict[str, Any], gm_summary: dict[str, str]) -> str:
    improved_tables = []
    for table in TABLES:
        qmt_value = int(float(record.get(f"{table}_records") or 0))
        gm_value = int(float(gm_summary.get(f"gm_{table.lower()}_records") or 0))
        if gm_value > qmt_value:
            improved_tables.append(table)
    if len(improved_tables) == len(TABLES):
        return "gm_financial_reference_available_for_all_tables"
    if improved_tables:
        return "gm_financial_reference_available_for_partial_tables"
    return "gm_financial_reference_not_better_than_qmt"


def _build_report(
    *,
    output_dir: Path,
    run_dir: Path,
    source_status: pd.DataFrame,
    observations: pd.DataFrame,
    suggestions: pd.DataFrame,
    overlay: pd.DataFrame,
    manifest: dict[str, Any],
) -> None:
    status_counts = (
        source_status.groupby(["source", "status"]).size().reset_index(name="count")
        if not source_status.empty
        else pd.DataFrame(columns=["source", "status", "count"])
    )
    category_counts = (
        suggestions.groupby(["category", "recommendation"]).size().reset_index(name="count")
        if not suggestions.empty
        else pd.DataFrame(columns=["category", "recommendation", "count"])
    )
    overlay_counts = (
        overlay.groupby(["category", "validation_level"]).size().reset_index(name="count")
        if not overlay.empty and "validation_level" in overlay.columns
        else pd.DataFrame(columns=["category", "validation_level", "count"])
    )

    def md_table(frame: pd.DataFrame) -> str:
        if frame.empty:
            return "_none_"
        columns = list(frame.columns)
        lines = [
            "| " + " | ".join(columns) + " |",
            "| " + " | ".join(["---"] * len(columns)) + " |",
        ]
        for row in frame.fillna("").astype(str).to_dict(orient="records"):
            lines.append("| " + " | ".join(row[column] for column in columns) + " |")
        return "\n".join(lines)

    report = f"""# GM 备用源补齐验证报告

- run_dir: `{run_dir}`
- output_dir: `{output_dir}`
- generated_at: `{manifest["generated_at"]}`
- mode: 只读备用源证据包；不覆盖 QMT 原始缓存；不打印 token

## Source Status

{md_table(status_counts)}

## Repair Suggestions

{md_table(category_counts)}

## Cross Source Overlay

{md_table(overlay_counts)}

## Files

- observations: `{output_dir / "summary/gm_source_observations.csv"}`
- suggestions: `{output_dir / "summary/gm_repair_suggestions.csv"}`
- cross-source overlay: `{output_dir / "summary/gm_cross_source_repair_overlay.csv"}`
- source status: `{output_dir / "summary/source_status.csv"}`
- manifest: `{output_dir / "manifest.json"}`
"""
    (output_dir / "evidence_report.md").write_text(report, encoding="utf-8")


def _normalize_compact_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace("-", "").replace("/", "").replace(" ", "")
    return text[:8]


def _load_prior_success_sources(run_dir: Path) -> dict[tuple[str, str], str]:
    source_map: dict[tuple[str, str], set[str]] = {}
    evidence_root = run_dir / "external_evidence"
    for path in evidence_root.glob("*/summary/cross_source_comparison*.csv"):
        if path.name.endswith(".pre_refine"):
            continue
        frame = _read_csv(path)
        if frame.empty or "stock_code" not in frame.columns:
            continue
        for row in frame.to_dict(orient="records"):
            key = (row.get("stock_code", ""), row.get("category", ""))
            if not key[0] or not key[1]:
                continue
            sources = {
                source.strip()
                for source in str(row.get("success_sources", "")).split(",")
                if source.strip()
            }
            if not sources:
                continue
            source_map.setdefault(key, set()).update(sources)
    return {key: ",".join(sorted(value)) for key, value in source_map.items()}


def _load_akshare_open_dates(run_dir: Path) -> dict[str, str]:
    candidates = sorted((run_dir / "external_evidence").glob("*/akshare/stock_info_sh_name_code_main_a.csv"))
    if not candidates:
        return {}
    frame = _read_csv(candidates[-1])
    if frame.empty or len(frame.columns) < 2:
        return {}
    code_col = frame.columns[0]
    date_col = frame.columns[-1]
    result = {}
    for row in frame[[code_col, date_col]].to_dict(orient="records"):
        code = str(row.get(code_col, "")).strip()
        date = _normalize_compact_date(row.get(date_col, ""))
        if code and date:
            result[f"{code}.SH"] = date
    return result


def _build_cross_source_overlay(suggestions: pd.DataFrame, run_dir: Path) -> pd.DataFrame:
    if suggestions.empty:
        return suggestions.copy()
    prior_sources = _load_prior_success_sources(run_dir)
    akshare_open_dates = _load_akshare_open_dates(run_dir)
    rows = []
    for row in suggestions.to_dict(orient="records"):
        stock_code = row.get("stock_code", "")
        category = row.get("category", "")
        prior = prior_sources.get((stock_code, category), "")
        akshare_list_date = akshare_open_dates.get(stock_code, "")
        validation_level = "single_external_source"
        if category == "financial_incomplete_after_qmt_refresh":
            validation_level = "gm_plus_prior_external_sources" if prior else "gm_single_external_source"
        elif category == "instrument_open_date_invalid":
            gm_list_date = _normalize_compact_date(row.get("gm_list_date", ""))
            if akshare_list_date and gm_list_date == akshare_list_date:
                validation_level = "gm_akshare_agree_open_date"
            elif akshare_list_date and gm_list_date and gm_list_date != akshare_list_date:
                validation_level = "gm_akshare_conflict_open_date"
            elif gm_list_date:
                validation_level = "gm_single_external_source"
            else:
                validation_level = "open_date_unresolved"
        elif category == "industry_mapping_missing":
            validation_level = "gm_permission_unavailable"
        rows.append(
            {
                **row,
                "prior_success_sources": prior,
                "akshare_list_date": akshare_list_date,
                "validation_level": validation_level,
            }
        )
    return pd.DataFrame(rows)


def main() -> int:
    args = parse_args()
    run_dir = args.run_dir.resolve()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_root = args.output_root or run_dir / "external_evidence"
    output_dir = output_root / f"{stamp}_gm_fallback_validation"
    summary_dir = output_dir / "summary"
    raw_dir = output_dir / "gm"
    summary_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    financial = _limit(
        _read_csv(run_dir / "remaining_financial_blocking_candidates.csv"),
        args.limit_per_category,
    )
    open_date = _limit(
        _read_csv(run_dir / "remaining_open_date_candidates.csv"),
        args.limit_per_category,
    )
    industry = _limit(
        _read_csv(run_dir / "remaining_industry_mapping_candidates.csv"),
        args.limit_per_category,
    )

    gm = GmResearchSource(windows_python=args.windows_python or None)
    source_status_rows: list[dict[str, Any]] = [
        {
            "source": "gm",
            "status": "initialized",
            "query_time": _utc_now(),
            "endpoint": "gm.api via windows subprocess",
            "message": "gm source initialized; token not printed",
        }
    ]
    observation_rows: list[dict[str, Any]] = []
    suggestion_rows: list[dict[str, Any]] = []

    financial_raw_frames = {table: [] for table in TABLES}
    for index, record in enumerate(_stock_records(financial, "financial_incomplete_after_qmt_refresh"), start=1):
        stock_code = record["stock_code"]
        try:
            dataset = gm.fetch_financials(
                [stock_code],
                tables=TABLES,
                start_date=args.start_date,
                end_date=args.end_date,
            )
            for table, frame in dataset.tables.items():
                if not frame.empty:
                    financial_raw_frames[table].append(frame.assign(category=record["category"]))
            gm_summary = _financial_table_summary(dataset)
            recommendation = _build_financial_suggestion(record, gm_summary)
            status = "success" if any(int(gm_summary.get(f"gm_{table.lower()}_records") or 0) for table in TABLES) else "empty"
            message = recommendation
        except Exception as exc:
            gm_summary = {}
            recommendation = "gm_financial_query_failed"
            status = "failed"
            message = f"{type(exc).__name__}: {str(exc)[:500]}"

        row = {
            "sample_order": index,
            "stock_code": stock_code,
            "name": record.get("name", ""),
            "category": record["category"],
            "source": "gm",
            "query_time": _utc_now(),
            "status": status,
            "qmt_balance_records": record.get("Balance_records", ""),
            "qmt_income_records": record.get("Income_records", ""),
            "qmt_cashflow_records": record.get("CashFlow_records", ""),
            "qmt_latest_announce": record.get("CashFlow_latest_announce", ""),
            "message": message,
            **gm_summary,
        }
        observation_rows.append(row)
        suggestion_rows.append({**row, "recommendation": recommendation})
        source_status_rows.append(
            {
                "source": "gm",
                "status": status,
                "query_time": row["query_time"],
                "endpoint": "stk_get_fundamentals_*",
                "stock_code": stock_code,
                "message": message,
            }
        )

    for table, frames in financial_raw_frames.items():
        if frames:
            _write_csv(pd.concat(frames, ignore_index=True), raw_dir / f"financial_{table}.csv")

    if not open_date.empty:
        symbols = open_date["stock_code"].dropna().astype(str).tolist()
        try:
            dataset = gm.fetch_instrument_basics(symbols)
            _write_csv(dataset.data, raw_dir / "instrument_basics.csv")
            by_symbol = {
                row["symbol"]: row
                for row in dataset.data.to_dict(orient="records")
                if row.get("symbol")
            }
            source_status_rows.append(
                {
                    "source": "gm",
                    "status": "success",
                    "query_time": _utc_now(),
                    "endpoint": "get_instruments",
                    "rows": len(dataset.data),
                    "message": "instrument basics fetched",
                }
            )
        except Exception as exc:
            by_symbol = {}
            source_status_rows.append(
                {
                    "source": "gm",
                    "status": "failed",
                    "query_time": _utc_now(),
                    "endpoint": "get_instruments",
                    "message": f"{type(exc).__name__}: {str(exc)[:500]}",
                }
            )
        for index, record in enumerate(_stock_records(open_date, "instrument_open_date_invalid"), start=1):
            stock_code = record["stock_code"]
            gm_row = by_symbol.get(stock_code, {})
            gm_list_date = str(gm_row.get("list_date", ""))
            recommendation = (
                "gm_open_date_candidate"
                if gm_list_date and gm_list_date != "0"
                else "gm_open_date_unavailable"
            )
            row = {
                "sample_order": index,
                "stock_code": stock_code,
                "name": record.get("name", gm_row.get("name", "")),
                "category": record["category"],
                "source": "gm",
                "query_time": _utc_now(),
                "status": "success" if gm_row else "empty",
                "qmt_open_date": record.get("open_date", ""),
                "gm_name": gm_row.get("name", ""),
                "gm_list_date": gm_list_date,
                "gm_exchange": gm_row.get("exchange", ""),
                "message": recommendation,
            }
            observation_rows.append(row)
            suggestion_rows.append({**row, "recommendation": recommendation})

    for index, record in enumerate(_stock_records(industry, "industry_mapping_missing"), start=1):
        row = {
            "sample_order": index,
            "stock_code": record["stock_code"],
            "name": record.get("name", ""),
            "category": record["category"],
            "source": "gm",
            "query_time": _utc_now(),
            "status": "not_available",
            "message": "current GM token has no stk_get_symbol_industry permission; keep akshare/mx-data evidence path",
            "recommendation": "gm_industry_permission_unavailable",
        }
        observation_rows.append(row)
        suggestion_rows.append(row)

    observations = pd.DataFrame(observation_rows)
    suggestions = pd.DataFrame(suggestion_rows)
    source_status = pd.DataFrame(source_status_rows)
    overlay = _build_cross_source_overlay(suggestions, run_dir)

    _write_csv(observations, summary_dir / "gm_source_observations.csv")
    _write_csv(suggestions, summary_dir / "gm_repair_suggestions.csv")
    _write_csv(overlay, summary_dir / "gm_cross_source_repair_overlay.csv")
    _write_csv(source_status, summary_dir / "source_status.csv")

    manifest = {
        "run_id": output_dir.name,
        "generated_at": _utc_now(),
        "run_dir": str(run_dir),
        "output_dir": str(output_dir),
        "limits": {"limit_per_category": args.limit_per_category},
        "inputs": {
            "financial_rows": int(len(financial)),
            "open_date_rows": int(len(open_date)),
            "industry_rows": int(len(industry)),
        },
        "outputs": {
            "observations": str(summary_dir / "gm_source_observations.csv"),
            "suggestions": str(summary_dir / "gm_repair_suggestions.csv"),
            "cross_source_overlay": str(summary_dir / "gm_cross_source_repair_overlay.csv"),
            "source_status": str(summary_dir / "source_status.csv"),
        },
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    _build_report(
        output_dir=output_dir,
        run_dir=run_dir,
        source_status=source_status,
        observations=observations,
        suggestions=suggestions,
        overlay=overlay,
        manifest=manifest,
    )

    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
