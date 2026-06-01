"""Benchmark task suites and the scaffold runner used by the regression gate.

A *suite* is a set of tasks run under fixed conditions. A *scaffold* is the
agent/harness strategy Chorus drives -- the thing the headline number varies while
holding the model and tasks constant. Two loaders sit behind ``load_suite``: the
deterministic synthetic suite (so the gate machinery is demonstrable end to end at
zero model cost) and the real ``swe-bench-verified`` loader in
:mod:`chorus.benchmarks.swebench`. The built-in scaffold is backed by the seeded
``StochasticAgent``; turning the SWE-bench task specs into a real ``pass^k`` needs
a real model behind ``AgentPort`` plus the SWE-bench test evaluator -- the gate
refuses to fake that with the stochastic scaffold.
"""
