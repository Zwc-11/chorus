from __future__ import annotations


def classify_failure(error: BaseException | None) -> str | None:
    if error is None:
        return None
    error_name = error.__class__.__name__.lower()
    if "divergence" in error_name:
        return "nondeterministic_loop"
    if "timeout" in error_name:
        return "budget_exceeded"
    if "key" in error_name or "value" in error_name:
        return "schema_mismatch"
    return "tool_error"

