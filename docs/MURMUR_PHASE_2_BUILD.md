# Murmur — Phase 2 Build Instruction (Reliability)

> Build-ready spec. Companion to `MURMUR_PHASE_1_BUILD.md`.
> Goal of this phase: treat a run as a **distribution of trajectories**, measure its
> spread honestly, and render it — including the divergence view that is Murmur's
> differentiator. Phase 1 (tracing) must hold first; this phase reads its event log.

> **Status note.** You already built a Phase 2 *slice* (fan + metrics) on synthetic
> output before tracing existed. Phase 2 is therefore **harden + connect**, not a fresh
> build: fix the metric math, feed it from real recorded trajectories, link it to Phase 1
> traces, and add the divergence overlay. *Then* make the first Phase 2 commit.

---

## Definition of done (the exit criterion)

Done when **all** of these hold:

1. `murmur run … --n 30` reports `pass@1` **with a Wilson CI**, a **`pass^k` decay curve**
   (both estimators), variance, cost, latency, and a failure breakdown.
2. The fan and the metrics are computed from **real recorded trajectories** (the Phase 1
   event log), not the `stochastic` agent's ad-hoc output.
3. The **divergence overlay** renders: N trajectories aligned on step index, a per-step
   agreement strip, and the divergence step highlighted and named.
4. **Linkage:** clicking a fan/overlay lane opens that trajectory's Phase 1 trace
   waterfall; clicking the divergence column filters to the trajectories that split there.
5. The same seed reproduces the exact distribution.
6. Tests cover the metric math, the unbiased/parametric divergence, the agreement/
   divergence algorithm, and seed reproducibility. `ruff` clean.

If any fails, do not commit and do not start Phase 3.

---

## Locked decisions (do not relitigate mid-build)

- **Reliability is a distribution.** Every reported number is `pass^k` / variance / CI —
  never a bare `pass@1`.
- **Default `N` = 30.** Document 50+ for real claims. `--n` overridable. (At N=12 the CI is
  uselessly wide.)
- **Two `pass^k` estimators, always both:** parametric (`p^k`, the i.i.d. projection) and
  unbiased empirical (`C(c,k)/C(n,k)`, what the data supports). Report as a **curve over
  k = 1…N**, never a single point. Label it "projected P(all k pass)" — never "all N pass."
- **`stochastic` agent → test fixture only.** It validates the metric math deterministically.
  The real run path uses a real `AgentPort` adapter and records real traces.
- **Alignment is on step index, not wall-clock.** That is what makes the overlay an overlay.
- **Everything reads off the event log.** The fan, metrics, and overlay are derived views;
  they store nothing of their own.

---

## Build tasks (in order)

**1. Fix the metric math.** Replace the slice's metrics with the definitions below
(paste-ready core is also in `MURMUR_PHASES.md` §Phase 2).

**2. Feed from real trajectories.** Point the conductor's aggregation at recorded
trajectories from the Phase 1 log. Demote `stochastic` to `tests/`.

**3. Reliability summary + decay curve UI.** Metric cards (with CI) + the two-line decay
curve (zone 1 of the screen).

**4. Agreement / divergence algorithm.** Implement per the spec below; it produces both the
agreement strip and the named divergence step.

**5. Divergence overlay UI.** N lanes aligned on step index, agreement strip, divergence
column highlighted (zone 3).

**6. Linkage.** Wire lane-click → Phase 1 trace waterfall; divergence-column-click → filter
to the split trajectories.

**7. Tests.** Metric correctness, estimator divergence, agreement/divergence detection on a
known fixture, seed reproducibility.

---

## Metric definitions (concrete)

```python
import math
from math import comb

def pass_at_1(c, n):            # per-run pass rate (point estimate)
    return c / n if n else 0.0

def wilson_ci(c, n, z=1.96):    # 95% interval on the rate — report this next to pass@1
    if n == 0: return (0.0, 0.0)
    p = c / n; d = 1 + z*z/n
    centre = (p + z*z/(2*n)) / d
    half = (z/d) * math.sqrt(p*(1-p)/n + z*z/(4*n*n))
    return (max(0.0, centre - half), min(1.0, centre + half))

def pass_hat_k_parametric(c, n, k):   # i.i.d. projection ("the model says")
    return (c / n) ** k if n else 0.0

def pass_hat_k_unbiased(c, n, k):     # unbiased empirical ("the data supports"); 0 once k > c
    return 0.0 if (k > c or k > n) else comb(c, k) / comb(n, k)

def reliability_curve(c, n):          # both lines over k = 1..n
    return [{"k": k,
             "projected": pass_hat_k_parametric(c, n, k),
             "empirical": pass_hat_k_unbiased(c, n, k)} for k in range(1, n + 1)]
```

The **gap** between the two lines, and the **k beyond which `empirical` is 0** (= the number
of passes observed), are the honest expression of uncertainty. Surface both; explain the gap.

---

## Agreement / divergence algorithm (concrete)

The point: align trajectories on step index and find the first step where they stop agreeing.

```python
def step_signature(traj, s, capture=False):
    """What a trajectory 'does' at step s — the decision, not the raw text."""
    ev = traj.step(s)
    if ev is None:                         # trajectory never reached step s
        return None                        # -> 'inactive' cell
    if ev.kind == "tool_call":
        return ("tool", ev.tool_name, normalize_args(ev.args))
    if ev.kind == "model_call":
        return ("act", ev.chosen_action)   # next action chosen, not the prose
    return (ev.kind,)

def agreement_at(trajs, s, similar=exact_match):
    sigs = [step_signature(t, s) for t in trajs]
    active = [x for x in sigs if x is not None]
    if not active:
        return None
    clusters = cluster(active, similar)    # exact by default; AST/embedding via config
    majority = max(clusters, key=len)
    return len(majority) / len(active)     # 1.0 = full agreement

def divergence_step(trajs, n_steps, tol=1.0):
    for s in range(n_steps):
        a = agreement_at(trajs, s)
        if a is not None and a < tol:
            return s                        # first split
    return None                             # fully converged (degenerate happy case)
```

**Cell state for the overlay** at `(trajectory, s)`:
- `converged` — signature in the majority cluster.
- `diverged` — active but not in the majority.
- `failed` — terminal failure at/after this step.
- `inactive` — `None` (trajectory aborted before reaching step s).

**Pluggable similarity.** Default `exact_match` on the signature. Config can swap in AST or
embedding similarity with a threshold, for agents whose equivalent actions differ textually.
Keep this behind a `Strategy` so Phase 3's gate reuses the same notion of "agreement."

---

## UI specification — three stacked zones

All three read the same event log; all link down into Phase 1 traces.

### Zone 1 — Reliability summary + decay curve
- **Metric cards** (secondary-bg, 13px label, 24px/500 number, mono subline): `pass@1` with
  its Wilson CI as the subline; `pass^k` labelled `projected · i.i.d.`; `variance`;
  `failures (X / N)` with a `f fail · e error` subline. Never a bare number.
- **Decay curve:** two series over k = 1…N — parametric as a solid line, empirical (unbiased)
  as dashed line + points. A shaded band marks `k > c` (data-can't-support region). Legend
  above; mono axis labels. Latency/cost summarised in the cards, not the chart.

### Zone 2 — Trajectory fan
- N lanes × steps, each cell coloured by outcome. The at-a-glance "shape of the run."
- Lane click → Phase 1 trace waterfall for that trajectory.

### Zone 3 — Divergence overlay (the differentiator)
- **Agreement strip** (top): one bar per step, height = `agreement_at(step)`. Flat at 100%
  while runs agree, drops at the divergence step.
- **Lanes aligned on step index**, cells = the four states above. Inactive = dashed, so you
  see how far each failure got.
- **Divergence column highlighted** (warning band + outlined cells) and named
  (`↑ divergence · step k`). Column click → filter to the trajectories that split there.

**Cross-cutting UI rules** (same as Phase 1): mono for machine-generated values, sans for
labels; colour encodes outcome/agreement only, never decoration; dense over airy; round
every displayed number; no prose/titles inside the widget.

---

## States you must design (not just the happy path)

- **Small N (< ~5):** render cells but **grey the agreement strip** with a "low confidence"
  note — be honest that thin data can't support a divergence claim.
- **All-converged (degenerate):** agreement flat at 100%, no divergence column. This is the
  correct, boring result your original Phase 0 run produced — show it as success, not a bug.
- **Still running:** columns fill left→right as steps complete; metrics show "provisional."
- **Empty:** "no runs yet — `murmur run … --n 30`".

---

## Out of scope for Phase 2 (resist these)

- **Acting on divergence** (escalation, repair, stronger judge) → that's Phase 3. Phase 2
  *names* the divergence step; Phase 3 *fires* on it.
- **The cost-aware judge cascade** → Phase 3.
- **Failure classification beyond pass/fail/error** → Phase 4 (`schema_mismatch`, etc.).
- Semantic-equivalence clustering tuned per agent → ship `exact_match` now, leave the hook.

---

## Phase 2 exit checklist

- [ ] Metrics replaced: `pass@1` + Wilson CI, both `pass^k` estimators, decay curve, N=30 default.
- [ ] Fan + metrics computed from real recorded trajectories; `stochastic` moved to `tests/`.
- [ ] Agreement/divergence algorithm implemented with pluggable similarity (exact default).
- [ ] Zone 1 / 2 / 3 UI built; all four states handled.
- [ ] Lane click → Phase 1 trace; divergence-column click → filtered set.
- [ ] Same seed reproduces the exact distribution.
- [ ] Tests green (metric math, estimator gap, divergence detection, reproducibility); `ruff` clean.
- [ ] **First Phase 2 commit** — defensible version, no placeholders, no invented numbers.

---

## Then: Phase 3 (the hand-off)

Phase 2 leaves you with a *named* divergence step and a working notion of run agreement.
Phase 3 turns that signal into **action**:

- The **variance gate** fires exactly at the divergence step Phase 2 detects — instead of
  letting diverged trajectories march into a doomed long-horizon run, escalate there.
- The **cost-aware judge cascade** (Tier 0 deterministic → Tier 1 convergence → Tier 2
  LLM-judge only on disagreement) reuses Phase 2's agreement notion as its Tier 1.
- The **escalation state machine** completes: `RUN_CHEAP → converged? → DONE` else
  `→ ESCALATE → (repair / stronger judge / human gate) → RE-EVAL`.

Phase 3's headline artifact is a measured number: *the cascade costs X% of judge-every-run
at equal accuracy* — the cost story that makes the harness one teams keep switched on.
