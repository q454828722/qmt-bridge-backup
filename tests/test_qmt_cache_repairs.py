from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "apply_verified_qmt_cache_repairs.py"
    spec = importlib.util.spec_from_file_location("apply_verified_qmt_cache_repairs", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_resolve_open_date_uses_two_source_majority_for_conflict() -> None:
    module = _load_module()

    repair = module.resolve_open_date_repair(
        overlay_row={
            "stock_code": "600018.SH",
            "name": "上港集团",
            "gm_list_date": "20061026",
            "akshare_list_date": "20000719",
        },
        eastmoney_row={"public_open_date": "20061026"},
        current_open_date="0",
    )

    assert repair["verified_open_date"] == "20061026"
    assert repair["apply"] == "1"
    assert repair["validation_level"] == "gm_eastmoney_resolve_akshare_conflict_open_date"


def test_resolve_financial_requires_full_public_date_agreement() -> None:
    module = _load_module()
    row = {
        "stock_code": "001389.SZ",
        "validation_level": "gm_single_external_source",
        "gm_balance_latest_report": "20251231",
        "gm_income_latest_report": "20251231",
        "gm_cashflow_latest_report": "20251231",
        "gm_balance_latest_announce": "20260328",
        "gm_income_latest_announce": "20260328",
        "gm_cashflow_latest_announce": "20260328",
    }

    held = module.resolve_financial_validation(
        row,
        {"status": "success", "latest_report": "20251231", "latest_notice": "20260228"},
        manual=None,
    )
    repaired = module.resolve_financial_validation(
        row,
        {"status": "success", "latest_report": "20251231", "latest_notice": "20260228"},
        manual={
            "stock_code": "001389.SZ",
            "public_source": "sina",
            "latest_report": "20251231",
            "latest_notice": "20260328",
        },
    )

    assert held["validated"] == "0"
    assert repaired["validated"] == "1"
    assert repaired["validation_level"] == "gm_manual_public_agree_latest_financial"
