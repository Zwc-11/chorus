# GitHub Action

Murmur gates pull requests on statistical reliability regression instead of a
single flaky run.

## Minimal Workflow

Run `murmur init` to generate a starter workflow, or add this by hand:

```yaml
name: Murmur reliability gate

on:
  pull_request:
  workflow_dispatch:

jobs:
  murmur:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: python -m pip install -e ".[dev]"
      - run: murmur gate --suite synthetic --n 20 --k 5
```

## Baselines

Run the same command on the base branch with `--update-baseline` to persist the
baseline. Candidate PRs compare against the stored baseline on the same suite,
N, seed policy, and branch.

```bash
murmur gate --suite synthetic --n 20 --k 5 --update-baseline
```

The gate exits non-zero only when the bootstrap confidence interval for the
candidate-vs-baseline `pass^k` delta is entirely below zero. `improved` and
`inconclusive` do not block.

## Real Benchmarks

Real SWE/Terminal-style benchmarks need a model key, Docker, and optional
benchmark dependencies. Murmur refuses to emit public numbers unless the real
judge ran.

```bash
python -m pip install -e ".[bench]"
murmur bench --subset 50 --n 10 --k 5
```
