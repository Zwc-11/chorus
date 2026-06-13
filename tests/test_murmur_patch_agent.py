"""MurmurPatchAgent: scripted-model repair of a real failing test.

A FakeModel plays the LLM; everything else is real — worktree sandbox, policy
engine, tool proxy, pytest subprocess. This is the milestone-4 vertical slice:
reproduce -> localize -> read -> patch -> apply -> test -> finish.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from murmur.adapters.agents.contract_lite import build_contract_agent
from murmur.adapters.agents.murmur_patch import (
    MurmurPatchAgent,
    _extract_patch,
    _extract_paths,
    port_for_provider,
)
from murmur.adapters.models.fake import FakeModel
from murmur.adapters.sandboxes.local_worktree import LocalWorktreeSandbox
from murmur.adapters.tools.contract_proxy import ContractToolProxy
from murmur.application.contract_compiler import compile_fix_test_contract
from murmur.application.event_log import JsonlRunEventLog
from murmur.domain.policy import BudgetState, PolicyEngine

COMMAND = "python -m pytest tests/test_checkout.py -q"

GOOD_PATCH = """\
--- a/checkout.py
+++ b/checkout.py
@@ -1,2 +1,2 @@
 def apply_discount(price, discount):
-    return price - discount
+    return price * (1 - discount)
"""

BROKEN_PATCH = """\
--- a/checkout.py
+++ b/checkout.py
@@ -1,2 +1,2 @@
 def apply_discount(price, discount):
-    THIS LINE DOES NOT EXIST
+    return price * (1 - discount)
"""


def _write_repo(root: Path) -> None:
    (root / "tests").mkdir(parents=True)
    (root / "checkout.py").write_text(
        "def apply_discount(price, discount):\n    return price - discount\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_checkout.py").write_text(
        "from checkout import apply_discount\n\n\n"
        "def test_discount():\n    assert apply_discount(100, 0.1) == 90\n",
        encoding="utf-8",
    )


def _harness(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_repo(repo)
    contract = compile_fix_test_contract(
        command=COMMAND,
        repo_root=repo,
        failure_output='File "checkout.py", line 2, in apply_discount',
        budget_usd=0.50,
    )
    sandbox = LocalWorktreeSandbox.create(repo, tmp_path / "run")
    budget = BudgetState()
    events = JsonlRunEventLog(tmp_path / "events.jsonl", run_id="test_run")
    proxy = ContractToolProxy(
        sandbox=sandbox,
        policy=PolicyEngine(contract, budget),
        budget=budget,
        events=events,
    )
    return contract, sandbox, proxy, budget


def test_agent_fixes_failing_test_end_to_end(tmp_path: Path) -> None:
    contract, sandbox, proxy, budget = _harness(tmp_path)
    port = FakeModel(responses=['["checkout.py"]', GOOD_PATCH], cost_per_call_usd=0.01)
    agent = MurmurPatchAgent(model_port=port, model="fake-model")

    summary = agent.run(contract=contract, tools=proxy)

    assert "target test passed" in summary
    assert proxy.finished
    assert sandbox.run(COMMAND, parser="pytest").passed
    assert "1 - discount" in sandbox.git_diff()

    # Exactly two narrow model calls: localize, then patch.
    assert budget.model_calls == 2
    assert budget.cost_usd == pytest.approx(0.02)
    localize, propose = port.calls
    assert "test_checkout" in localize.messages[1]["content"]  # failure output present
    assert "checkout.py" in propose.messages[1]["content"]
    assert "return price - discount" in propose.messages[1]["content"]  # file was read


def test_agent_retries_after_apply_failure(tmp_path: Path) -> None:
    contract, sandbox, proxy, budget = _harness(tmp_path)
    port = FakeModel(responses=['["checkout.py"]', BROKEN_PATCH, GOOD_PATCH])
    agent = MurmurPatchAgent(model_port=port, model="fake-model")

    summary = agent.run(contract=contract, tools=proxy)

    assert "target test passed" in summary
    assert budget.model_calls == 3
    retry = port.calls[2].messages[1]["content"]
    assert "Previous attempt failed" in retry  # apply error fed back to the model


def test_agent_gives_up_after_max_patch_rounds(tmp_path: Path) -> None:
    contract, sandbox, proxy, _ = _harness(tmp_path)
    port = FakeModel(responses=['["checkout.py"]', "no diff here", "still no diff"])
    agent = MurmurPatchAgent(model_port=port, model="fake-model", max_patch_rounds=2)

    summary = agent.run(contract=contract, tools=proxy)

    assert summary == "no applicable patch produced"
    assert proxy.finished
    assert not sandbox.run(COMMAND, parser="pytest").passed  # untouched repo still fails


def test_localization_tolerates_prose_replies(tmp_path: Path) -> None:
    contract, _, proxy, _ = _harness(tmp_path)
    port = FakeModel(
        responses=["The bug is probably in checkout.py given the traceback.", GOOD_PATCH]
    )
    agent = MurmurPatchAgent(model_port=port, model="fake-model")

    summary = agent.run(contract=contract, tools=proxy)

    assert "target test passed" in summary


def test_factory_builds_murmur_agent_with_fake_port() -> None:
    agent = build_contract_agent(agent="murmur", provider="fake")
    assert isinstance(agent, MurmurPatchAgent)
    assert agent.model == "fake-model"


def test_factory_rejects_unknown_agents() -> None:
    with pytest.raises(KeyError, match="murmur"):
        build_contract_agent(agent="nope")


def test_port_for_provider_requires_model_for_ollama() -> None:
    with pytest.raises(KeyError, match="ollama"):
        port_for_provider("ollama")


def test_extract_patch_handles_fences_and_prose() -> None:
    fenced = f"Here is the fix:\n```diff\n{GOOD_PATCH}```\nGood luck!"
    assert _extract_patch(fenced) == GOOD_PATCH
    assert _extract_patch(f"Sure thing.\n{GOOD_PATCH}") == GOOD_PATCH
    assert _extract_patch("I cannot fix this.") == ""


def test_extract_paths_prefers_json_and_filters_unknown() -> None:
    known = ["checkout.py", "tests/test_checkout.py"]
    assert _extract_paths(json.dumps(["checkout.py", "ghost.py"]), known=known) == [
        "checkout.py"
    ]
    assert _extract_paths('```json\n["tests/test_checkout.py"]\n```', known=known) == [
        "tests/test_checkout.py"
    ]
