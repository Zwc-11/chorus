# Flock — the self-writing workflow engine

Flock is Murmur's self-writing multi-agent layer (package `murmur.flock`). You give it
a task and a cheap model; a **planner** compiles the task into a typed **Workflow IR**,
and an **executor** interprets that plan by spawning many small, isolated subagents —
fanning out, running tournaments, and adversarially verifying — and merging the results.

It is the Claude-Code "dynamic workflows" idea rebuilt for models ~100× cheaper, so the
heavy reliability patterns (fan-out, tournaments, adversarial verification) become the
default instead of a luxury. See the full design in
[../murmur_contract_first_execution_harness_plan.md](../murmur_contract_first_execution_harness_plan.md).

## The spine: compiler → IR → interpreter

```
natural-language task ──► planner ──► WorkflowPlan (typed DAG) ──► executor ──► result + trace
```

- **Workflow IR** (`murmur.flock.ir`) — a `WorkflowPlan` is a DAG of `Node`s, each one
  operator. It is plain data, schema- and DAG-validated, not code — so there is no
  arbitrary code execution. `validate_plan` enforces: positive budget, unique ids, known
  operators, every input resolves to a node or a declared source, the graph is acyclic,
  and the **taint rule** (an `untrusted` node's output may not flow into a `trusted` one).
- **Model gateway** (`murmur.flock.gateway`) — one async `ModelPort`. Adapters
  (`murmur.flock.adapters`) cover a deterministic `FakeModel`, and an OpenAI-compatible
  adapter that serves both DeepSeek and a local Ollama model. A `BudgetLedger` caps the
  whole run (a circuit breaker) and `MeteredModel` debits it on every call.
- **Operators** (`murmur.flock.operators`) — the seven primitives:

  | op | pattern | shape in → out |
  |----|---------|----------------|
  | `classify` | classify-and-act | items → one label |
  | `map` | fan-out | N items → N results (parallel) |
  | `reduce` | synthesize (barrier) | many → one merged |
  | `tournament` | pairwise bracket | N → N ranked (winner first) |
  | `verify` | adversarial refutation | top_k → annotated (`contested`) |
  | `filter` | generate-and-filter | scored items → top_k by score |
  | `loop` | loop until done | seed → refined |

- **Executor** (`murmur.flock.scheduler`) — `execute_plan` turns each node into an
  asyncio task that awaits its upstream tasks, so independent nodes run concurrently and
  merges act as barriers. A semaphore caps parallel subagent calls (the bulkhead). It is
  **resumable** (pass an `event_log`: finished nodes are replayed for free) and enforces
  the **runtime quarantine** (`untrusted_sources` can't reach a `trusted` node).
- **Planner** (`murmur.flock.planner`) — `plan_workflow` asks a thinking model for a JSON
  plan, validates it, and re-prompts with the exact error on failure (a bounded repair
  loop). If the model is unavailable or never yields a valid plan, it falls back to a
  deterministic `template_plan`.

## Hardening (Phase 3)

- **Budget** — `BudgetLedger` caps the whole run; once tripped, the offending node is
  recorded as failed and the rest of the run stays inspectable.
- **Resume** — `murmur.flock.eventlog` appends every node start/finish/failure to an
  append-only log (in-memory or JSONL). Re-running with the same log restores finished
  nodes and only redoes outstanding work — finished work costs nothing the second time.
- **Quarantine (IFC)** — `validate_plan` statically forbids an `untrusted` node feeding a
  `trusted` one; at run time the scheduler also refuses tainted *source* data reaching a
  trusted node (`QuarantineViolation`). Taint propagates through artifacts.
- **Trace** — `murmur.flock.report.render_run_report` renders a markdown trace with a
  mermaid DAG, a per-node table (op, trust, artifacts, calls, status), cost totals, and
  the final output.

## Self-improvement (Phase 4)

Because the model is cheap, you don't write *one* workflow — `best_of_k`
(`murmur.flock.improve`) generates K candidate plans, runs them all, scores them (a
pluggable `PlanScorer`; the default rewards success, output volume, and fewer contested
picks, penalizing cost), and keeps the winner. `self_improving_plan` closes the loop: it
first checks the `TemplateLibrary` (`murmur.flock.library`) for a proven shape for this
kind of task and **reuses** it (no tournament); on a miss it **mines** a new winner and
distills it into the library. So a task type gets cheaper and better the more it is seen.

## CLI

Everything runs offline on deterministic fakes by default (no API keys), which is how the
test suite exercises it; add `--live` to use real adapters.

```bash
# Run the bundled resume-ranking plan end-to-end (offline):
murmur flock run

# Run your own plan over items from a file:
murmur flock run path/to/plan.yaml --items-file resumes.txt --source resumes

# Resume a killed run, write a markdown trace, mark the source untrusted:
murmur flock run --event-log .murmur/flock/run.jsonl --trace trace.md --untrusted

# Compile a task into a plan (offline → template; --live → DeepSeek writes it):
murmur flock plan "rank these resumes for a backend role; verify the top 10" --source resumes
murmur flock plan "..." --source resumes --live -o plan.yaml

# Self-improving: race K candidate workflows, keep + distill the winner (then reuse it):
murmur flock improve "rank backend resumes; verify the top" --source resumes --k 3
```

`murmur flock run` prints per-node results (artifacts produced, model calls made), the
token/cost totals against the budget, and the final synthesized output.

## Library

```python
import asyncio
from murmur.flock.planner import plan_workflow
from murmur.flock.scheduler import execute_plan
from murmur.flock.models import build_model, offline_resolver

async def main():
    planner = build_model("deepseek-v4-pro")            # or a FakeModel offline
    plan = await plan_workflow(
        "rank resumes for a backend role; verify the top",
        model=planner, sources=["resumes"],
    )
    report = await execute_plan(
        plan, sources={"resumes": [...]},               # list of strings / dicts
        resolver=offline_resolver(),                    # swap for default_resolver() to go live
    )
    print(report.final[0].content)

asyncio.run(main())
```

## Configuration

| Variable | Used by | Default |
|----------|---------|---------|
| `DEEPSEEK_API_KEY` | DeepSeek adapter | (required for `--live` with DeepSeek) |
| `DEEPSEEK_BASE_URL` | DeepSeek adapter | `https://api.deepseek.com` |
| `OLLAMA_BASE_URL` | Ollama adapter | `http://localhost:11434/v1` |

Model specs a plan node may name: `deepseek-v4-flash`, `deepseek-v4-pro`,
`ollama:<name>`, or `fake` / `fake:<label>`.
