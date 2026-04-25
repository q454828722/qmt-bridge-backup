from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

pd = pytest.importorskip("pandas")

from research.lib.research_client import GmResearchSource, ResearchClient, SourceName  # noqa: E402


def test_gm_source_normalizes_instrument_symbols(monkeypatch):
    def fake_run(cmd, *, input, text, capture_output, timeout, check):
        request = json.loads(input)
        assert request["action"] == "instrument"
        assert request["symbols"] == ["SHSE.600000"]
        payload = {
            "ok": True,
            "version": "3.0.test",
            "records": [
                {
                    "symbol": "SHSE.600000",
                    "sec_name": "浦发银行",
                    "exchange": "SHSE",
                    "listed_date": "1999-11-10T00:00:00+08:00",
                    "delisted_date": "2038-01-01T00:00:00+08:00",
                }
            ],
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("research.lib.research_client.subprocess.run", fake_run)

    dataset = GmResearchSource(windows_python="python.exe").fetch_instrument_basics(["600000.SH"])

    assert dataset.source == SourceName.GM
    assert dataset.coverage_symbols == ("600000.SH",)
    assert dataset.data.loc[0, "name"] == "浦发银行"
    assert dataset.data.loc[0, "list_date"] == "19991110"


def test_research_client_policy_includes_gm_as_explicit_fallback(monkeypatch):
    monkeypatch.setattr("research.lib.research_client.QMTResearchSource", lambda: object())
    client = ResearchClient(qmt=object(), tushare=object(), akshare=object(), gm=object())

    daily_policy = client.policies[next(domain for domain in client.policies if domain.value == "daily_bar")]
    instrument_policy = client.policies[next(domain for domain in client.policies if domain.value == "instrument")]
    financial_policy = client.policies[next(domain for domain in client.policies if domain.value == "financial")]

    assert SourceName.GM in daily_policy.fallbacks
    assert SourceName.GM in instrument_policy.fallbacks
    assert financial_policy.fallbacks == (SourceName.GM,)
