<!--
This document is the product architecture and roadmap. It explains the target
system, the design principles, and the phased build plan that the code follows.
-->

# Chorus — Architecture & Roadmap

> An open-source reliability + cost harness for coding agents.
> It runs an agent **many times** per task, **records** every step neutrally,
> **judges** the outcomes independently, and gates CI on *statistical regression*
> instead of a single noisy run.

---

## 0. Thesis (one paragraph)

A coding agent's output is a **distribution**, not a single trajectory. A model that
passes a task 6 times out of 10 and a model that passes 10 out of 10 look identical
under `pass@1`. Chorus treats reliability as a first-class runtime concern: it runs each
task `N` times, measures the spread, and uses *disagreement between runs* as a control
signal — running cheaply when runs agree and escalating (stronger judge, self-repair,
human gate) only when they diverge. Everything it observes is recorded as a replayable
trace, so any failure can be reproduced step-by-step instead of debugged by vibes.

---

## 1. The closed loop Chorus enables

```
   change prompt / tool / model
              │
              ▼
   ┌─────────────────────┐
   │  Chorus runs task ×N │
   └─────────┬───────────┘
             │ emits OTel gen_ai.* traces
             ▼
   ┌─────────────────────┐      pull traces via LangSmith MCP
   │  Trace backend       │◄──────────────────────────────┐
   │  (Phoenix / LangSmith)│                               │
   └─────────┬───────────┘                                 │
             │ pass^k, variance, cost, failure class       │
             ▼                                              │
   ┌─────────────────────┐                       ┌─────────┴────────┐
   │  Regression verdict  │── regressed? ───────► │  Coding agent     │
   │  (CI gate / report)  │                       │  reads trace,     │
   └─────────────────────┘                        │  debugs, re-codes │
                                                  └──────────────────┘
```

This is the same "write → trace → debug → write" loop people are excited about, but
**Chorus is the harness that makes the trace and the verdict trustworthy.** We dogfood it:
Chorus debugs *itself* through this loop.

---

## 2. Design principles

| Principle | What it means in practice |
|---|---|
| **Separation of powers (三权分立)** | The branch that *runs* the agent, the branch that *records* it, and the branch that *judges* it are isolated modules with no shared mutable state. The judge never executes; the executor never grades; the recorder never interprets. |
| **Reliability is a distribution** | `pass^k`, variance, and confidence intervals — never a single `pass@1`. |
| **Trace-first** | Nothing happens without an event. The event log *is* the source of truth; the UI, metrics, and replays are all derived from it (event sourcing). |
| **Integrate, don't replace** | Chorus wraps the agent and tools you already use (Claude Code, LangGraph, OpenAI Agents SDK). It is a library + CLI + GitHub Action, not a new runtime to migrate to. |
| **Cost-aware by default** | The expensive LLM-judge fires only on disagreement. A harness that gets disabled in month three for cost is worthless. |
| **Block on regression, not on threshold** | CI fails when reliability *drops vs the baseline*, not when it misses an arbitrary absolute bar. |

---

## 3. High-level architecture

Chorus uses a **hexagonal (ports & adapters)** core: a pure domain that knows nothing
about which model, framework, storage, or trace backend is plugged in. Everything
external is an adapter behind a port. This is what makes the three branches swappable
and testable.

```
                         ┌──────────────────────── CONSTITUTION ────────────────────────┐
                         │   Contracts & Policies (the rules all branches are bound to)   │
                         │   • task spec / acceptance contract   • step I/O schemas       │
                         │   • escalation policy   • cost & latency budgets               │
                         └───────────────────────────────────────────────────────────────┘
                                                    ▲
                                                    │ read-only
   ┌───────────── EXECUTIVE ─────────────┐   ┌──────┴──────── CORE DOMAIN ────────┐   ┌──────── JUDICIAL ────────┐
   │  Agent Runner                        │   │  Run Conductor (orchestrator)      │   │  Judge Cascade            │
   │   • drives agent-under-test          │◄──┤   • spawns N trajectories          ├──►│   • cheap convergence chk │
   │  Tool Gateway (single choke point)   │   │   • variance gate + escalation FSM │   │   • LLM-as-judge on        │
   │   • intercepts every tool call       │   │   • aggregates verdicts            │   │     disagreement only      │
   │   • record / replay                  │   └───────────────┬────────────────────┘   │  Failure Classifier        │
   └───────────────┬──────────────────────┘                   │                        │   • drift / schema / tool  │
                   │ emits events                              │ derives                └───────────┬────────────────┘
                   ▼                                           ▼                                    │ reads only
   ┌───────────── PRESS / RECORD ─────────────────────────────────────────────────────────────────┘
   │  Event Log (append-only, immutable)        ──►  Tracer (OTel gen_ai.* spans)  ──►  Backend adapter
   │  Replay Engine (re-folds events)                                                   (Phoenix / LangSmith)
   └───────────────────────────────────────────────────────────────────────────────────────────────────────┘
                   │ derived artifacts
                   ▼
   Reporter ──► CI Gate (GitHub Action, block-on-regression)   +   Visualizer (trajectory-fan view)

   PORTS (interfaces the core depends on, adapters implement):
     ModelPort · AgentPort · ToolPort · StoragePort · TracePort · JudgePort · ReportPort
```

**Why three branches matter:** the classic eval failure is a component grading its own
output. By construction, the Judicial branch can only *read* the Press/Record; it never
touches the Executive. The Record is append-only, so the Executive can't rewrite history
to look better. The Constitution (contracts/policies) is the only shared, read-only truth.

---

## 4. Components in detail

### 4.1 Constitution — Contracts & Policies
- **Responsibility:** declares what "success" means and what the run is allowed to cost.
  Holds the task/acceptance contract, per-step I/O schemas, the escalation policy
  (when to escalate), and cost/latency budgets.
- **Patterns:** *Specification* (acceptance criteria as composable predicates),
  *Policy object* (escalation rules as data, not code).
- **Notes:** schemas here power the boundary checks in 4.5 — schema-misalignment between
  steps is one of the top real-world failure modes, so it's a first-class contract.

### 4.2 Executive — Agent Runner + Tool Gateway
- **Responsibility:** drives the agent-under-test for one trajectory and funnels **every**
  tool/model call through one place so it can be recorded and, in replay mode, served from
  the log instead of the real world.
- **Patterns:** *Proxy / Interceptor* (the gateway wraps real tools), *Command* (each tool
  call is a serializable command), *Adapter* (per-framework agent drivers).
- **Invariant:** if a tool can be called from anywhere except the gateway, replay breaks.
  One choke point, no exceptions.
- **Modes:** `record` (call real tools, log requests+responses) and `replay` (serve logged
  responses; any divergence from the recorded path is a real correctness signal).

### 4.3 Press / Record — Event Log + Tracer + Replay Engine
- **Responsibility:** the neutral source of truth. Append-only log of typed events
  (`run_started`, `step_started`, `model_call`, `tool_call`, `tool_result`,
  `contract_check`, `verdict`, `run_finished`). The Tracer projects events into
  **OpenTelemetry GenAI spans** (`gen_ai.*`) for any OTLP backend. The Replay Engine
  re-folds events to reconstruct exact state.
- **Patterns:** *Event Sourcing* (state = fold over events), *Observer / pub-sub*
  (tracer + metrics subscribe to the event stream), *Repository* (StoragePort hides
  SQLite/Postgres/JSONL).
- **Notes:** keep message-content capture **off by default** for privacy; emit structural
  spans always, redact at the collector. Pin OTel semconv versions — `gen_ai.*` is still
  in *Development* status and attribute names can shift.

### 4.4 Core Domain — Run Conductor (the orchestrator)
- **Responsibility:** the brain. Spawns `N` trajectories for a task, watches their
  outputs, runs the **variance gate**, drives the **escalation state machine**, and
  aggregates per-run verdicts into a run-level reliability result.
- **Escalation FSM:** `RUN_CHEAP → (converged?) → DONE` else `→ ESCALATE → (repair? stronger judge? human gate?) → RE-EVAL → DONE/FAIL`.
- **Patterns:** *State machine* (run lifecycle), *Strategy* (pluggable escalation policy),
  *Mediator* (coordinates branches without them knowing each other).

### 4.5 Judicial — Judge Cascade + Failure Classifier
- **Judge Cascade (cost-aware):**
  1. **Tier 0 — deterministic check:** exit code / test pass / contract predicate. Free.
  2. **Tier 1 — convergence check:** do the `N` runs *agree*? Cheap (string/AST/embedding similarity). Free-ish.
  3. **Tier 2 — LLM-as-judge:** fires **only** when Tier 0/1 disagree. The expensive tier, rarely hit.
- **Failure Classifier:** labels each failed trajectory — `context_drift`,
  `schema_mismatch`, `tool_error`, `nondeterministic_loop`, `budget_exceeded` — by reading
  the record.
- **Patterns:** *Chain of Responsibility* (cascade tiers), *Strategy* (swappable judge
  models and classifiers), *Judge-of-judge* (sample a stronger judge to measure agreement
  with the cheap judge — evaluator capability matters).
- **Invariant:** reads the Record only; never invokes the Executive.

### 4.6 Adapters (the edges)
- **AgentPort adapters:** Claude Code, LangGraph, OpenAI Agents SDK.
- **ModelPort adapters:** Anthropic, OpenAI, DeepSeek (via one client interface).
- **TracePort adapters:** Phoenix (default, self-hosted), LangSmith (managed + MCP).
- **StoragePort adapters:** JSONL (zero-config) → SQLite → Postgres.
- **Pattern:** *Adapter* + *Dependency Injection* (wire adapters at startup; core stays pure).

### 4.7 Reporter + CI Gate + Visualizer
- **Reporter:** turns a run result into a Markdown/JSON report (pass^k, variance, CI,
  cost, latency, failure breakdown).
- **CI Gate:** a GitHub Action that runs the suite on a PR and **blocks on regression vs
  the baseline** (paired comparison, fails only if the confidence interval on the delta
  sits below zero). Posts the report as a PR comment.
- **Visualizer:** the trajectory-fan view — `N` lanes × steps, color-coded
  converged / diverged / repaired / failed. Reads straight from the event log.

---

## 5. Data model (core types)

```jsonc
// One recorded event (append-only)
Event {
  run_id, trajectory_id, seq,           // ordering
  type,                                  // run_started | model_call | tool_call | ...
  ts, payload,                           // type-specific data (prompt, tool args, result)
  hash                                   // hash of (model input / tool io) for replay equality
}

// One full attempt at the task
Trajectory {
  trajectory_id, run_id,
  events: Event[],
  outcome,                               // pass | fail | error
  failure_class?,                        // set by classifier
  cost_usd, tokens, latency_ms
}

// A whole task run (the distribution)
Run {
  run_id, task_id, model, suite_version,
  trajectories: Trajectory[],
  metrics: { pass_at_1, pass_at_k, variance, wilson_ci, mean_cost, p50_latency, p95_latency },
  escalations: int,
  verdict                                // pass | fail | needs_more_evidence
}

// CI comparison
RegressionReport {
  baseline_run, candidate_run,
  delta_pass_k, delta_ci, delta_cost,
  decision                               // ok | regressed | improved | inconclusive
}
```

`needs_more_evidence` is deliberate: when `N` is too small to separate signal from noise,
the honest verdict is neither pass nor fail.

---

## 6. Tracing & evaluation strategy (the explicit requirement)

### Tracing
- **Wire format:** OpenTelemetry **GenAI semantic conventions** (`gen_ai.*` spans:
  agent / workflow / tool / model + token & latency metrics). Vendor-neutral; pin the
  semconv version and opt in explicitly (`OTEL_SEMCONV_STABILITY_OPT_IN`).
- **Default backend:** **Phoenix** (OTel-native, open-source, self-hostable — best fit
  for an OSS project where users run their own).
- **Supported backend:** **LangSmith** (native OTel ingest; deepest LangGraph debugging;
  managed scorers). Selected via the TracePort adapter — no core changes.
- **The MCP closed loop:** Chorus → LangSmith → coding agent pulls traces via **LangSmith
  MCP** → debugs Chorus. Demo this on Chorus's own bugs (dogfooding).
- **Privacy:** content capture off by default; structural spans always on; redact at the
  collector.

### Evaluation
- **Metrics:** `pass@1`, `pass^k`, inter-run variance, Wilson confidence interval,
  cost/run, p50/p95 latency. (Capability *and* reliability *and* cost — all three.)
- **Judge:** the Tier-2 LLM-as-judge from 4.5, with a periodic **judge-of-judge** agreement
  measurement so you know your evaluator is trustworthy.
- **Benchmarks (for the headline number):** SWE-bench Verified, Terminal-Bench 2.0, and
  τ²-bench. Report `pass^k` deltas, not just `pass@1`.
- **Regression semantics:** paired baseline-vs-candidate on the same task set; block when
  the delta CI is entirely below zero; `needs_more_evidence` when it straddles zero.
- **Eval results as telemetry:** emit verdicts/scores back as OTel evaluation spans so the
  eval itself is observable.

---

## 7. Design patterns — consolidated

| Pattern | Where | Why |
|---|---|---|
| Hexagonal (Ports & Adapters) | whole system | Pure core; swap models, frameworks, backends without touching logic. |
| Event Sourcing | Press/Record | State and replay derive from an append-only log = trustworthy record. |
| Proxy / Interceptor | Tool Gateway | One choke point to record/replay every tool call. |
| Command | tool/model calls | Calls are serializable, loggable, replayable units. |
| Chain of Responsibility | Judge Cascade | Cheap checks first, expensive judge only on disagreement. |
| Strategy | escalation, judges, classifiers | Behaviors are pluggable and testable in isolation. |
| State Machine | Run Conductor | Explicit run lifecycle: cheap → escalate → repair → re-eval. |
| Observer / pub-sub | Tracer + metrics | Many derived views subscribe to one event stream. |
| Adapter | framework/provider/backend edges | Integrate, don't replace. |
| Repository | StoragePort | Hide JSONL/SQLite/Postgres behind one interface. |
| Specification | acceptance contracts | Composable, declarative success criteria. |
| Dependency Injection | startup wiring | Keep the domain free of concrete dependencies. |

---

## 8. Tech stack

- **Language:** Python (core + CLI). TypeScript only for the Visualizer.
- **CLI:** Typer or Click. **Async** run fan-out via `asyncio`.
- **Tracing:** OpenTelemetry SDK (`gen_ai` semconv) → Phoenix (default) / LangSmith.
- **Storage:** JSONL → SQLite (`aiosqlite`) → Postgres (optional).
- **Agents under test:** Claude Code, LangGraph, OpenAI Agents SDK (via AgentPort adapters).
- **CI:** GitHub Actions (composite action wrapping `murmur run`).
- **Visualizer:** single self-contained HTML/SVG file reading the event log (no app shell).
- **Packaging:** `pip install murmur-ai-harness`; `murmur run agent.py --n 12 --task tasks/142.yaml`.

---

## 9. Roadmap (phased — each phase ends in something demoable)

> Target: a solo build over ~6 focused weeks. Every phase produces an artifact you can
> screenshot, commit, or talk about. Don't start phase N+1 until phase N's exit criterion holds.

**Phase 0 — Skeleton & contracts (core domain, no real model)**
- Hexagonal skeleton: ports, domain types (Event/Trajectory/Run), in-memory adapters.
- Tool Gateway with record/replay against a *fake* agent + fake tools.
- **Exit:** a dummy run logs events; replay reproduces the exact path; a divergence is detected when you mutate a step.

**Phase 1 — Observation (make runs visible)**
- OTel `gen_ai.*` instrumentation; Phoenix running locally; LangSmith sink behind the port.
- **Exit:** you can open one run's trace in Phoenix and step through it. Dogfood: trace Chorus itself.

**Phase 2 — Reliability (the distribution + the visual)**
- Run Conductor fan-out (`N` trajectories), pass^k / variance / Wilson CI / cost / latency.
- Trajectory-fan Visualizer wired to the event log.
- **Exit:** `murmur run … --n 12` prints a distribution and renders the fan view.

**Phase 3 — Judgment (cost-aware cascade)**
- Tier 0/1/2 cascade; judge-of-judge agreement; escalation FSM.
- **Exit:** a measured cost number — "cascade costs X% of judge-every-run at equal accuracy."

**Phase 4 — Diagnosis (contracts + failure classes)**
- Inter-step schema/contract checks at boundaries; failure classifier.
- **Exit:** a failed run is auto-labeled (e.g. `schema_mismatch @ step 5`) and is replayable.

**Phase 5 — Productize + the headline number**
- GitHub Action (block-on-regression, PR comment); 1–2 AgentPort adapters.
- Run on SWE-bench Verified / Terminal-Bench subset.
- **Exit:** the resume line — a `pass^k` delta from changing only the harness — and a working CI gate.

**Phase 6 — Real-world validation (stretch)**
- Run Chorus on 2–3 popular open-source agent repos; find a real reliability/cost cliff; file reproductions upstream.
- Demo the LangSmith-MCP self-debug closed loop on a real Chorus bug.
- **Exit:** named real users / accepted upstream issues — the strongest interview signal.

---

## 10. Suggested repo layout

```
murmur/
  core/            # pure domain: conductor, escalation FSM, metrics, contracts
    ports.py       # ModelPort, AgentPort, ToolPort, StoragePort, TracePort, JudgePort
    events.py      # event types + event log
    conductor.py   # run-N, variance gate, aggregation
    judge.py       # cascade tiers
    classify.py    # failure classifier
  adapters/
    agents/        # claude_code.py, langgraph.py, openai_agents.py
    models/        # anthropic.py, openai.py, deepseek.py
    trace/         # phoenix.py, langsmith.py  (OTel emit)
    storage/       # jsonl.py, sqlite.py
  gateway/         # tool gateway (record/replay proxy)
  report/          # reporter + regression comparison
  ci/              # github action wrapper
  viz/             # trajectory-fan (single HTML/SVG)
  cli.py
  tasks/           # task specs + acceptance contracts
  benchmarks/      # swe-bench / terminal-bench runners
  tests/
```

---

## 11. Risks & cut-lines (scope discipline)

| Risk | Mitigation |
|---|---|
| Scope creep into "build a runtime" | Hard rule: Chorus *wraps* agents; it is library + CLI + Action. No GUI app, no scheduler, no marketplace. |
| OTel `gen_ai.*` churn (Development status) | Pin semconv version; isolate behind TracePort so a change is one adapter edit. |
| Judge cost blows up | Cascade is non-negotiable; Tier 2 must be the rare path. Track cost from Phase 3. |
| Benchmark runs are slow/expensive | Use a fixed *subset* of SWE-bench Verified for iteration; full run once for the number. |
| "Real users" never materialize | Phase 6 is stretch; the build is already strong without it. Designing *for* adoption is the signal. |

---

## 12. Success criteria

- **Engineering:** clean hexagonal core; deterministic replay works; cascade is measurably cheap.
- **Result:** a real `pass^k` delta on a public benchmark, attributable to the harness alone.
- **Adoption posture:** zero-config first run; integrates with tools people already use; OTel-standard traces.
- **Narrative:** "I built the harness that makes the trace and the verdict trustworthy" — and you can walk any interviewer through the three branches and why the judge can't cheat.
