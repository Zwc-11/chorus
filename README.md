<!--
This is the repo entrypoint for humans. It explains what Chorus is, what works
right now, and the commands needed to install, test, and run the Phase 0 demo.
-->

# Chorus

Chorus is an open-source reliability and cost harness for coding agents. It runs an
agent many times per task, records every step as neutral events, judges outcomes
independently, and eventually gates CI on statistical regression instead of one noisy run.

The architecture is Python-first. The core is hexagonal: the domain owns contracts,
events, replay, metrics, and run orchestration; models, agents, storage, tracing, judges,
and reports plug in through ports.

## Current slice

This repo covers Phase 0 and a Phase 2 reliability slice from
[docs/architecture.md](docs/architecture.md):

- Pure core domain types and ports.
- Append-only JSONL and in-memory event stores.
- Tool gateway with record and replay modes.
- Fake agent adapter for deterministic local demos.
- **Stochastic (flaky) agent** so the harness has a real distribution to measure.
- **Concurrent `N`-trajectory fan-out** in the run conductor.
- **Distribution-aware metrics:** `pass@1`, `pass^k`, variance, Wilson CI, cost,
  p50/p95 latency, plus a failure breakdown.
- **Trajectory-fan visualizer** — a terminal view and a standalone HTML/SVG file.
- CLI commands to record/replay a dummy run and to fan out a stochastic run.
- Tests proving replay reproduces a recorded path, detects divergence, and that
  the distribution is reproducible per seed.

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

The `--mutate` replay intentionally changes the task prompt and should fail with a replay
divergence. That is the first proof that Chorus can detect when a trajectory stops matching
the recorded path.

Run the Phase 2 reliability fan-out:

```bash
chorus run --n 12 --success-rate 0.7 --error-rate 0.1 --seed 7
```

This fans out a flaky agent `N` times and prints the distribution. The point is the gap
between the two pass rates: an agent at `pass@1 = 0.75` only succeeds on *all* 12 runs
about 3% of the time (`pass^k`). A one-shot `pass@1` eval cannot see that; Chorus can.

```text
  pass@1  ████████████████░░░░░░░░  0.75   (9/12 single runs pass)
  pass^k  █░░░░░░░░░░░░░░░░░░░░░░░░  0.0317  (all 12 runs pass)
```

The run is reproducible per `--seed`. It also writes a standalone `.chorus/fan.html`
trajectory-fan you can open in a browser (no server, no build step).

## GitHub

This checkout is configured for:

```bash
origin https://github.com/Zwc-11/chorus.git
```
