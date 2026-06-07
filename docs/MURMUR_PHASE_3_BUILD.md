# Murmur — Phase 3 Build Instruction (Judgment)

> Build-ready spec. Companion to `MURMUR_PHASE_1_BUILD.md`, `MURMUR_PHASE_2_BUILD.md`.
> Goal: decide pass/fail **cheaply**, escalate to an expensive judge **only on
> disagreement**, and **act** on the divergence Phase 2 named. The headline artifact is a
> measured cost number — *the cascade costs X% of judge-every-run at equal accuracy.*

---

## Definition of done (the exit criterion)

Done when **all** hold:

1. A run is judged by the **three-tier cascade**; Tier 2 (LLM-judge) fires only on the
   ambiguous/divergent minority.
2. The **escalation state machine** runs: a divergent run escalates at the divergence step
   (repair / stronger judge / human gate), then re-evaluates.
3. A **measured cost result** exists on a labelled task set: `cost_ratio = cost(cascade) /
   cost(judge-every-run)` **with a proof that accuracy is held equal** (`Δaccuracy ≈ 0`
   within CI). Plus the tier-hit distribution (% resolved at T0 / T1 / T2).
4. A **judge-of-judge** agreement number (raw % + Cohen's κ) shows the cheap judge is
   trustworthy.
5. Judge calls go through the gateway → recorded → **replayable and cached** (re-runs don't
   re-pay).
6. Tests + `ruff` clean.

If the cost number isn't accompanied by the accuracy-parity proof, it is **not done** — a
cheaper-but-worse judge is not a win.

---

## Locked decisions

- **Three tiers, cheap → expensive:** Tier 0 deterministic (free), Tier 1 convergence
  (reuses Phase 2 `agreement_at`, ~free), Tier 2 LLM-judge (expensive, rare).
- **Escalation policy is data, not code** — thresholds and action choice live in the
  Constitution (`Policy` object), so behaviour is configurable and testable.
- **Judicial reads the record only.** The judge never invokes the Executive. (The *repair*
  action does re-run the agent — but that's the Conductor driving the Executive, not the
  judge.)
- **Judge calls are model calls** → through the Tool Gateway → recorded → cached by input
  hash. This is non-negotiable for replay determinism *and* cost.
- **Evaluator capability matters.** Default Tier-2 judge is a strong model; a different
  model family from the agent-under-test where possible (avoids self-enhancement bias).

---

## Build tasks (in order)

1. **Tier 0 — deterministic check.** Exit code / test pass / contract predicate → PASS /
   FAIL / UNKNOWN. Cost 0. (Usually decisive for coding tasks.)
2. **Tier 1 — convergence.** Reuse Phase 2 agreement on the *outcome/output* signature
   across trajectories. Cost ~0.
3. **Tier 2 — LLM-as-judge.** Rubric-based, structured output, bias mitigations. Expensive.
4. **Cascade orchestration** — the gating logic that makes Tier 2 rare.
5. **Escalation FSM** — drive repair / stronger-judge / human-gate at the divergence step.
6. **Judge-of-judge** — sample a stronger judge, measure agreement.
7. **Cost-measurement harness** — the methodology below; produce the headline number.
8. **Tests** — gating correctness, FSM transitions, cost accounting, cache/replay.

---

## The cascade (concrete)

```python
def judge_run(run, contract, policy):
    verdicts = {}

    # Tier 0 — deterministic, per trajectory, free
    for t in run.trajectories:
        verdicts[t.id] = contract.deterministic_check(t)   # PASS / FAIL / UNKNOWN

    # Tier 1 — convergence across trajectories, ~free  (reuse Phase 2)
    agree = agreement_on_outcome(verdicts)
    if all_known(verdicts) and agree >= policy.converge_tol:
        return aggregate(verdicts, resolved_tier=1)         # cheap accept — no LLM spend

    # Tier 2 — LLM-judge ONLY the ambiguous / minority trajectories
    for t in run.trajectories:
        if verdicts[t.id].outcome == UNKNOWN or in_minority(t, verdicts):
            verdicts[t.id] = llm_judge(t, contract.rubric)  # expensive, recorded + cached
    return aggregate(verdicts, resolved_tier=2)
```

The savings come from one fact: on a healthy run most trajectories agree and Tier 0 is
decisive, so **Tier 2 never fires.** It fires only where reliability is actually in question.

### Tier 2 LLM-judge design
- **Structured output:** `{verdict: pass|fail, confidence: 0–1, rationale, rubric_scores}`.
- **Rubric** comes from the Constitution's acceptance contract (composable predicates).
- **Bias mitigations:** randomise candidate order (position bias); judge correctness not
  length (verbosity bias); use a judge model from a different family than the agent
  (self-enhancement bias).

---

## Escalation state machine

```
            ┌─────────────┐
            │  RUN_CHEAP  │   Tier 0 + Tier 1
            └──────┬──────┘
        converged & all known?
            ┌──────┴───────┐
           yes             no
            │               │
            ▼               ▼
         ┌──────┐     ┌───────────┐
         │ DONE │     │ ESCALATE  │   at the divergence step (Phase 2)
         └──────┘     └─────┬─────┘
                            │ policy picks ONE action
            ┌───────────────┼─────────────────┐
            ▼               ▼                  ▼
       ┌─────────┐   ┌──────────────┐   ┌────────────┐
       │ REPAIR  │   │ STRONG_JUDGE │   │ HUMAN_GATE │
       │ (re-run │   │ (bigger judge│   │ (queue for │
       │  agent) │   │   model)     │   │  a human)  │
       └────┬────┘   └──────┬───────┘   └─────┬──────┘
            └───────────────┼─────────────────┘
                            ▼
                      ┌───────────┐
                      │  RE_EVAL  │
                      └─────┬─────┘
                  ┌─────────┴──────────┐
                pass               fail / budget exceeded
                  │                     │
                  ▼                     ▼
               ┌──────┐             ┌──────┐
               │ DONE │             │ FAIL │
               └──────┘             └──────┘
```

| From | Trigger / guard | Action | To |
|---|---|---|---|
| RUN_CHEAP | converged & all known | accept | DONE |
| RUN_CHEAP | divergence or UNKNOWN | — | ESCALATE |
| ESCALATE | policy = repair | re-run agent from step k−1 | REPAIR → RE_EVAL |
| ESCALATE | policy = strong_judge | re-judge with stronger model | STRONG_JUDGE → RE_EVAL |
| ESCALATE | policy = human | enqueue for human verdict | HUMAN_GATE → RE_EVAL |
| RE_EVAL | passes | accept | DONE |
| RE_EVAL | fails or over budget | stop | FAIL |

Policy chooses the action by cost/criticality: cheap+low-stakes → repair; ambiguous
correctness → strong_judge; high-stakes/irreversible → human_gate.

---

## Cost-measurement methodology (the headline — get this rigorous)

On a **labelled** task set (tasks with known ground-truth pass/fail):

```
Baseline B  — judge EVERY trajectory with Tier 2.
  cost(B)     = sum of all Tier-2 judge-call costs
  accuracy(B) = agreement of B's verdicts with ground truth

Cascade C   — the tiered approach.
  cost(C)     = sum of ONLY the Tier-2 calls that actually fired
  accuracy(C) = agreement of C's verdicts with ground truth

Report:
  cost_ratio  = cost(C) / cost(B)            # the savings
  Δaccuracy   = accuracy(C) − accuracy(B)    # MUST be ≈ 0 (within CI)
  tier_hits   = % resolved at T0 / T1 / T2   # explains WHERE the savings come from
```

**The claim is only valid if `Δaccuracy ≈ 0`.** State it as *"the cascade costs X% of
judge-every-run at equal accuracy (Δacc = +0.2pp, 95% CI straddles 0)."* If accuracy drops,
say so plainly — a cheaper, worse judge is not a result. This rigor is exactly what
separates a real engineering claim from a demo.

---

## Judge-of-judge (is the cheap judge trustworthy?)

- On a sample of cases the cheap judge ruled on, run a **stronger** judge model.
- Report **raw agreement %** and **Cohen's κ** (chance-corrected).
- If κ < threshold (e.g. 0.6), the cheap judge is untrustworthy on this task class → alert,
  and consider upgrading the default Tier-2 model. A strong judge catches failures a weak
  one misses; this measurement is how you *know* yours is good enough.

---

## Determinism, caching, replay

- Every judge call is a `model_call` → through the gateway → recorded as an event.
- **Cache by `hash(rubric + trajectory_summary + judge_model)`** — identical inputs never
  re-pay. Critical for cost and for stable re-runs.
- In replay mode, judge verdicts are served from the log; no live spend, exact reproduction.

---

## UI specification (zones to add to the Phase 2 screen)

- **Cost panel:** two bars — `judge-every-run` vs `cascade` — with the ratio and the
  accuracy-parity badge (`Δacc ≈ 0`). A small stacked bar shows the tier-hit distribution.
- **Escalation trace:** which runs escalated, at which step, which action fired, and the
  RE_EVAL outcome. Links to the trajectory's Phase 1 trace.
- **Judge-agreement panel:** raw % + κ, with a warning state when κ is low.
- Cross-cutting rules unchanged: mono for machine values, colour encodes tier/outcome only,
  round every number, no prose inside the widget.

---

## States to design

- **Tier-2 never fired** (fully converged run): cost panel shows ~0 LLM spend — the ideal
  case, present it as success.
- **Low judge agreement** (κ low): surface a warning, don't silently trust the cheap judge.
- **Escalation exhausted budget:** run ends FAIL with "budget exceeded" — show the partial
  trace, not a crash.
- **Human gate pending:** verdict "awaiting human" — neither pass nor fail yet.

---

## Out of scope for Phase 3 (resist)

- **Failure *classification*** (`schema_mismatch`, `context_drift`, …) → Phase 4. Phase 3
  only needs PASS / FAIL / UNKNOWN.
- **Benchmark numbers** (SWE-bench, etc.) → Phase 5.
- Auto-tuning the escalation policy → leave thresholds as configured data.

---

## Phase 3 exit checklist

- [ ] Tiers 0 / 1 / 2 implemented; Tier 2 gated to the ambiguous minority.
- [ ] Escalation FSM with repair / strong-judge / human-gate; policy is data.
- [ ] Cost result on a labelled set, **with accuracy-parity proof** and tier-hit breakdown.
- [ ] Judge-of-judge: raw agreement + Cohen's κ, with a low-κ warning state.
- [ ] Judge calls recorded, cached by hash, replayable.
- [ ] UI: cost panel, escalation trace, judge-agreement panel; all four states.
- [ ] Tests green; `ruff` clean.

---

## Then: Phase 4 (hand-off)

Phase 3 gives you PASS / FAIL / UNKNOWN and escalation. Phase 4 answers **why a failure
happened and where** — turning "FAIL" into "`schema_mismatch @ step 5`, here's the replay."
The escalation `repair` action becomes far smarter once Phase 4 can tell it *what* broke.
