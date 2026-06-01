"""Benchmark task suites and the scaffold runner used by the regression gate.

A *suite* is a set of tasks run under fixed conditions. A *scaffold* is the
agent/harness strategy Chorus drives -- the thing the headline number varies while
holding the model and tasks constant. Today the only scaffold is backed by the
seeded ``StochasticAgent`` so the gate machinery is demonstrable end to end; a
real benchmark (SWE-bench Verified, Terminal-Bench) plugs in behind the same
``load_suite`` / ``Scaffold`` seam, against a real model.
"""
