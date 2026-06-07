<!--
Murmur AI Harness — send any prompt, get competing sub-agents, keep the best answer.
-->

# Murmur AI Harness

> **Send any prompt. Murmur spawns competing sub-agents, verifies their work, and returns the best answer.**

Murmur is an open-source AI harness that turns a single user prompt into a
multi-agent competition. A **planner** reads your prompt and automatically
generates a typed workflow — a DAG of parallel sub-agents, each working
independently in its own isolated context. The sub-agents **fan out**, produce
candidate answers, then **compete** through tournaments, adversarial
verification, and iterative repair. Murmur picks the winner and hands you the
final result.

The entire system runs on **cheap, commodity models** (DeepSeek, Ollama, or any
OpenAI-compatible API). Because each sub-agent call costs fractions of a cent,
Murmur can afford to spawn dozens of competing attempts by default — manufacturing
reliability through **volume** instead of paying for a single expensive model.

---

## How it works

```text
You send a prompt
    ↓
Planner reads the prompt, generates a typed workflow (DAG)
    ↓
Runtime spawns N isolated sub-agents in parallel
    ↓
Sub-agents compete: fan-out → tournament → adversarial verification → repair
    ↓
Best answer is selected and returned with a full execution trace
```

### Why Murmur?

| Approach | Workflow design | Model requirement | Cost |
|---|---|---|---|
| Static graphs (LangGraph, CrewAI) | Developer hand-codes steps | Any | Medium |
| Claude Code dynamic workflows | Self-writing | Frontier only (Opus) | High |
| **Murmur** | **Self-writing from your prompt** | **Cheap / local** | **Pennies or free** |

### Core idea

You don't need a smarter model — you need **more attempts**. Fan-out,
tournaments, and adversarial verification cost real money on frontier models;
on DeepSeek Flash or a local Ollama model they're nearly free. Murmur exploits
that cost asymmetry: spawn many cheap sub-agents, make them compete, and keep
only the verified winner.

---

## Screenshots

**Workflow Workbench** — interactive Three.js workspace for composing and
visualizing multi-agent workflow DAGs:

![Workflow Workbench](docs/images/murmur-workflow.png)

**Reliability Fan Report** — pass@1 / pass^k curves with divergence overlay,
judgment cascade, and failure diagnosis from a multi-trajectory fan-out:

![Reliability Fan Report](docs/images/fan-report.png)

**Trace Viewer** — span waterfall with per-trajectory timelines, token
accounting, and an interactive inspector for every step of every run:

![Trace Viewer](docs/images/trace-viewer.png)

---

## Architecture

```
Prompt → Planner → Workflow IR (typed DAG) → Runtime → Sub-agents → Result
```

- **Planner** — reads your prompt, selects or generates a workflow template
  (coding repair, strategy research, document review, or free-form), and emits a
  schema-validated `WorkflowPlan`.
- **Workflow IR** — a typed DAG of operator nodes (`classify`, `map`, `generate`,
  `exec`, `loop`, `filter`, `tournament`, `verify`, `rank`, `reduce`, `report`).
  Structured data, not code — safe, inspectable, and replayable.
- **Runtime** — walks the DAG, runs independent nodes concurrently (async with
  semaphore-capped parallelism), and treats merge nodes as barriers.
- **Sub-agents** — each sub-agent is an isolated actor with its own context,
  budget slice, and sandbox. No shared state between competitors.
- **Model Gateway** — one `ModelPort` interface with adapters for DeepSeek and
  Ollama (both OpenAI-compatible). Swapping models is a one-line config change.
- **Contract & Proof Layer** — enforceable engineering contracts, policy-controlled
  tool execution, diff verification, and PR-ready proof packages with
  distribution-aware reliability metrics.

---

## What's included

### Multi-agent orchestration
- Automatic workflow generation from any user prompt
- Parallel sub-agent fan-out with isolated contexts
- Tournament-style ranking of competing candidates
- Adversarial verification (blind refuter per artifact)
- Closed-loop repair with test feedback
- Token budget accounting and quarantine/taint tracking
- Append-only event log for resumable runs

### Contract-first code changes
- `fix-test` execution: reproduce a failing command → compile a typed contract →
  run a policy-controlled agent → verify the diff → emit proof artifacts
- Policy-controlled tools: `list_files`, `search`, `read_file`, `apply_patch`,
  `run_test`, `git_diff`, `finish`
- Dangerous operations (`.env` access, secrets, destructive shell, network,
  pushes) denied by default

### Reliability & observability
- `pass@1` with Wilson CI, projected and empirical `pass^k`, divergence analysis
- Cost-aware judgment cascade (deterministic → convergence → LLM, with caching)
- Failure diagnosis with taxonomy and trace stamping
- `gen_ai.*` span waterfall trace viewer (standalone HTML, no server)
- OTLP export to LangSmith / Phoenix for production debugging
- Statistical CI gate with bootstrap regression testing

### Benchmarks
- SWE-bench Verified loader with deterministic subsets
- Single-shot vs self-repair scaffold comparison
- Integrated and batch evaluation paths

### Trace import
- OpenAI Agents SDK, Claude Code, Google ADK, and LangGraph traces can be
  normalized into the Murmur event log for unified analysis

---

## Quick start

**Requirements:** Python 3.12+

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"
```

Run tests and lint:

```bash
pytest
ruff check chorus tests
```

### Try it

**Fix a failing test** (contract-first, single or multi-candidate):

```bash
chorus fix-test --cmd "python -m pytest tests/test_checkout.py -q" --budget 0.50
```

**Fan out a multi-trajectory reliability run:**

```bash
chorus run --n 30 --success-rate 0.7 --error-rate 0.1 --seed 7
```

**Render the trace viewer:**

```bash
chorus trace --n 30 --seed 7 --replay
```

**Gate CI on statistical regression:**

```bash
chorus gate --branch main --n 20 --update-baseline
chorus gate --branch main --n 20 --scaffold worse --success-delta -0.12
```

**Generate a workflow plan from a task:**

```bash
chorus workflow plan --task "Fix the checkout discount bug" \
  --cmd "python -m pytest tests/test_checkout.py -q" --attempts 5 --max-repairs 3
```

See [docs/quickstart.md](docs/quickstart.md) for the full walkthrough and
[docs/github-action.md](docs/github-action.md) for CI integration.

---

## Project status

Working and tested locally:

- Multi-agent workflow planner and runtime
- Contract-first `fix-test` with proof packages
- Reliability fan-out with distribution-aware metrics
- Trace viewer, fan report, and divergence overlay
- Statistical CI gate with bootstrap regression testing
- SWE-bench harness wiring (fake models/evaluators)
- External trace importers (OpenAI, Claude Code, ADK, LangGraph)

Not yet publicly validated:

- Paid SWE-bench benchmark with a real frontier model and Docker evaluator.
  Murmur refuses to print a number unless a real evaluation actually ran.

---

## Repository

```
https://github.com/Zwc-11/Murmur-ai-harness
```
