# Murmur — Code Review Report

Full-codebase review pass, June 12, 2026. Every Python module in `chorus/` was
read or pattern-swept; real bugs were fixed in place. Suite: 163/163 passing,
ruff clean.

## Bugs found and fixed

### 1. Recursive worktree copy when the run directory lives inside the repo (high)
`chorus/adapters/sandboxes/local_worktree.py` — `_copy_tree` copied the
repository into the sandbox worktree, including the in-progress run directory
itself whenever `--out-dir` pointed inside the repo (anything other than the
ignored `.chorus/`). The copy nested worktrees inside worktrees until the OS
rejected the path length. The default `.chorus/runs` masked this; `--out-dir
./runs` reproduced it instantly. Fixed: the copy now skips any directory that
is the destination worktree or one of its ancestors.

### 2. `python -m chorus.cli` silently did nothing (medium)
`chorus/cli.py` had no `if __name__ == "__main__"` block, so running the module
directly imported it and exited 0 — looking like success while doing nothing.
Fixed with a main guard; `python -m chorus.cli` now behaves like the installed
`murmur` command. (This is also what surfaced bug #1.)

### 3. Schema validator accepted booleans as integers/numbers (low)
`chorus/core/schema.py` — `isinstance(True, int)` is true in Python, so a step
contract requiring `"type": "integer"` accepted `True`. JSON Schema treats
boolean and number as distinct types. Fixed with an explicit bool guard.

### 4. Diff size counted file headers as changed lines (low)
`chorus/application/verifier.py` — `diff_lines` counted `+++`/`---` header
lines, inflating every patch by ~4 lines per file and biasing both the
`max_diff_lines` contract check and tournament ranking against multi-file
patches. Fixed: only real `+`/`-` content lines count.

## Wording / polish

- `chorus/config/env.py`: repaired a mis-indented docstring line.
- Remaining user-facing "Chorus" strings in proof outputs and report pages
  rebranded to Murmur (`proof.md` header, report titles, HUD footer).

## Reviewed clean (no findings)

`core/`: conductor, events, types, metrics (Wilson CI and unbiased pass^k math
verified), regression (seeded bootstrap), divergence, judge cascade, classify,
acceptance, suite, results, agent_tasks, ports, model_port.
`domain/`: contract, policy, workflow, tool, verification, proof.
`application/`: workflow_runtime, workflow_planner, fix_test, tournament,
contract_compiler, event_log, proof_builder.
`gateway/`: tool_gateway (record/replay divergence detection is sound).
`adapters/`: models (openai_compatible, ollama, fake), agents (contract_lite,
murmur_patch, fake, stochastic, registry), sandboxes, storage (jsonl, memory,
baseline), tools (contract_proxy).
`trace/`: spans, emit, mapper.
Pattern sweeps over `benchmarks/`, `adapters/trace/`, and `report/` (mutable
default args, bare excepts, swallowed exceptions, float equality, unclosed
files, bool comparisons): no hits.

## Also shipped in this pass

- **Proof package (Milestone 6)**: every run now writes `proof.md` (leads with
  Winner / Selected-by / Attempts / Cost), `winner.patch`, `fan.html` (one lane
  per attempt, winner highlighted, tie-break rationale), `cost.json`,
  `report.html` (verdict banner + headline stat cards + links to all
  artifacts), alongside the existing contract/workflow/events evidence.
- **CLI demo block**: `murmur fix-test` ends with the Verdict / Winner /
  Reason / Attempts / Cost summary plus direct paths to proof, patch, fan, and
  report.
- Verified end to end: a scripted-agent `fix-test` run on a demo repo produces
  the full 16-artifact run directory and exits green.

## Known limitations (intentional, room to grow)

- The workflow runtime executes nodes sequentially; `concurrency` is accepted
  but parallel DAG scheduling is future work.
- The LLM planner (Mode B) and candidate-workflow tournament (Mode C) are not
  built yet; the template planner covers the demo paths.
- `ChorusLiteAgent` still uses the legacy sync `PatchModel` seam; new work
  should target `MurmurPatchAgent` on `ModelPort`.
