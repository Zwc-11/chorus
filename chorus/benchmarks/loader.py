"""Benchmark suite loaders.

``load_suite(name)`` returns the task set for a named suite. The built-in
``synthetic`` suite is a fixed set of tasks with per-task ``difficulty`` metadata
so the regression gate has a real multi-task distribution to bootstrap over,
deterministically and with no model cost.

Real benchmarks are a drop-in: a ``swe-bench-verified`` / ``terminal-bench`` loader
returns ``TaskSpec``s the same way (with an acceptance contract per task), and the
same gate runs against a real model behind the ``AgentPort``.
"""

from __future__ import annotations

from chorus.core.types import TaskSpec

SUITE_VERSION = "synthetic-v1"

# A fixed task set. Difficulty is the per-task base success probability a baseline
# scaffold achieves; the gate compares scaffolds holding these tasks constant.
_SYNTHETIC_TASKS: tuple[tuple[str, float], ...] = (
    ("bench.parse_args", 0.92),
    ("bench.format_table", 0.88),
    ("bench.retry_backoff", 0.80),
    ("bench.merge_configs", 0.78),
    ("bench.paginate_api", 0.74),
    ("bench.resolve_imports", 0.70),
    ("bench.dedup_records", 0.66),
    ("bench.rate_limiter", 0.62),
    ("bench.schema_migrate", 0.58),
    ("bench.async_cancel", 0.52),
    ("bench.lock_ordering", 0.46),
    ("bench.race_condition", 0.40),
)


def synthetic_suite() -> list[TaskSpec]:
    return [
        TaskSpec(
            task_id=task_id,
            prompt="hello chorus",
            expected_output="HELLO CHORUS",
            metadata={"difficulty": difficulty},
        )
        for task_id, difficulty in _SYNTHETIC_TASKS
    ]


def load_suite(name: str = "synthetic") -> list[TaskSpec]:
    if name == "synthetic":
        return synthetic_suite()
    raise ValueError(
        f"unknown suite {name!r}; only 'synthetic' is built in. "
        "Real benchmarks (swe-bench-verified, terminal-bench) plug in here."
    )
