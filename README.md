<!--
This is the repo entrypoint for humans. It explains what Murmur is, what works
right now, and the commands needed to install, test, and run the local demo.
-->

# Murmur

Murmur is a contract and proof layer for AI-generated code changes. It turns a
coding task into an enforceable engineering contract, runs an agent through
policy-controlled tools in an isolated workspace, verifies the resulting diff
with tests and file/diff rules, and emits a PR-ready proof package.

The architecture is Python-first. The core is hexagonal: the domain owns
contracts, events, replay, metrics, and run orchestration; models, agents,
storage, tracing, judges, and reports plug in through ports.

## Quickstart: the workbench

Run the prompt-to-artifact workbench locally in a few minutes.

```bash
git clone <your-fork-url> murmur && cd murmur
python3 -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"                    # model SDKs ship by default
cp .env.example .env                                 # then add DEEPSEEK_API_KEY
murmur serve                                          # builds + serves the workbench
```

`murmur serve` prints a URL (default <http://127.0.0.1:8765/agent-map.html>). Open it,
type a natural-language goal (a website, a program, an essay, or a fix-test), tick
**Use model**, and click **Run agents**. The result panel shows the validated artifact
(website preview, program, or document), the trust score, and every gate.

Without an API key the workbench still runs in deterministic offline mode (leave
**Use model** unchecked). With `deepseek-v4-pro`, planning is model-authored and the
model designs the workflow from your task. Reasoning/thinking is on by default;
`DEEPSEEK_MIN_OUTPUT_TOKENS` and `DEEPSEEK_MAX_ESCALATION_TOKENS` tune the safety
budget that prevents reasoning-starvation, and `DEEPSEEK_THINKING=disabled` turns
thinking off.

## Current slice

It also now includes **Flock** (`murmur.flock`), the self-writing multi-agent
engine: a planner compiles a task into a typed, schema-validated workflow DAG and
an async executor runs it by fanning out cheap, isolated subagents — fan-out,
tournaments, and adversarial verification by default. Try `murmur flock run`
(offline, no keys) and see [docs/flock.md](docs/flock.md).

This repo now includes the contract-first MVP plus the earlier reliability,
trace, judgment, and CI-gate machinery from [docs/architecture.md](docs/architecture.md):

- Contract-first `fix-test` execution: reproduce a failing command, compile a
  typed YAML contract, run a policy-controlled agent, verify the diff, and write
  `contract.yaml`, `events.jsonl`, `diff.patch`, `proof.md`, `report.html`, and
  `summary.json` under `.murmur/runs/<run_id>/`.
- Contract utility commands: `murmur contract create`, `murmur contract check`,
  and `murmur run-contract`.
- Policy-controlled typed tools: `list_files`, `search`, `read_file`,
  `apply_patch`, `run_test`, `git_diff`, and `finish`; `.env`, secrets,
  destructive shell, network/dependency installs, pushes, and unknown edit paths
  are denied by default.
- Pure core domain types and ports.
- Append-only JSONL and in-memory event stores.
- Tool gateway with record and replay modes.
- Fake agent adapter for deterministic local demos.
- Stochastic flaky agent so the harness has a real distribution to measure.
- Concurrent `N`-trajectory fan-out in the run conductor.
- Distribution-aware metrics: `pass@1` with Wilson CI, projected `pass^k`,
  empirical unbiased `pass^k`, variance, cost, p50/p95 latency, and failure
  breakdown.
- Event-log-derived results: metrics, fan, divergence overlay, judgment, and
  diagnosis are projected from recorded events.
- Agreement/divergence analysis: step-index alignment, per-step agreement, first
  divergence detection, and overlay cell states (`converged`, `diverged`,
  `failed`, `inactive`).
- Cost-aware judgment cascade: deterministic Tier 0, convergence Tier 1, Tier 2
  only for unknown/minority trajectories, cached judge-call helper, escalation
  trace, and cost-ratio measurement harness.
- Diagnosis: step-boundary schema checks, deterministic-first failure taxonomy,
  trace stamping with `murmur.failure.class` / `murmur.failure.step`, and
  validation metrics for injected failures.
- Structured contract diagnostics: acceptance checks can now emit stable
  predicate IDs, evidence, and neutral repair hints while preserving the simple
  boolean `task.accepts()` path.
- Public SDK trace importers: OpenAI Agents SDK-style traces, Claude Code-style
  transcripts/hooks, Google ADK-style traces, and LangGraph event streams can be
  normalized into the same Murmur event log.
- Trajectory-fan visualizer: a terminal view and a standalone HTML/SVG report
  with reliability cards, decay curve, divergence overlay, judgment, and
  diagnosis.
- Statistical CI gate: a baseline store, a paired-delta bootstrap regression test
  (`regressed` / `improved` / `inconclusive`, seeded and deterministic), a
  per-failure-class PR comment, and a composite GitHub Action wrapping it —
  [demonstrated blocking a real regression on PR #2](https://github.com/Zwc-11/Murmur-ai-harness/pull/2).
- Benchmark seam: a `load_suite` / `Scaffold` interface with two loaders — the
  deterministic synthetic suite, and a real **SWE-bench Verified** loader that maps
  instances to `TaskSpec`s (problem statement → prompt; `FAIL_TO_PASS` /
  `PASS_TO_PASS` test contract → metadata) over a deterministic subset.
- Real SWE-bench evaluation along **two paths**, both holding the model fixed and
  varying only the scaffold (`single-shot` vs `self-repair`):
  - **Integrated** — `SwePatchAgent` implements the existing `AgentPort` and
    `SweBenchJudge` implements `JudgePort`; the conductor's judge is injectable, so
    a real run flows through the harness and inherits tracing, replay, divergence,
    and per-step diagnosis (`murmur gate --suite swe-bench-verified --real-agent`).
    Per-trajectory Docker eval — right for small/debug N.
  - **Batch** — `murmur bench` evaluates all patches in one parallel harness run for
    the headline number at scale (faster, but not traced).
  Both fold resolved/not into the same `SuiteResult` + `pass^k` machinery the gate
  uses. The wiring is complete and tested with fakes; the numbers need
  `ANTHROPIC_API_KEY` + Docker (see below).
- CLI commands to record/replay a dummy run, fan out a stochastic run, render
  trace/fan HTML artifacts, initialize a project (`murmur init`), inspect agent
  adapter capabilities (`murmur agents list`), gate a candidate against a
  baseline, and run the SWE-bench harness-only comparison.
- Tests proving replay, event-log projection, metric math, divergence detection,
  judgment gating, judge caching, failure classification, the three gate verdicts,
  seed reproducibility, structured diagnostics, external trace import, and the
  OSS adoption CLI surface.

## Launch status

Implemented and locally validated:

- `pytest -q`
- `ruff check murmur tests`
- Free synthetic reliability and regression-gate demos.
- Offline SWE-bench harness wiring with fake models/evaluators.
- Public trace importers for observational integration demos.

Not yet publicly validated:

- A paid SWE/Terminal-style benchmark result with a real frontier model and
  Docker evaluator. Murmur deliberately exits instead of printing a placeholder
  number when those dependencies are absent.

Start with [docs/quickstart.md](docs/quickstart.md). For CI wiring, see
[docs/github-action.md](docs/github-action.md).

## Environment

Use Python 3.12 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Run the checks:

```bash
pytest
ruff check murmur tests
```

Run the contract-first MVP on a failing test:

```bash
murmur fix-test --cmd "python -m pytest tests/test_checkout.py -q" --budget 0.50
```

This creates `.murmur/runs/<run_id>/contract.yaml`, `events.jsonl`,
`diff.patch`, `proof.md`, `report.html`, and `summary.json`. The command fails
closed if the failure cannot be reproduced or the final diff violates the
contract.

Run the Phase 0 record/replay demo:

```bash
murmur demo --n 3 --event-log .murmur/demo.jsonl
murmur replay --event-log .murmur/demo.jsonl
murmur replay --event-log .murmur/demo.jsonl --mutate
```

The `--mutate` replay intentionally changes the task prompt and should fail with
a replay divergence. That is the first proof that Murmur can detect when a
trajectory stops matching the recorded path.

Run the Phase 2-4 reliability fan-out:

```bash
murmur run --n 30 --success-rate 0.7 --error-rate 0.1 --seed 7
```

This fans out a flaky agent `N` times and prints the distribution. The point is
the gap between `pass@1`, projected `pass^k`, and the empirical unbiased
`pass^k` curve. A one-shot `pass@1` eval cannot see that gap; Murmur can.

```text
pass@1            0.80    Wilson95 [0.63, 0.90]
pass^k projected  0.0012  (i.i.d. k=30)
pass^k empirical  0.0000  (unbiased; 24/30 pass)
```

The run is reproducible per `--seed`. It also writes a standalone
`.murmur/fan.html` report you can open in a browser (no server, no build step):
reliability cards, the projected-vs-empirical `pass^k` decay curve, the
divergence overlay (the flaky agent shares a fixed opening plan, so the lanes
stay *converged* until the seed-driven split — divergence at step 4 — making the
overlay locate exactly where runs stop agreeing), the judgment cascade cost
panel, and the failure-diagnosis breakdown.

Render the Phase 1 trace viewer (`gen_ai.*` span waterfall + inspector) and
verify replay:

```bash
murmur trace --n 30 --seed 7 --replay
```

`--replay` re-executes every recorded trajectory through the replay gateway and
confirms each reproduces exactly, marking the spans `murmur.replay=true`.

Export the trace to LangSmith and close the MCP self-debug loop (Phase 6):

```bash
pip install -e ".[otel]"
export LANGSMITH_API_KEY=ls-...
murmur trace --n 12 --seed 7 --otlp --backend langsmith --project murmur
```

The same `gen_ai.*` spans the local viewer renders are exported over OTLP to
LangSmith (content capture stays off by default). The repo ships a
[`.mcp.json`](.mcp.json) wiring the official LangSmith MCP server, so a coding agent
can pull the run's trace back and debug Murmur from it — the "write → trace → debug"
loop closed on Murmur itself. Full runbook:
[docs/LANGSMITH_MCP_LOOP.md](docs/LANGSMITH_MCP_LOOP.md). The exporter, CLI, and
`.mcp.json` are in the repo and tested; the live export + MCP debugging need a
LangSmith account (documented, never faked).

Gate CI on a *statistical* regression (Phase 5):

```bash
murmur gate --branch main --n 20 --update-baseline                 # records the baseline
murmur gate --branch main --n 20 --scaffold worse --success-delta -0.12   # a candidate
```

The gate runs a task suite, compares the candidate against the stored baseline on
the same tasks/N/seed, and bootstraps a 95% CI on the per-task `pass^k` delta. It
emits one of three verdicts and exits non-zero **only** on `regressed`:

```text
## Murmur reliability gate — REGRESSED ❌
pass^5: 0.21 -> 0.07   (Δ -0.14, 95% CI [-0.21, -0.07])   <- below 0
New failures by class (candidate vs baseline):
  +16  contract_violation
  +15  tool_error
```

`improved` (CI entirely above 0) and `inconclusive` (CI straddles 0 — "widen N")
do not block. Blocking only on a statistically real regression — never on a raw
dip — is what keeps the gate from crying wolf and getting disabled. The bootstrap
is seeded, so the verdict is stable. The composite GitHub Action in
[murmur/ci/action.yml](murmur/ci/action.yml) wraps this command, posts the report
as a PR comment, and sets the check status.

Load the real SWE-bench Verified task set behind the same seam:

```bash
# from a local dump of princeton-nlp/SWE-bench_Verified, or `pip install datasets`
MURMUR_SWEBENCH_PATH=swebench_verified.jsonl \
  murmur gate --suite swe-bench-verified --n 5
```

The loader maps each instance to a `TaskSpec` (problem statement → prompt, the
`FAIL_TO_PASS` / `PASS_TO_PASS` tests → an acceptance contract in metadata) over a
deterministic subset. The gate deliberately **refuses** to run these through the
built-in stochastic scaffold — that would emit a `pass^k` that *looks* like a
benchmark result and isn't. The old `murmur bench` patch-only path is deprecated;
use `murmur fix-test` or `murmur run-contract` for public proof runs.

Legacy SWE-bench harness-only comparison (internal evaluator seam):

```bash
# Deprecated public path; retained only for internal/legacy evaluator work.
pip install -e '.[bench]'          # anthropic + datasets + swebench (needs Docker)
export ANTHROPIC_API_KEY=sk-ant-…  # one model, held fixed across scaffolds
murmur bench --subset 100 --n 10 --k 5 \
  --scaffold-a single-shot --scaffold-b self-repair
```

This holds one model fixed and varies **only the scaffold**: scaffold A is a
single model call, scaffold B adds one self-review/repair turn — the only
difference, so the `pass^k` delta is attributable to the harness. Each attempt's
patch is evaluated by the official SWE-bench Docker harness; resolved/not folds
into the same `SuiteResult` + `pass^k` + paired-delta machinery the gate uses, and
the report states the claim from measured numbers:

```text
scaffold A  single-shot   pass@1 0.31  Wilson95 [0.23, 0.41]  pass^5 0.18
scaffold B  self-repair   pass@1 0.39  Wilson95 [0.30, 0.49]  pass^5 0.27
verdict     IMPROVED  (Δpass^5 +0.09, 95% CI [+0.02, +0.16])
```

Or run the **integrated** path, which routes a real SWE-bench run through the
conductor so it inherits tracing, replay, and per-step diagnosis (per-trajectory
Docker eval; use a small N):

```bash
murmur gate --suite swe-bench-verified --real-agent --scaffold self-repair --n 5
```

*(Illustrative layout — the figures above are not a measured result.)* The harness
**refuses to print a number unless a real model and Docker evaluation actually
ran**; without them it exits with an actionable error rather than a placeholder.

> **The harness is built; the measured number is the one remaining paid step.**
> The wiring — model adapter (prompt-cached), the two scaffolds, the SWE-bench
> evaluator, the runner, and the report — is complete and unit-tested with fakes
> (no API, no Docker). What's left is *running* it: a frontier model plus the
> SWE-bench Docker harness over a subset, which costs real money and compute (on
> the order of $1–2k for a defensible 100-instance subset). Until that run
> happens, the résumé line stops at "gates CI on statistical regression" — the one
> locked rule is **the number is real or absent**.

## GitHub

This checkout is configured for:

```bash
murmur https://github.com/Zwc-11/Murmur-ai-harness.git
```
