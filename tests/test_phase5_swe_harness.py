"""SWE-bench harness tests.

Exercise the glue -- scaffolds, the runner, the report-parser, and the two-scaffold
report -- with fakes injected for the model and the evaluator. Nothing here imports
``anthropic``, ``swebench``, ``datasets``, or touches Docker, so the harness's wiring
is proven offline at zero cost. The only thing these tests do *not* cover is the
real model output and the real Docker evaluation -- those are the paid run itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chorus.benchmarks.swe.evaluator import SubprocessSweEvaluator, parse_report, write_predictions
from chorus.benchmarks.swe.runner import compare_scaffolds, run_scaffold
from chorus.benchmarks.swe.scaffold import (
    SelfRepairScaffold,
    SingleShotScaffold,
    _build_user,
    extract_patch,
)
from chorus.benchmarks.swe.types import (
    EMPTY_PATCH,
    EVAL_ERROR,
    RESOLVED,
    TESTS_FAILED,
    BenchDependencyMissing,
    ModelResponse,
    SweOutcome,
    SwePrediction,
)
from chorus.core.types import TaskSpec
from chorus.report.swe_html import render_benchmark_html
from chorus.report.swe_md import render_benchmark_report


def _task(task_id: str) -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        prompt=f"fix {task_id}",
        metadata={"repo": "psf/requests", "base_commit": "abc"},
    )


class ScriptedModel:
    """Returns canned patches; counts calls so we can assert scaffold behaviour."""

    def __init__(self, text: str = "```diff\n+ fix\n```", cost: float = 0.01) -> None:
        self.text = text
        self.cost = cost
        self.calls = 0

    def complete(self, *, system, user, seed, max_tokens=4096) -> ModelResponse:
        del system, user, seed, max_tokens
        self.calls += 1
        return ModelResponse(text=self.text, input_tokens=10, output_tokens=5, cost_usd=self.cost)


class ScriptedEvaluator:
    """Resolves the instance ids in ``resolve`` on each call; rest fail."""

    def __init__(self, resolve: set[str], category: str = TESTS_FAILED) -> None:
        self.resolve = resolve
        self.category = category
        self.run_ids: list[str] = []

    def evaluate(self, predictions: list[SwePrediction], *, run_id: str) -> dict[str, SweOutcome]:
        self.run_ids.append(run_id)
        return {
            p.instance_id: SweOutcome(
                p.instance_id,
                p.instance_id in self.resolve,
                RESOLVED if p.instance_id in self.resolve else self.category,
            )
            for p in predictions
        }


def test_extract_patch_handles_fences_and_bare() -> None:
    assert extract_patch("```diff\n--- a\n+++ b\n```") == "--- a\n+++ b"
    assert extract_patch("```\n--- a\n+++ b\n```") == "--- a\n+++ b"
    assert extract_patch("--- a\n+++ b") == "--- a\n+++ b"


def test_swe_prompt_includes_target_tests() -> None:
    task = TaskSpec(
        task_id="astropy__astropy-12907",
        prompt="fix separability",
        metadata={
            "repo": "astropy/astropy",
            "base_commit": "abc",
            "fail_to_pass": ("astropy/modeling/tests/test_separable.py::test_nested",),
            "pass_to_pass": ("astropy/modeling/tests/test_separable.py::test_coord_matrix",),
        },
    )

    user = _build_user(task)

    assert "Failing tests to make pass" in user
    assert "test_nested" in user
    assert "Passing tests to keep passing" in user
    assert "test_coord_matrix" in user


def test_single_shot_makes_one_call_self_repair_makes_two() -> None:
    task = _task("t1")
    single, repair = ScriptedModel(), ScriptedModel()
    SingleShotScaffold().run(task, single, seed=0)
    SelfRepairScaffold().run(task, repair, seed=0)
    assert single.calls == 1
    assert repair.calls == 2  # the extra self-review turn is the harness-only diff


def test_self_repair_sums_cost_across_turns() -> None:
    out = SelfRepairScaffold().run(_task("t1"), ScriptedModel(cost=0.02), seed=0)
    assert out.cost_usd == pytest.approx(0.04)  # two turns at 0.02 each


def test_run_scaffold_folds_attempts_into_suite_result() -> None:
    tasks = [_task("t1"), _task("t2")]
    model = ScriptedModel()
    # t1 always resolves, t2 never does.
    evaluator = ScriptedEvaluator(resolve={"t1"})
    suite = run_scaffold(
        tasks, scaffold=SingleShotScaffold(), model=model, evaluator=evaluator, n=4, seed=0
    )

    by_id = suite.task_map()
    assert by_id["t1"].passes == 4
    assert by_id["t2"].passes == 0
    assert by_id["t2"].failure_breakdown == {TESTS_FAILED: 4}
    assert suite.n == 4
    assert suite.seed_policy == "per-attempt"
    assert len(evaluator.run_ids) == 4  # one evaluation pass per attempt
    # mean cost = one call per attempt at 0.01
    assert by_id["t1"].mean_cost_usd == pytest.approx(0.01)


def test_compare_scaffolds_detects_a_harness_improvement() -> None:
    tasks = [_task(f"t{i}") for i in range(8)]
    model = ScriptedModel()
    ref = run_scaffold(
        tasks, scaffold=SingleShotScaffold(), model=model,
        evaluator=ScriptedEvaluator(resolve={"t0", "t1"}), n=5, seed=0,
    )
    cand = run_scaffold(
        tasks, scaffold=SelfRepairScaffold(), model=model,
        evaluator=ScriptedEvaluator(resolve={f"t{i}" for i in range(8)}), n=5, seed=0,
    )
    report = compare_scaffolds(ref, cand, k=1)
    assert report.decision == "improved"
    assert report.delta_ci[0] > 0


def test_parse_report_categorises_outcomes() -> None:
    report = {
        "resolved_ids": ["a"],
        "empty_patch_ids": ["b"],
        "error_ids": ["c"],
    }
    outcomes = parse_report(report, ["a", "b", "c", "d"])
    assert outcomes["a"] == SweOutcome("a", True, RESOLVED)
    assert outcomes["b"].category == EMPTY_PATCH
    assert outcomes["c"].category == EVAL_ERROR
    assert outcomes["d"].category == TESTS_FAILED  # submitted but not in any list
    assert outcomes["d"].resolved is False


def test_write_predictions_uses_harness_schema(tmp_path) -> None:
    import json

    path = write_predictions(
        [SwePrediction("inst-1", "DIFF")], tmp_path / "p.jsonl", model_tag="chorus"
    )
    row = json.loads(path.read_text(encoding="utf-8").strip())
    assert row == {"instance_id": "inst-1", "model_name_or_path": "chorus", "model_patch": "DIFF"}


def test_swebench_preflight_explains_native_windows_import_failure(monkeypatch, tmp_path) -> None:
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "swebench":
            exc = ModuleNotFoundError("No module named 'resource'")
            exc.name = "resource"
            raise exc
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    evaluator = SubprocessSweEvaluator(run_dir=tmp_path)
    with pytest.raises(BenchDependencyMissing, match="WSL/Linux"):
        evaluator.ensure_ready()


def test_subprocess_evaluator_passes_absolute_predictions_path(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    def fake_run(cmd, *, cwd, check, timeout):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["check"] = check
        captured["timeout"] = timeout

    evaluator = SubprocessSweEvaluator(run_dir=tmp_path / "runs")
    monkeypatch.setattr(evaluator, "_ensure_swebench", lambda: None)
    monkeypatch.setattr(evaluator, "_load_report", lambda run_id: {"resolved_ids": ["inst-1"]})
    monkeypatch.setattr("chorus.benchmarks.swe.evaluator.subprocess.run", fake_run)

    outcomes = evaluator.evaluate([SwePrediction("inst-1", "DIFF")], run_id="single-shot__a0")

    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    predictions_path = cmd[cmd.index("--predictions_path") + 1]
    assert Path(predictions_path).is_absolute()
    assert Path(predictions_path).exists()
    assert captured["cwd"] == tmp_path / "runs"
    assert outcomes["inst-1"].resolved is True


def test_benchmark_report_states_the_claim() -> None:
    tasks = [_task(f"t{i}") for i in range(6)]
    model = ScriptedModel()
    ref = run_scaffold(
        tasks, scaffold=SingleShotScaffold(), model=model,
        evaluator=ScriptedEvaluator(resolve={"t0"}), n=5, seed=0,
    )
    cand = run_scaffold(
        tasks, scaffold=SelfRepairScaffold(), model=model,
        evaluator=ScriptedEvaluator(resolve={"t0", "t1", "t2"}), n=5, seed=0,
    )
    text = render_benchmark_report(ref, cand, compare_scaffolds(ref, cand, k=5), k=5)
    assert "harness-only comparison" in text
    assert "single-shot" in text and "self-repair" in text
    assert "Claim:" in text
    assert "pass^5" in text


def test_benchmark_html_report_surfaces_failures_and_tasks() -> None:
    tasks = [_task("astropy__astropy-12907")]
    model = ScriptedModel()
    ref = run_scaffold(
        tasks,
        scaffold=SingleShotScaffold(),
        model=model,
        evaluator=ScriptedEvaluator(resolve=set(), category=EMPTY_PATCH),
        n=1,
        seed=0,
    )
    cand = run_scaffold(
        tasks,
        scaffold=SelfRepairScaffold(),
        model=model,
        evaluator=ScriptedEvaluator(resolve=set(), category=EMPTY_PATCH),
        n=1,
        seed=0,
    )
    comparison = compare_scaffolds(ref, cand, k=1)

    html = render_benchmark_html(ref, cand, comparison, k=1, subset_label="subset1")

    assert "SWE-bench harness-only comparison" in html
    assert "INCONCLUSIVE" in html
    assert "empty_patch" in html
    assert "astropy__astropy-12907" in html
