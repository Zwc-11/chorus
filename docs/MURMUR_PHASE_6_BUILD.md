# Murmur — Phase 6 Build Instruction (Real-World Validation)

> Build-ready spec. Companion to the Phase 1–5 build docs.
> Goal: stop testing Murmur on Murmur, and point it at **real coding agents on real
> tasks** until it finds a genuine reliability or cost cliff — then make that finding
> external (an upstream reproduction, a named user). This is the stretch phase: the
> engineering is already defensible without it, but a real finding is the single
> strongest interview signal.

---

## Dependency gate (read first)

Phase 6 builds **only** on the merged real-agent path, not on new core work:

- `SwePatchAgent` ([murmur/adapters/agents/swe.py](../murmur/adapters/agents/swe.py)) is the
  reference real `AgentPort`: it drives a real `PatchModel` through the gateway, so a real
  run already inherits tracing, replay, divergence, and diagnosis.
- The conductor takes an injectable `JudgePort`
  ([murmur/core/conductor.py](../murmur/core/conductor.py)); `SweBenchJudge` runs the real
  tests. Any new agent reuses this seam.
- The Phase 5 gate + `pass^k` machinery is what turns a real run into a verdict. Do **not**
  re-implement metrics here.

If the SWE-bench harness number is still absent (it is, by design), that is fine — Phase 6
does not need the *headline* number; it needs a *finding*. They can share the same paid run.

---

## The honest cost/effort split (read before promising anything)

Phase 6 has three tiers. Keep them separate so nothing gets overclaimed.

| Tier | What | Who/what it needs | Autonomous? |
|---|---|---|---|
| **A. Machinery** | New real `AgentPort` adapters; a "reliability cliff" report; the LangSmith export + MCP wiring; fake-model smoke tests | Nothing paid | **Yes — build now** |
| **B. The real run** | Run a real agent ×N on real tasks until a cliff appears; the LangSmith trace of a real Murmur bug | `ANTHROPIC_API_KEY` (or other), Docker for SWE-bench eval, ~\$20–200 for a small validation (not the \$1–2k headline run), a LangSmith account | **No — user-gated (keys + budget)** |
| **C. The finding** | File the reproduction upstream; recruit a named user | A real maintainer interaction | **No — external/manual** |

The rule from Phase 5 carries over: **a finding is real or absent.** No invented cliff, no
fabricated upstream issue. Tier A makes the real run a sure thing; Tiers B/C are the user's
call.

---

## Definition of done (the exit criterion)

Done when **all** hold:

1. Murmur drives **at least one real third-party agent** (not just `SwePatchAgent`) through
   the existing `AgentPort` — "integrate, don't replace" proven on a framework people use.
2. A **reliability-cliff report**: a real agent whose `pass@1` looks healthy but whose
   `pass^k` collapses, with the divergence step and per-class failure breakdown that say
   *where* and *why* — rendered from a real run's event log.
3. The **LangSmith-MCP closed loop** is demonstrated: a Murmur run's trace lands in
   LangSmith, a coding agent pulls it via the LangSmith MCP server, and uses it to debug a
   real Murmur bug. Dogfood.
4. At least one **external artifact**: an upstream reproduction issue (linked) **or** a named
   user running Murmur on their agent.
5. Tests + `ruff` clean; new adapters covered with a fake model (no network in CI).

Items 1, 2 (machinery + fake smoke), 3 (wiring), and 5 are Tier A. The *real* numbers in 2
and the live demo in 3, plus item 4, are Tiers B/C.

---

## Locked decisions

- **Integrate, don't replace.** New agents are `AgentPort` adapters; the core does not change.
  Reuse `SwePatchAgent` as the template and the conductor's `JudgePort` seam.
- **Reuse Phase 5 statistics.** The cliff is a `pass^k` curve + Wilson CI + divergence; the
  gate already computes all of it. Phase 6 supplies *input*, not new math.
- **Real or absent.** Same as Phase 5. The cliff report ships with a smoke (fake-model) run
  for CI and is filled with a real run only when one is paid for.
- **One framework, done well**, beats three wired badly. Pick the adapter with the best
  reliability-cliff story and the deepest LangSmith debugging (LangGraph is the natural
  first; Claude Code and OpenAI Agents SDK are follow-ups).
- **Privacy unchanged.** Content capture stays off by default even on real third-party runs.

---

## Build tasks (in order)

**Tier A — build now, free**

1. **Real third-party `AgentPort` adapter.** Wrap one real framework (recommend **LangGraph**:
   pip-installable, LangSmith-native). The adapter drives the framework's agent for one
   trajectory, records model/tool calls through the gateway exactly like `SwePatchAgent`, and
   returns the final output. Provide a **fake model/graph** for tests so CI needs no network.
   Optional extra: `pip install "murmur-harness[agents]"`.
2. **Reliability-cliff report + CLI.** A `murmur cliff` command (or `gate` flag) that runs the
   real agent ×N on a small real task set and renders the existing fan/overlay/diagnosis
   report, foregrounding the `pass@1` → `pass^k` gap and the divergence step. Default to a
   deterministic fake model so the artifact renders for free; a real model fills in the number.
3. **LangSmith export, verified.** Confirm the Phase 1 `TracePort` OTLP adapter
   (`backend="langsmith"`) emits a real Murmur run's `gen_ai.*` spans to LangSmith. Document
   the env (`LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`).
4. **Tests.** Adapter behavior with the fake model; the cliff report renders from a recorded
   log; the gate verdict on a real-agent suite (fakes).

**Tier B — user-gated (keys + budget + Docker)**

5. **The real cliff run.** Run the chosen agent ×N on a handful of real SWE-bench Verified (or
   Terminal-Bench) instances until a cliff appears; capture the report. Small N, small subset
   — this is a *finding*, not the headline number.
6. **The LangSmith-MCP demo.** Point Murmur at LangSmith, pick a real Murmur bug, pull its
   trace through the LangSmith MCP server in a coding agent, and fix the bug from the trace.
   Record the loop.

**Tier C — external/manual**

7. **File it.** Open an upstream reproduction issue on the agent repo with the trace + the
   `pass^k` evidence, or get one external user running Murmur on their agent. Link it in the
   README.

---

## The reliability-cliff artifact (what makes Phase 6 land)

```
agent: <real framework>@<version>   model: <fixed>   tasks: <real subset>   N: <small>

pass@1   0.72   Wilson95 [0.55, 0.84]      <- looks shippable on one run
pass^5   0.19                               <- fails 4 of 5 times across runs
divergence: step 3 (tool selection)         <- where the runs stop agreeing
failures: schema_mismatch ×7, tool_error ×3 <- what breaks, by Phase 4 label
```

The cliff is the thesis made concrete on someone else's agent: the one-shot number hides a
distribution Murmur exposes, and the divergence step + failure class say where to look. The
report reuses [murmur/report/fan_html.py](../murmur/report/fan_html.py) unchanged.

---

## Out of scope for Phase 6 (resist)

- New core/metric work — Phase 6 only *feeds* the existing machinery.
- A hosted dashboard / SaaS — Murmur stays library + CLI + Action.
- Wiring every agent framework — one real adapter, done well.
- Inventing a cliff or an upstream issue to fill the slot — real or absent.

---

## Phase 6 exit checklist

- [ ] One real third-party `AgentPort` adapter, fake-model tested (Tier A).
- [ ] `murmur cliff` (or `gate` path) renders the reliability-cliff report from a real-agent
      run; free with a fake model (Tier A).
- [ ] LangSmith OTLP export verified + documented (Tier A).
- [ ] Tests green; `ruff` clean (Tier A).
- [ ] A **real** cliff captured on a real agent (Tier B — needs keys/Docker/budget).
- [ ] LangSmith-MCP self-debug loop demonstrated on a real Murmur bug (Tier B).
- [ ] An external artifact: linked upstream reproduction **or** a named user (Tier C).

---

## The résumé line (only once a finding is real)

> "…and gates CI on statistical regression. Pointed at <real agent> on <benchmark>, Murmur
> surfaced a reliability cliff — pass@1 X% but pass^5 Y% — localized to a divergence at step
> N; the reproduction is filed upstream as <link>."

Tier A makes that sentence *possible*. It becomes *true* only after the Tier B run and the
Tier C filing — never write it with a placeholder.
