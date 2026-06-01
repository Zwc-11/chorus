# Chorus — Phase 5 Build Instruction (Productize + Headline Number)

> Build-ready spec. Companion to the Phase 1–4 build docs.
> Goal: make Chorus **droppable into a real workflow** (a GitHub Action that blocks PRs on
> *statistically real* regressions) and produce **the one number** — a `pass^k` delta on a
> public benchmark, attributable to the harness alone. This is where the project stops being
> a local demo and becomes a résumé line an interviewer can't wave away.

---

## Dependency gate (read first)

**Do not run the benchmark half of this phase until Phase 4 is clean.** Specifically: the
`nondeterministic_loop` detector must be tightened (require repeated signature **and** no
state change between repeats) and the Phase 4 confusion matrix must be re-verified. The
benchmark `pass^k` and the per-class regression breakdown both inherit the classifier's
labels — if the classifier mislabels, the headline number and the PR comments are quietly
wrong. The Action half (regression statistics) does **not** depend on this and can be built
first.

---

## Definition of done (the exit criterion)

Done when **all** hold:

1. A **GitHub Action** runs the suite on a PR, posts a report comment, and sets pass/fail.
2. The gate uses a **paired-delta statistical test**: blocks only when the CI on
   `(candidate − baseline)` is entirely below zero; `improved` when entirely above;
   **`inconclusive` (does not block)** when it straddles zero.
3. A **baseline is persisted and versioned** (per branch/suite), and candidate runs compare
   against it on the **same task set, same N, same seed policy**.
4. The PR comment **breaks a regression down by failure class** (uses Phase 4 labels).
5. A **benchmark run** on a public set (SWE-bench Verified subset / Terminal-Bench) yields a
   `pass^k` number, with a documented "harness-only change" comparison.
6. Tests + `ruff` clean; the Action is demonstrated on a real test PR.

A gate that blocks on raw `pass^k` drops (no statistics) is **not done** — it will cry wolf
and get disabled, which is the exact failure this project exists to prevent.

---

## Locked decisions

- **Block on regression, not on threshold.** Never "fail if pass^k < 0.8" — always "fail if
  this PR made it *reliably worse* than baseline."
- **Paired comparison, same conditions.** Baseline and candidate run the same tasks, same N,
  same seed policy. Compare the *paired delta*, not two independent rates.
- **Three outcomes, not two:** `regressed` / `improved` / `inconclusive`. The third is what
  makes the gate trustworthy — it's honest when N is too small to tell.
- **Action wraps the CLI.** A thin composite action calling `chorus run`; no logic in YAML.
  Integrate, don't reinvent.
- **Benchmark uses a fixed subset for iteration**, one full run for the headline number.
- **The number is real or absent.** No placeholders, no synthetic-validated figure dressed
  up as a benchmark result.

---

## Build tasks (in order)

1. **Baseline store** — persist a run result keyed by `(branch, suite_version, N)`; load the
   comparison baseline for a candidate.
2. **Paired-delta regression test** — the statistics below; outputs
   `regressed | improved | inconclusive` + the delta CI.
3. **Reporter → PR comment** — Markdown: headline verdict, `pass^k` curve delta, cost delta,
   and the **per-failure-class breakdown** (Phase 4).
4. **GitHub Action** — composite action: checkout → `chorus run` → compare → comment → exit
   code. Inputs: `n`, `seed-policy`, `task-set`, `baseline-ref`.
5. **Benchmark adapter(s)** — SWE-bench Verified subset and/or Terminal-Bench task loader
   behind the existing `AgentPort`/task interfaces.
6. **The headline run** — full benchmark run; document the harness-only comparison.
7. **Tests + demo PR** — gate logic unit tests; one real PR showing each of the three verdicts.

---

## Regression statistics (the core — get this rigorous)

Per task, you have paired outcomes (baseline vs candidate, same task, same conditions). Two
defensible approaches; pick one and state it:

**Option A — bootstrap CI on the delta (recommended, distribution-free):**
```
For each task i: d_i = pass_k_candidate(i) − pass_k_baseline(i)   # paired per-task delta
Bootstrap B≈10k resamples of the task set → distribution of mean(d)
delta_ci = 2.5th and 97.5th percentile of that distribution

Verdict:
  regressed     if delta_ci_high < 0        # reliably worse
  improved      if delta_ci_low  > 0        # reliably better
  inconclusive  otherwise                    # CI straddles 0 → DO NOT BLOCK
```

**Option B — paired test (parametric alternative):** McNemar's test on per-task pass/fail
flips (built for paired binary outcomes), or a paired bootstrap on the rate. Same three-way
verdict from the resulting interval/p-value.

```python
def regression_verdict(baseline, candidate, k, n_boot=10000, seed=0):
    deltas = [pass_hat_k(candidate, t, k) - pass_hat_k(baseline, t, k)
              for t in shared_tasks(baseline, candidate)]
    lo, hi = bootstrap_ci(deltas, n_boot=n_boot, seed=seed)   # 95%
    if hi < 0:  return Verdict("regressed",   lo, hi)
    if lo > 0:  return Verdict("improved",    lo, hi)
    return       Verdict("inconclusive", lo, hi)              # gate does NOT block
```

**Why `inconclusive` matters:** with N=30 and CIs like `[0.63, 0.90]`, run-to-run noise will
move `pass^k` constantly. Blocking on every dip trains the team to ignore the gate. Blocking
only on a delta CI entirely below zero — and saying "inconclusive, widen N" otherwise — is
the same intellectual honesty as your two-estimator `pass^k` curve. It's the feature that
makes the gate survive contact with a real team.

**Determinism:** fix the bootstrap seed so the same inputs give the same verdict (a CI gate
that flickers is worse than none).

---

## The PR comment (what reviewers actually see)

```
## Chorus reliability gate — REGRESSED ❌
pass^5: 0.71 → 0.58   (Δ −0.13, 95% CI [−0.19, −0.07])   ← entirely below 0
cost/run: $0.038 → $0.041 (+8%)

New failures by class (vs baseline):
  +4  schema_mismatch      ← concentrated in step 5
  +1  tool_error
   0  context_drift

Top regressed tasks: #142, #88, #17   [trace links]
Baseline: main@a1671b5 · N=30 · seed-policy=per-lane
```

The per-class breakdown (from Phase 4) is what turns "your number went down" into "here's
*what* broke and *where*" — that's the output that reads as a real tool. `improved` and
`inconclusive` comments follow the same shape with different headers.

---

## Benchmark methodology (the headline number)

```
Goal: a pass^k delta attributable to the HARNESS, not the model.

1. Fix the model. Pick one model; do not change it across the comparison.
2. Vary ONLY the scaffold (the harness/agent strategy Chorus drives).
3. Run both on the SAME benchmark task subset, same N, same seed policy.
4. Report pass^k (curve), with Wilson CI, for each scaffold.
5. The claim:  "changing only the scaffolding moved pass^5 from X to Y on SWE-bench Verified."
```

- **Iterate on a fixed subset** (cost/time control); do **one** full run for the reported
  figure.
- **Hold everything else constant** — the entire credibility of the number is "only the
  harness changed." Document the diff.
- **Report reliability, not just capability** — `pass^k` and CI, not `pass@1`. That's the
  whole thesis, carried into the headline.

---

## UI / artifact specification

- **Regression report view:** baseline-vs-candidate `pass^k` curves overlaid, the delta CI
  band, the verdict badge (`regressed`/`improved`/`inconclusive`), cost delta, and the
  per-class failure breakdown. Links to regressed tasks' Phase 1 traces.
- **Benchmark result view:** the two-scaffold `pass^k` curves with CIs, and the one-line
  claim rendered as the headline.
- Cross-cutting rules unchanged: mono for machine values, colour encodes verdict/class only,
  round every number, no prose inside the widget.

---

## States to design

- **No baseline yet** (first run on a branch): record as baseline, verdict `baseline-set`,
  do not block.
- **Inconclusive:** comment says so, suggests widening N, **does not block**.
- **Improved:** celebrate, update baseline (on merge).
- **Benchmark run partial/failed** (a task harness errored): report partial, flag excluded
  tasks, never silently drop them from the denominator.

---

## Out of scope for Phase 5 (resist)

- **Real-world adoption / upstream repros** → Phase 6 (stretch).
- **Auto-tuning N or thresholds** → leave as configured inputs.
- **Multi-model leaderboards** → one model, one harness-diff, one number.
- A hosted dashboard / SaaS → Chorus stays library + CLI + Action.

---

## Phase 5 exit checklist

- [ ] Baseline store: persist + load per `(branch, suite, N)`.
- [ ] Paired-delta regression test with `regressed/improved/inconclusive`; seeded, deterministic.
- [ ] GitHub Action wraps `chorus run`, comments, sets exit code; demoed on a real PR.
- [ ] PR comment breaks regressions down by Phase 4 failure class.
- [ ] **Phase 4 loop-detector fix landed + confusion matrix re-verified** (dependency gate).
- [ ] Benchmark adapter loads SWE-bench Verified subset / Terminal-Bench.
- [ ] Headline run done; harness-only comparison documented; `pass^k` + CI reported.
- [ ] Tests green; `ruff` clean.

---

## The résumé line (only once the number is real)

> "Built Chorus, an open-source reliability harness for coding agents: runs each task N
> times, records every step as a replayable OpenTelemetry trace, judges outcomes with a
> cost-aware cascade (20% the cost of judging every run at equal accuracy), classifies
> failures, and gates CI on *statistical* regression. Changing only the scaffolding moved
> pass^5 from X% to Y% on SWE-bench Verified."

Fill X and Y with measured figures. Until then the sentence stops at "statistical
regression" — never ship the benchmark clause with placeholders.

---

## Then: Phase 6 (stretch hand-off)

With a real number and a working gate, Phase 6 is adoption: run Chorus on 2–3 popular
open-source agent repos, find a genuine reliability/cost cliff, file a reproduction upstream,
and demo the LangSmith-MCP self-debug loop on a real Chorus bug. A named external user or an
acted-on upstream issue is the single strongest interview signal — but the project is already
defensible without it.
