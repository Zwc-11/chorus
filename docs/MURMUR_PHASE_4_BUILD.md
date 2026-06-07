# Murmur — Phase 4 Build Instruction (Diagnosis)

> Build-ready spec. Companion to the Phase 1–3 build docs.
> Goal: when a run fails, say **where** and **why** — automatically and replayably. Turn
> "FAIL" into "`schema_mismatch @ step 5`, here's the deterministic reproduction."

---

## Definition of done (the exit criterion)

Done when **all** hold:

1. Every step boundary is **contract-checked** against a typed I/O schema; a violation is
   recorded with the exact step and offending field.
2. Each failed trajectory is **auto-classified** into the taxonomy below, written as
   `murmur.failure.class` + `murmur.failure.step` on its span.
3. The classifier is **deterministic-first** — rule-based detectors resolve the clear cases;
   an LLM classifier is only the fallback for residual `unknown`.
4. A failed run shows **where + why + replay**: the class, the exact step, the violation
   detail, and a one-click "replay from step k−1".
5. The classifier is **validated on injected failures** (known ground truth): per-class
   precision / recall / F1 and a confusion matrix.
6. Tests + `ruff` clean.

The classifier number is only meaningful against **injected, ground-truth** failures — not
self-labelled ones. That's the rigor that makes it defensible.

---

## Locked decisions

- **Contract checks at step boundaries.** Typed I/O schemas (the dominant real-world failure
  mode is schema misalignment between steps — catch it *at the boundary*, name the step).
- **Closed failure taxonomy** (extensible, but a fixed core): `tool_error`,
  `schema_mismatch`, `context_drift`, `nondeterministic_loop`, `budget_exceeded`,
  `timeout`, `contract_violation`, and `unknown`.
- **Classifier is Judicial** — reads the record only, never re-runs the agent.
- **Deterministic-first, LLM-fallback** — mirrors the Phase 3 cascade philosophy: cheap,
  unambiguous detectors first; expensive LLM classifier only on residual `unknown`.
- **Reuse Phase 2 `step_signature`** for loop detection (repeated identical action signatures).

---

## Build tasks (in order)

1. **Inter-step contract layer.** Per-step `input_schema` / `output_schema` (pydantic /
   JSON Schema) declared in the Constitution; validate at each boundary; emit
   `contract_check{result, step, field, expected, got}` events.
2. **Failure taxonomy + rule-based detectors** (each reads the trace).
3. **Classifier orchestration** — Chain of Responsibility: detectors in priority order,
   first decisive match wins; residual → LLM fallback → else `unknown`.
4. **Write-back** — `murmur.failure.class` + `murmur.failure.step` onto spans; surface in
   the Phase 1 trace UI and the Phase 2 overlay.
5. **Diagnosis UI** — failure breakdown + per-failure "where + why + replay" panel.
6. **Validation harness** — inject known failures, measure per-class precision/recall/F1.

---

## Inter-step contract checks (concrete)

```python
def check_step_boundary(step, contract):
    """Validate a step's actual I/O against its declared schema."""
    issues = []
    if not contract.input_schema(step.index).validate(step.input):
        issues.append(("input", contract.input_schema(step.index).first_error(step.input)))
    if not contract.output_schema(step.index).validate(step.output):
        issues.append(("output", contract.output_schema(step.index).first_error(step.output)))
    for side, err in issues:
        emit(ContractCheck(result="fail", step=step.index, field=err.field,
                           expected=err.expected, got=err.got, side=side))
    return not issues
```

A failed boundary check is the single most useful signal you can produce: it pinpoints
`schema_mismatch` at an exact step and field, instead of a generic downstream crash.

---

## Failure taxonomy + detectors

Each detector reads the recorded trace and returns a match or passes. Run in this priority
order (most certain → least):

| Class | Detector (reads the record) |
|---|---|
| `tool_error` | a `tool_call` span has non-zero exit / error status |
| `schema_mismatch` | a `contract_check` failed at a step boundary (typed I/O mismatch) |
| `budget_exceeded` | cumulative cost / tokens over the Constitution budget |
| `timeout` | a step exceeded its wall-clock limit |
| `nondeterministic_loop` | same `step_signature` repeats > N times with no state change (cycle detection on the action sequence) |
| `contract_violation` | run "completed" but the acceptance predicate is false |
| `context_drift` | *heuristic, lower confidence*: agent references state not present, contradicts/repeats earlier steps, or successive-step context similarity drops below threshold |
| `unknown` | nothing matched → LLM fallback → else `unknown` (never guess) |

```python
def classify(traj, contract, policy):
    for detector in DETECTORS_IN_PRIORITY:        # deterministic, cheap
        hit = detector(traj, contract)
        if hit:
            return Failure(cls=hit.cls, step=hit.step, detail=hit.detail, confidence=1.0)
    llm = llm_classify(traj, contract.taxonomy)   # fallback only, expensive
    return llm if llm.confidence >= policy.min_conf else Failure(cls="unknown", step=None)
```

**Note on `context_drift`:** it's the one genuinely fuzzy class. Mark its confidence < 1.0
and let the LLM fallback confirm. Don't pretend a heuristic is certain — flagging
low-confidence is itself the mature move.

---

## Validation methodology (the rigor that makes the number real)

Self-labelled failures prove nothing. **Inject** failures with known ground truth:

```
Fixture construction (each fixture has a KNOWN label):
  tool_error            → a tool stub that returns exit 1
  schema_mismatch       → a step that emits output violating its schema
  budget_exceeded       → a Constitution budget set deliberately low
  nondeterministic_loop → an agent stub that repeats one action
  timeout               → a tool that sleeps past the limit
  context_drift         → an agent stub that references absent state

Run the classifier over the fixture set, then report:
  per-class precision / recall / F1
  a confusion matrix (what gets mislabelled as what)
```

The confusion matrix is the honest artifact — it shows, e.g., that `context_drift` is
sometimes mislabelled `unknown`, which is exactly the kind of limitation you *want* to state
rather than hide.

---

## UI specification (zones to add)

- **Failure breakdown:** extend the Phase 2 `failures` card into a small bar — counts per
  class. Click a class → filter to those trajectories.
- **Per-failure panel ("where + why + replay"):** the class, the exact step
  (`murmur.failure.step`), the contract-violation detail (field / expected / got), and a
  **"replay from step k−1"** button (reuses Phase 0 replay) so the failure is reproducible
  in one click.
- **In the divergence overlay:** annotate diverged-then-failed lanes with their class, so
  the overlay shows not just *where* runs split but *why* the splits died.
- **In the Phase 1 trace:** the failed span already carries `murmur.failure.class` in the
  inspector (the schema hook from Phase 1).
- Cross-cutting rules unchanged: mono for machine values, colour encodes class/outcome only,
  round numbers, no prose inside the widget.

---

## States to design

- **`unknown`** — detector miss *and* LLM fallback unsure: label `unknown`, don't guess.
  Honest beats confident-and-wrong.
- **Multiple candidate classes** — report a primary + secondary with confidences (a tool
  error can cascade into a schema mismatch downstream; show both, primary = root cause).
- **No failures** — clean run: breakdown empty, panel shows "all trajectories passed".
- **Low-confidence class** (`context_drift`): render with a visible confidence indicator.

---

## Out of scope for Phase 4 (resist)

- **Fixing** failures automatically — the `repair` action lives in Phase 3; broader
  auto-repair is beyond this project's scope.
- **Production alerting / monitoring** over many runs → Phase 5 and beyond.
- **Per-agent tuned drift models** → ship the heuristic + LLM fallback; leave the hook.

---

## Phase 4 exit checklist

- [ ] Inter-step contract checks emit `contract_check` events with step + field.
- [ ] Taxonomy + deterministic detectors in priority order; LLM fallback for residual only.
- [ ] `murmur.failure.class` + `murmur.failure.step` written onto spans and surfaced in UI.
- [ ] Per-failure "where + why + replay" works; replay-from-step reproduces the failure.
- [ ] Validation on **injected** failures: per-class precision/recall/F1 + confusion matrix.
- [ ] `context_drift` and `unknown` carry honest confidence indicators.
- [ ] Tests green; `ruff` clean.

---

## Then: Phase 5 (hand-off)

With reliability (Ph2), judgment (Ph3), and diagnosis (Ph4) in place, Phase 5 productizes:
the **GitHub Action** blocks PRs on regression vs baseline, and the **regression report can
break a regression down by failure class** ("this PR added 4 new `schema_mismatch`
failures"). Then point it at SWE-bench Verified / Terminal-Bench for the headline `pass^k`
number — the resume line, with real figures and no placeholders.
