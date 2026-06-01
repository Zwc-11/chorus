<!--
This is the repo entrypoint for humans. It explains what Chorus is, what works
right now, and the commands needed to install, test, and run the local demo.
-->

# Chorus

Chorus is an open-source reliability and cost harness for coding agents. It runs
an agent many times per task, records every step as neutral events, judges
outcomes independently, and eventually gates CI on statistical regression instead
of one noisy run.

The architecture is Python-first. The core is hexagonal: the domain owns
contracts, events, replay, metrics, and run orchestration; models, agents,
storage, tracing, judges, and reports plug in through ports.

## Current slice

This repo covers the Phase 0/1 core, the Phase 2-4 reliability, judgment, and
diagnosis path, and the Phase 5 CI gate from
[docs/architecture.md](docs/architecture.md):

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
  trace stamping with `chorus.failure.class` / `chorus.failure.step`, and
  validation metrics for injected failures.
- Trajectory-fan visualizer: a terminal view and a standalone HTML/SVG report
  with reliability cards, decay curve, divergence overlay, judgment, and
  diagnosis.
- Statistical CI gate: a baseline store, a paired-delta bootstrap regression test
  (`regressed` / `improved` / `inconclusive`, seeded and deterministic), a
  per-failure-class PR comment, and a composite GitHub Action wrapping it.
- Benchmark seam: a `load_suite` / `Scaffold` interface with two loaders — the
  deterministic synthetic suite, and a real **SWE-bench Verified** loader that maps
  instances to `TaskSpec`s (problem statement → prompt; `FAIL_TO_PASS` /
  `PASS_TO_PASS` test contract → metadata) over a deterministic subset.
- CLI commands to record/replay a dummy run, fan out a stochastic run, render
  trace/fan HTML artifacts, and gate a candidate against a baseline.
- Tests proving replay, event-log projection, metric math, divergence detection,
  judgment gating, judge caching, failure classification, the three gate verdicts,
  and seed reproducibility.

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
ruff check chorus tests
```

Run the Phase 0 record/replay demo:

```bash
chorus demo --n 3 --event-log .chorus/demo.jsonl
chorus replay --event-log .chorus/demo.jsonl
chorus replay --event-log .chorus/demo.jsonl --mutate
```

The `--mutate` replay intentionally changes the task prompt and should fail with
a replay divergence. That is the first proof that Chorus can detect when a
trajectory stops matching the recorded path.

Run the Phase 2-4 reliability fan-out:

```bash
chorus run --n 30 --success-rate 0.7 --error-rate 0.1 --seed 7
```

This fans out a flaky agent `N` times and prints the distribution. The point is
the gap between `pass@1`, projected `pass^k`, and the empirical unbiased
`pass^k` curve. A one-shot `pass@1` eval cannot see that gap; Chorus can.

```text
pass@1            0.80    Wilson95 [0.63, 0.90]
pass^k projected  0.0012  (i.i.d. k=30)
pass^k empirical  0.0000  (unbiased; 24/30 pass)
```

The run is reproducible per `--seed`. It also writes a standalone
`.chorus/fan.html` report you can open in a browser (no server, no build step):
reliability cards, the projected-vs-empirical `pass^k` decay curve, the
divergence overlay (the flaky agent shares a fixed opening plan, so the lanes
stay *converged* until the seed-driven split — divergence at step 4 — making the
overlay locate exactly where runs stop agreeing), the judgment cascade cost
panel, and the failure-diagnosis breakdown.

Render the Phase 1 trace viewer (`gen_ai.*` span waterfall + inspector) and
verify replay:

```bash
chorus trace --n 30 --seed 7 --replay
```

`--replay` re-executes every recorded trajectory through the replay gateway and
confirms each reproduces exactly, marking the spans `chorus.replay=true`.

Gate CI on a *statistical* regression (Phase 5):

```bash
chorus gate --branch main --n 20 --update-baseline                 # records the baseline
chorus gate --branch main --n 20 --scaffold worse --success-delta -0.12   # a candidate
```

The gate runs a task suite, compares the candidate against the stored baseline on
the same tasks/N/seed, and bootstraps a 95% CI on the per-task `pass^k` delta. It
emits one of three verdicts and exits non-zero **only** on `regressed`:

```text
## Chorus reliability gate — REGRESSED ❌
pass^5: 0.21 -> 0.07   (Δ -0.14, 95% CI [-0.21, -0.07])   <- below 0
New failures by class (candidate vs baseline):
  +16  contract_violation
  +15  tool_error
```

`improved` (CI entirely above 0) and `inconclusive` (CI straddles 0 — "widen N")
do not block. Blocking only on a statistically real regression — never on a raw
dip — is what keeps the gate from crying wolf and getting disabled. The bootstrap
is seeded, so the verdict is stable. The composite GitHub Action in
[chorus/ci/action.yml](chorus/ci/action.yml) wraps this command, posts the report
as a PR comment, and sets the check status.

Load the real SWE-bench Verified task set behind the same seam:

```bash
# from a local dump of princeton-nlp/SWE-bench_Verified, or `pip install datasets`
CHORUS_SWEBENCH_PATH=swebench_verified.jsonl \
  chorus gate --suite swe-bench-verified --n 5
```

The loader maps each instance to a `TaskSpec` (problem statement → prompt, the
`FAIL_TO_PASS` / `PASS_TO_PASS` tests → an acceptance contract in metadata) over a
deterministic subset. The gate deliberately **refuses** to run these through the
built-in stochastic scaffold — that would emit a `pass^k` that *looks* like a
benchmark result and isn't. Producing the real number is the next step below.

> **Headline benchmark number — intentionally absent.** The synthetic suite above
> demonstrates the gate *mechanics* deterministically and at zero model cost. The
> SWE-bench Verified **task loader** is implemented; the résumé-grade *number*
> ("changing only the scaffold moved pass^5 from X to Y on SWE-bench Verified")
> additionally requires a real model behind `AgentPort` and the SWE-bench test
> evaluator. That run is left undone rather than filled with a placeholder — the
> one locked rule is *the number is real or absent*.

## GitHub

This checkout is configured for:

```bash
origin https://github.com/Zwc-11/chorus.git
```
