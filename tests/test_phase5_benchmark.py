"""Phase 5 benchmark-adapter tests.

Cover the SWE-bench Verified task loader behind the ``load_suite`` seam: real
instance parsing (including the JSON-encoded ``FAIL_TO_PASS`` / ``PASS_TO_PASS``
test lists), deterministic subset selection, the per-suite baseline key, and the
actionable error when no data source is available. These run offline against a
local fixture -- no network, no ``datasets`` dependency.
"""

from __future__ import annotations

import json

import pytest

from chorus.benchmarks.loader import load_suite, suite_version_for
from chorus.benchmarks.swebench import (
    BenchmarkDataUnavailable,
    load_swebench_verified,
    resolve_subset_size,
    suite_version,
)


def _instance(instance_id: str, repo: str = "psf/requests") -> dict:
    return {
        "instance_id": instance_id,
        "repo": repo,
        "base_commit": "abc123",
        "problem_statement": f"Fix the bug described in {instance_id}.",
        "version": "2.0",
        "FAIL_TO_PASS": json.dumps([f"tests/test_{instance_id}.py::test_it"]),
        "PASS_TO_PASS": json.dumps(["tests/test_core.py::test_smoke"]),
    }


def _write_jsonl(tmp_path, instances):
    path = tmp_path / "swebench.jsonl"
    path.write_text("\n".join(json.dumps(i) for i in instances), encoding="utf-8")
    return path


def test_loads_instances_and_maps_acceptance_contract(tmp_path) -> None:
    path = _write_jsonl(tmp_path, [_instance("requests__requests-100")])
    tasks = load_swebench_verified(path=path, subset_size=None)

    assert len(tasks) == 1
    task = tasks[0]
    assert task.task_id == "requests__requests-100"
    assert task.expected_output is None  # acceptance is test-based, not a string match
    assert task.prompt.startswith("Fix the bug")
    assert task.metadata["repo"] == "psf/requests"
    # JSON-encoded test lists are parsed into tuples.
    assert task.metadata["fail_to_pass"] == ("tests/test_requests__requests-100.py::test_it",)
    assert task.metadata["pass_to_pass"] == ("tests/test_core.py::test_smoke",)


def test_subset_is_deterministic_first_k_by_sorted_id(tmp_path) -> None:
    ids = ["zeta-3", "alpha-1", "mid-2"]
    path = _write_jsonl(tmp_path, [_instance(i) for i in ids])

    tasks = load_swebench_verified(path=path, subset_size=2)
    assert [t.task_id for t in tasks] == ["alpha-1", "mid-2"]  # sorted, first 2


def test_full_set_when_subset_size_is_falsey(tmp_path) -> None:
    path = _write_jsonl(tmp_path, [_instance(f"x-{i}") for i in range(5)])
    assert len(load_swebench_verified(path=path, subset_size=None)) == 5
    assert len(load_swebench_verified(path=path, subset_size=0)) == 5


def test_accepts_plain_list_test_fields(tmp_path) -> None:
    inst = _instance("plain-1")
    inst["FAIL_TO_PASS"] = ["a::b"]  # already a list, not a JSON string
    path = _write_jsonl(tmp_path, [inst])
    task = load_swebench_verified(path=path)[0]
    assert task.metadata["fail_to_pass"] == ("a::b",)


def test_missing_data_source_raises_actionable_error(tmp_path) -> None:
    missing = tmp_path / "nope.jsonl"
    with pytest.raises(BenchmarkDataUnavailable) as exc:
        load_swebench_verified(path=missing)
    assert "CHORUS_SWEBENCH_PATH" in str(exc.value)


def test_load_suite_routes_to_swebench(tmp_path, monkeypatch) -> None:
    path = _write_jsonl(tmp_path, [_instance("routed-1")])
    monkeypatch.setenv("CHORUS_SWEBENCH_PATH", str(path))
    monkeypatch.setenv("CHORUS_SWEBENCH_SUBSET", "0")  # full set

    tasks = load_suite("swe-bench-verified")
    assert [t.task_id for t in tasks] == ["routed-1"]


def test_suite_version_keys_keep_baselines_separate() -> None:
    assert suite_version_for("synthetic") == "synthetic-v1"
    assert suite_version(50) == "swe-bench-verified-subset50"
    assert suite_version(None) == "swe-bench-verified-full"
    assert suite_version_for("synthetic") != suite_version_for("swe-bench-verified")


def test_resolve_subset_size_env_override(monkeypatch) -> None:
    monkeypatch.delenv("CHORUS_SWEBENCH_SUBSET", raising=False)
    assert resolve_subset_size(None) == 50  # default
    assert resolve_subset_size(10) == 10  # explicit wins
    monkeypatch.setenv("CHORUS_SWEBENCH_SUBSET", "5")
    assert resolve_subset_size(None) == 5
    monkeypatch.setenv("CHORUS_SWEBENCH_SUBSET", "0")
    assert resolve_subset_size(None) is None  # 0 -> full set
