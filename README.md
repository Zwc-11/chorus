# Chorus

Chorus is an open-source reliability and cost harness for coding agents. It runs an
agent many times per task, records every step as neutral events, judges outcomes
independently, and eventually gates CI on statistical regression instead of one noisy run.

The architecture is Python-first. The core is hexagonal: the domain owns contracts,
events, replay, metrics, and run orchestration; models, agents, storage, tracing, judges,
and reports plug in through ports.

## Current slice

This repo is starting at Phase 0 from [docs/architecture.md](docs/architecture.md):

- Pure core domain types and ports.
- Append-only JSONL and in-memory event stores.
- Tool gateway with record and replay modes.
- Fake agent adapter for deterministic local demos.
- CLI commands for recording and replaying a dummy run.
- Tests proving replay reproduces a recorded path and detects divergence.

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

Run the Phase 0 demo:

```bash
chorus demo --n 3 --event-log .chorus/demo.jsonl
chorus replay --event-log .chorus/demo.jsonl
chorus replay --event-log .chorus/demo.jsonl --mutate
```

The `--mutate` replay intentionally changes the task prompt and should fail with a replay
divergence. That is the first proof that Chorus can detect when a trajectory stops matching
the recorded path.

## GitHub

This checkout is configured for:

```bash
origin https://github.com/Zwc-11/chorus.git
```
