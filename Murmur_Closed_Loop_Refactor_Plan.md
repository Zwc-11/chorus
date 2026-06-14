# Murmur Closed-Loop Refactor Plan

## Purpose

This plan reconciles the Murmur architecture with the current Chorus codebase.

The Murmur draft defines the ambitious workflow runtime:

```text
task -> planner -> typed workflow IR -> DAG scheduler -> reusable operators
```

The current Chorus pivot defines the product surface:

```text
task / failing test -> contract -> sandboxed agent -> verification -> proof package
```

The refactor should join them without throwing away either side:

```text
Chorus = contract and proof layer for AI-generated code changes
Murmur = self-writing workflow runtime inside Chorus
```

The immediate showcase should be closed-loop autonomous coding, not resume
ranking. Ranking proves the harness can compare. Closed-loop coding proves the
harness can do work, run objective checks, and repair until the artifact works.

## Target Showcase

```text
Input:
  - a software-change spec or failing test command
  - objective tests
  - a budget

Workflow:
  generate   map, K parallel attempts
  exec       run tests for each attempt in its isolated sandbox
  fix        loop failed attempts with test output as feedback
  rank       choose the cleanest passing candidate
  verify     run contract/diff/static checks on the winner
  report     emit final diff, proof package, and trace

Output:
  - final working code diff
  - per-candidate test results
  - repair history
  - winner selection rationale
  - Chorus proof package
```

This keeps the objective signal deterministic: tests pass or they do not. LLMs
can generate, repair, and review, but they do not get to decide whether the code
works.

## Current State

Already useful:

- `murmur/application/fix_test.py` runs the single-candidate failing-test flow.
- `LocalWorktreeSandbox` already creates an isolated worktree/copy and runs
  subprocess commands.
- `ContractToolProxy` already enforces policy before tool execution.
- `PolicyEngine` already gates file reads, file writes, shell/test commands,
  denied tools, and budget counters.
- `verify_contract` already reruns target tests, related tests, static checks,
  diff limits, and forbidden-file checks.
- `RunConductor` already knows how to fan out trajectories and record events,
  but it belongs to the older reliability harness path.

Main gap:

- The current contract-first path has one sandbox, one agent run, one final
  verification. It does not yet have a workflow IR, a DAG scheduler, or a
  first-class exec node that can sit between generation and repair.

## Architectural Decision

Add `exec` as a workflow operator, not as an unrestricted agent tool.

Agent tools should stay contract-limited:

```text
list_files, search, read_file, apply_patch, run_test, git_diff, finish
```

The workflow runtime should get a deterministic `exec` node:

```yaml
id: test_candidate
op: exec
inputs: [candidate_patch]
params:
  command: "python -m pytest tests/test_checkout.py -q"
  success:
    returncode: 0
  parser: pytest
```

The `exec` operator uses the same sandbox, policy, budget, and event log
primitives as the contract tool proxy. It is not a shortcut around the contract.

## Proposed Module Layout

Keep the domain/application split already in the repo:

```text
murmur/domain/workflow.py
  WorkflowPlan
  WorkflowNode
  WorkflowEdge
  NodeResult
  CandidateArtifact
  ExecResult

murmur/application/workflow_runtime.py
  WorkflowRuntime
  async DAG scheduler
  per-node event emission
  dependency resolution
  semaphore/concurrency limit

murmur/application/operators/
  generate.py
  exec.py
  loop.py
  rank.py
  verify.py
  report.py

murmur/application/coding_workflow.py
  compile_fix_test_workflow(contract, n, max_repairs)
  run_closed_loop_fix_test(...)

murmur/report/closed_loop_md.py
  winner summary
  attempt table
  repair trace
  objective test evidence
```

The first implementation can keep operators small and direct. The important
thing is the type boundary and event trail, not building a general-purpose graph
engine on day one.

## Workflow IR Changes

Extend the Murmur operator list from:

```text
classify | map | reduce | tournament | verify | filter | loop
```

to:

```text
classify | map | generate | exec | loop | filter | tournament | verify | reduce | report
```

`generate` can be a specialized `map` for candidate artifacts. Keeping it named
separately in the first coding workflow makes traces easier to read.

Minimal schema:

```yaml
version: 1
goal: "Fix failing test"
budget:
  max_cost_usd: 0.50
  max_runtime_seconds: 600
  max_candidates: 5
  max_repairs_per_candidate: 3
nodes:
  - id: generate
    op: generate
    inputs: []
    params:
      n: 5
      agent: chorus-lite

  - id: run_tests
    op: exec
    inputs: [generate]
    params:
      command: "python -m pytest tests/test_checkout.py -q"
      parser: pytest

  - id: repair
    op: loop
    inputs: [run_tests]
    params:
      until: "passed"
      max_iterations: 3
      feedback: "test_output"

  - id: rank
    op: tournament
    inputs: [repair]
    params:
      eligible: "passed"
      primary_score: "diff_size"

  - id: verify
    op: verify
    inputs: [rank]
    params:
      contract_checks: true

  - id: report
    op: report
    inputs: [verify]
```

For MVP, this plan can be compiled by Python from a `Contract`. The self-writing
planner can come later.

## Exec Operator Contract

Input:

```python
ExecCommand(
    command: str,
    cwd: str,
    timeout_s: int,
    parser: Literal["generic", "pytest", "backtest"],
)
```

Output:

```python
ExecResult(
    command: str,
    returncode: int,
    passed: bool,
    stdout: str,
    stderr: str,
    timeout: bool,
    latency_ms: float,
    summary: str,
    failing_tests: tuple[str, ...],
)
```

For pytest, `failing_tests` can start as a lightweight parser over terminal
output. It does not need to be perfect; the raw stdout/stderr remains the
ground-truth feedback.

## Candidate Isolation

The current `run_fix_test` creates one sandbox for the whole run. Closed-loop
Murmur needs one sandbox per candidate:

```text
.murmur/runs/<run_id>/
  contract.yaml
  workflow.yaml
  events.jsonl
  attempts/
    attempt_1/
      worktree/
      events.jsonl
      test_initial.json
      repair_1.json
      diff.patch
    attempt_2/
      ...
  winner/
    diff.patch
    proof.md
  proof.md
  report.html
  summary.json
```

Each candidate must have:

- its own worktree
- its own tool budget slice
- its own event stream or trajectory id
- its own final diff
- its own verification result

The shared run-level event log should only aggregate structural events and
winner selection.

## Refactor Steps

### Step 1: Extract attempt execution

Create a reusable attempt runner around the current single-candidate logic:

```text
run_attempt(contract, attempt_id, agent, sandbox, events, feedback) -> AttemptResult
```

Done when:

- `run_fix_test` still behaves exactly as today for one attempt.
- tests around `fix-test` continue to pass.
- attempt result includes summary, diff, verification, model/tool cost, and
  test output.

### Step 2: Add structured exec results

Move subprocess result normalization into a domain-level `ExecResult`.

Done when:

- `ContractToolProxy.run_test` returns the same behavior as today, but through a
  reusable result shape.
- `verify_contract` can use the same result shape internally.
- tests cover passing command, failing command, and timeout.

### Step 3: Add the workflow IR

Add typed `WorkflowPlan` and `WorkflowNode` records plus YAML read/write.

Done when:

- a hand-written fix-test workflow validates and round-trips through YAML.
- invalid node references and unsupported ops fail validation.
- no planner or self-writing behavior is required yet.

### Step 4: Add the minimal workflow runtime

Implement a small async runtime that resolves node dependencies and dispatches
operator handlers.

Done when:

- a hand-written plan can run `generate -> exec -> report`.
- node start/finish events are emitted.
- concurrency is capped by a semaphore.

### Step 5: Implement closed-loop fix-test

Compile a failing-test contract into the fixed workflow:

```text
generate K candidates -> exec tests -> repair failed candidates -> rank passers -> verify winner
```

Done when:

- `murmur fix-test --n 5 --max-repairs 3 ...` works.
- `--n 1 --max-repairs 0` is equivalent to the old path.
- failing test output is fed back into the repair prompt.
- candidates that never pass are kept in the report but excluded from winner
  selection.

### Step 6: Rank deterministically first

Rank passing candidates by objective and reviewable signals before using an LLM
judge:

```text
1. verification passed
2. fewer changed files
3. fewer diff lines
4. target test latency
5. related/static checks passed
```

Only after this works should a tournament LLM judge be added as an optional
tiebreaker.

### Step 7: Add adversarial verify

Keep the deterministic verifier as the primary judge. Add an LLM verifier only
as a review memo:

```text
spec consistency
edge cases
unnecessary edits
hidden risk
```

The LLM verifier can flag concerns, but it cannot override failing tests or
policy violations.

## CLI Shape

Prefer evolving the current command instead of adding a separate public command:

```bash
murmur fix-test \
  --cmd "python -m pytest tests/test_checkout.py -q" \
  --agent chorus-lite \
  --n 5 \
  --max-repairs 3 \
  --budget 0.50
```

Optional dev-only command while building:

```bash
murmur workflow run .murmur/workflows/fix-test.yaml
```

That keeps the public product simple while giving us a way to test the IR.

## Demo Order

### Demo 1: toy failing test

Use the existing checkout discount fixture style. Show:

- baseline test fails
- K candidates attempt independent fixes
- failed candidates receive test output
- at least one candidate passes
- winner is selected by objective criteria
- proof package contains the exact diff and test evidence

### Demo 2: real repo bug

Use a small open-source repo or a local fixture with multiple plausible fixes.
This proves ranking and diff discipline matter.

### Demo 3: strategy research

Only after the coding demo works, add a strategy-research workflow:

```text
idea -> generate K strategy implementations -> exec backtests -> repair survivors
     -> rank Sharpe/drawdown/PnL -> risk memo
```

This needs extra policy for network/data access and cached market data, so it
should not be the first refactor target.

## Risks

- Do not let `exec` become arbitrary shell access. It must route through policy.
- Do not let the LLM verifier become the source of truth. Tests and contract
  checks remain the source of truth.
- Do not mix candidate worktrees. Shared state would invalidate the ranking.
- Do not start with the self-writing planner. A hand-compiled workflow proves
  the runtime first.
- Do not rename everything to Murmur yet. The current Chorus positioning is
  stronger for the product; Murmur can be the runtime concept.

## First Implementation Slice

The smallest useful implementation is:

```text
1. Add ExecResult.
2. Extract one attempt runner from run_fix_test.
3. Run N isolated attempts sequentially.
4. Pick the first passing attempt deterministically.
5. Write an aggregate closed-loop report.
```

This already demonstrates closed-loop task completion. Parallel scheduling,
loop repair, tournament ranking, and self-writing plans can land after that
without changing the product promise.

