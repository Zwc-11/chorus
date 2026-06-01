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

This repo covers the Phase 0/1 core and the Phase 2-4 reliability, judgment, and
diagnosis path from [docs/architecture.md](docs/architecture.md):

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
- CLI commands to record/replay a dummy run, fan out a stochastic run, and render
  trace/fan HTML artifacts.
- Tests proving replay, event-log projection, metric math, divergence detection,
  judgment gating, judge caching, failure classification, and seed
  reproducibility.

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

## GitHub

This checkout is configured for:

```bash
origin https://github.com/Zwc-11/chorus.git
```
