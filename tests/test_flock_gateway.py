"""Model gateway: the fake adapter, budget ledger, metering, and model routing."""

from __future__ import annotations

import asyncio

import pytest

from murmur.flock.adapters.fake import FakeModel
from murmur.flock.adapters.openai_compat import OpenAICompatModel, deepseek_model, ollama_model
from murmur.flock.gateway import (
    BudgetExceeded,
    BudgetLedger,
    CallLog,
    MeteredModel,
    ModelPort,
    ModelUnavailable,
    estimate_tokens,
)
from murmur.flock.models import build_model, offline_resolver


def test_fake_model_is_deterministic() -> None:
    model = FakeModel()
    a = asyncio.run(model.complete(system="s", user="rank this resume"))
    b = asyncio.run(model.complete(system="s", user="rank this resume"))
    assert a.text == b.text
    assert a.input_tokens > 0 and a.output_tokens > 0


def test_fake_model_scripted_reply() -> None:
    model = FakeModel(scripted={"candidate-A": "A wins"})
    reply = asyncio.run(model.complete(system="judge", user="who is stronger: candidate-A?"))
    assert reply.text == "A wins"


def test_fake_model_satisfies_port() -> None:
    assert isinstance(FakeModel(), ModelPort)


def test_budget_ledger_admits_then_trips() -> None:
    ledger = BudgetLedger(budget_tokens=100)
    ledger.reserve(40)
    ledger.debit(input_tokens=30, output_tokens=10, cost_usd=0.01)
    assert ledger.spent_tokens == 40
    assert ledger.remaining_tokens == 60
    with pytest.raises(BudgetExceeded):
        ledger.reserve(1000)


def test_budget_ledger_rejects_nonpositive() -> None:
    with pytest.raises(ValueError, match="must be > 0"):
        BudgetLedger(budget_tokens=0)


def test_metered_model_debits_and_logs() -> None:
    ledger = BudgetLedger(budget_tokens=10_000)
    log = CallLog()
    metered = MeteredModel(FakeModel(cost_per_call=0.002), ledger=ledger, on_call=log.record)
    node_model = metered.for_node("score")
    asyncio.run(node_model.complete(system="rubric", user="resume #1"))
    asyncio.run(node_model.complete(system="rubric", user="resume #2"))
    assert ledger.calls == 2
    assert ledger.spent_tokens > 0
    assert len(log.calls) == 2
    assert {c.node_id for c in log.calls} == {"score"}
    assert log.total_cost_usd == pytest.approx(0.004)


def test_metered_model_halts_run_at_cap() -> None:
    ledger = BudgetLedger(budget_tokens=3)  # tiny: the first reserve trips it
    metered = MeteredModel(FakeModel(), ledger=ledger)
    with pytest.raises(BudgetExceeded):
        asyncio.run(metered.complete(system="a long system prompt", user="and a long user prompt"))


def test_metered_model_satisfies_port() -> None:
    assert isinstance(MeteredModel(FakeModel()), ModelPort)


def test_estimate_tokens_is_positive() -> None:
    assert estimate_tokens("") == 1
    assert estimate_tokens("a" * 40) == 10


def test_build_model_routes_specs() -> None:
    assert isinstance(build_model("fake"), FakeModel)
    assert isinstance(build_model("fake:scorer"), FakeModel)
    assert isinstance(build_model("deepseek-v4-flash"), OpenAICompatModel)
    assert isinstance(build_model("ollama:qwen2.5"), OpenAICompatModel)


def test_build_model_rejects_unknown_spec() -> None:
    with pytest.raises(ModelUnavailable, match="unknown model spec"):
        build_model("gpt-4o")


def test_offline_resolver_keeps_spec_as_name() -> None:
    resolver = offline_resolver()
    model = resolver("deepseek-v4-pro")
    assert isinstance(model, FakeModel)
    assert model.name == "deepseek-v4-pro"


def test_deepseek_adapter_errors_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    model = deepseek_model(model="deepseek-v4-pro")
    with pytest.raises(ModelUnavailable, match="DEEPSEEK_API_KEY"):
        asyncio.run(model.complete(system="s", user="u"))


def test_ollama_adapter_builds() -> None:
    model = ollama_model(model="qwen2.5")
    assert model.name == "ollama:qwen2.5"
