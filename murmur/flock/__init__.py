"""Flock — Murmur's self-writing workflow engine.

The spine is a compiler/interpreter: a planner compiles a natural-language task
into a typed :class:`~murmur.flock.ir.WorkflowPlan` (the IR), and an executor
interprets that plan by spawning many small, isolated subagents — fanning out,
running tournaments, and adversarially verifying — on cheap models.

This subpackage hangs off the existing Murmur harness without disturbing it:

- :mod:`murmur.flock.ir`       — the typed plan and its schema/DAG validation.
- :mod:`murmur.flock.gateway`  — the ``ModelPort`` seam, budget ledger, metering.
- :mod:`murmur.flock.adapters` — concrete model adapters (fake, DeepSeek, Ollama).
- :mod:`murmur.flock.models`   — map an IR ``model`` string to a built adapter.
"""

from __future__ import annotations

__all__ = ["__doc__"]
