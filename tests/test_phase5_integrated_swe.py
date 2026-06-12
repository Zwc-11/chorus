"""Integrated SWE-bench path: AgentPort + JudgePort + the judged-suite glue.

Proves the three pieces wire through the *existing* ports and conductor (so a real
run inherits tracing/diagnosis), using fakes for the model and the evaluator -- no
anthropic, no swebench, no Docker.
"""

from __future__ import annotations

import asyncio

from chorus.adapters.agents.swe import SwePatchAgent
from chorus.adapters.storage.memory import InMemoryEventStore
from chorus.benchmarks.scaffold import run_judged_suite, run_judged_suite_batched
from chorus.benchmarks.swe.judge import SweBenchJudge
from chorus.benchmarks.swe.types import ModelResponse, SweOutcome, SwePrediction
from chorus.core.conductor import RunConductor
from chorus.core.events import EventType
from chorus.core.types import TaskSpec
from chorus.trace.mapper import events_to_traces


def _run(value):
    return asyncio.run(value)


def _task(task_id: str = "psf__requests-1") -> TaskSpec:
    return TaskSpec(task_id=task_id, prompt="fix it", metadata={"repo": "psf/requests"})


class FakePatchModel:
    model = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *, system, user, seed, max_tokens=4096) -> ModelResponse:
        del system, user, seed, max_tokens
        self.calls += 1
        return ModelResponse(text="```diff\n+ patched\n```", input_tokens=12, output_tokens=4)


class AlwaysFailJudge:
    async def judge(self, task: TaskSpec, output: str) -> str:
        del task, output
        return "fail"


class SetJudge:
    """Pass iff the task id is in ``resolve`` -- a JudgePort for the suite test."""

    def __init__(self, resolve: set[str]) -> None:
        self.resolve = resolve

    async def judge(self, task: TaskSpec, output: str) -> str:
        del output
        return "pass" if task.task_id in self.resolve else "fail"


class ScriptedEvaluator:
    def __init__(self, resolve: set[str]) -> None:
        self.resolve = resolve
        self.calls = 0

    def evaluate(self, predictions: list[SwePrediction], *, run_id: str) -> dict[str, SweOutcome]:
        del run_id
        self.calls += 1
        return {
            p.instance_id: SweOutcome(
                p.instance_id, p.instance_id in self.resolve,
                "resolved" if p.instance_id in self.resolve else "tests_failed",
            )
            for p in predictions
        }


class EchoAgent:
    async def run(self, task, gateway) -> str:
        del gateway
        return task.expected_output or "anything"


def test_conductor_uses_the_injected_judge() -> None:
    task = TaskSpec(task_id="t", prompt="p", expected_output="anything")
    # DeterministicJudge would pass (output == expected); the injected judge fails.
    conductor = RunConductor(
        agent=EchoAgent(), storage=InMemoryEventStore(), tools={}, judge=AlwaysFailJudge()
    )
    result = _run(conductor.run(task, n=1))
    assert result.trajectories[0].outcome == "fail"


def test_swe_patch_agent_emits_patch_and_records_model_calls() -> None:
    store = InMemoryEventStore()
    model = FakePatchModel()
    conductor = RunConductor(
        agent=SwePatchAgent(model, repair=False), storage=store, tools={}, judge=SetJudge(set())
    )
    result = _run(conductor.run(_task(), n=1))

    assert result.trajectories[0].output == "+ patched"  # fenced diff extracted
    assert model.calls == 1
    events = _run(store.read_events())
    assert sum(1 for e in events if e.type == EventType.MODEL_CALL) == 1


def test_self_repair_agent_makes_two_turns() -> None:
    store = InMemoryEventStore()
    model = FakePatchModel()
    conductor = RunConductor(
        agent=SwePatchAgent(model, repair=True), storage=store, tools={}, judge=SetJudge(set())
    )
    _run(conductor.run(_task(), n=1))
    assert model.calls == 2  # generate + self-review
    events = _run(store.read_events())
    assert sum(1 for e in events if e.type == EventType.MODEL_CALL) == 2


def test_swebench_judge_maps_resolved_and_caches() -> None:
    evaluator = ScriptedEvaluator(resolve={"psf__requests-1"})
    judge = SweBenchJudge(evaluator)
    task = _task("psf__requests-1")

    assert _run(judge.judge(task, "DIFF")) == "pass"
    assert _run(judge.judge(task, "DIFF")) == "pass"  # identical patch -> cached
    assert evaluator.calls == 1  # only one evaluator run despite two judge() calls
    assert _run(judge.judge(_task("other"), "DIFF")) == "fail"  # different instance -> new run
    assert evaluator.calls == 2


def test_swebench_judge_prime_makes_judge_a_lookup() -> None:
    evaluator = ScriptedEvaluator(resolve={"a"})
    judge = SweBenchJudge(evaluator)
    judge.prime([SwePrediction("a", "PA"), SwePrediction("b", "PB")], run_id="batch-0")
    assert evaluator.calls == 1  # one batch run

    assert _run(judge.judge(_task("a"), "PA")) == "pass"
    assert _run(judge.judge(_task("b"), "PB")) == "fail"
    assert evaluator.calls == 1  # both served from the primed cache, no extra runs


def test_run_judged_suite_folds_into_suite_result() -> None:
    tasks = [_task("a"), _task("b")]
    model = FakePatchModel()

    suite = _run(
        run_judged_suite(
            tasks,
            agent_factory=lambda s: SwePatchAgent(model, seed=s),
            judge=SetJudge(resolve={"a"}),
            n=3,
            seed=0,
            branch="bench",
            suite_version="swe-bench-verified-subset2",
            scaffold="single-shot",
        )
    )

    by_id = suite.task_map()
    assert by_id["a"].passes == 3
    assert by_id["b"].passes == 0
    assert suite.scaffold == "single-shot"
    assert suite.n == 3
    assert suite.suite_version == "swe-bench-verified-subset2"


def test_batched_runner_batches_judging_across_instances_and_records_traces() -> None:
    tasks = [_task("a"), _task("b"), _task("c")]
    model = FakePatchModel()
    evaluator = ScriptedEvaluator(resolve={"a", "b"})

    run = _run(
        run_judged_suite_batched(
            tasks,
            agent_factory=lambda s: SwePatchAgent(model, seed=s),
            judge=SweBenchJudge(evaluator),
            n=4,
            seed=0,
            branch="bench",
            suite_version="swe-bench-verified-subset3",
            scaffold="single-shot",
        )
    )

    by_id = run.suite.task_map()
    assert by_id["a"].passes == 4 and by_id["b"].passes == 4 and by_id["c"].passes == 0
    # One batch evaluation per attempt (4), not one per (task, attempt) (which is 12).
    assert evaluator.calls == 4
    assert run.suite.seed_policy == "per-attempt"
    # Every task's run is recorded -> a trace exists for each.
    assert set(run.events) == {"a", "b", "c"}
    assert any(e.type == EventType.MODEL_CALL for e in run.events["a"])
    assert any(e.type == EventType.VERDICT for e in run.events["a"])


def test_batched_run_events_project_to_traces() -> None:
    tasks = [_task("a")]
    model = FakePatchModel()
    run = _run(
        run_judged_suite_batched(
            tasks,
            agent_factory=lambda s: SwePatchAgent(model, repair=True, seed=s),
            judge=SweBenchJudge(ScriptedEvaluator(resolve=set())),
            n=2,
            seed=0,
            branch="bench",
            suite_version="swe-bench-verified-subset1",
            scaffold="self-repair",
        )
    )
    traces = events_to_traces(run.events["a"])
    assert len(traces) == 2  # two attempts -> two trajectories
    assert sum(t.total_tokens for t in traces) > 0  # model calls were recorded
