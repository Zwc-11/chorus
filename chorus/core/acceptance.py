"""Structured acceptance checks for tasks without a fixed ``expected_output``."""

from __future__ import annotations

import re

from chorus.core.agent_tasks import HARD_WEBSITE_ACCEPTANCE
from chorus.core.types import ContractDiagnostic, TaskSpec


def task_accepts(task: TaskSpec, output: str) -> bool:
    return not task_diagnostics(task, output)


def task_diagnostics(task: TaskSpec, output: str) -> tuple[ContractDiagnostic, ...]:
    if task.expected_output is not None:
        if output.strip() == task.expected_output.strip():
            return ()
        return (
            ContractDiagnostic(
                predicate_id="expected_output_mismatch",
                severity="error",
                message="Output did not match the deterministic expected output.",
                evidence="normalized string comparison failed",
                repair_hint="Return the exact output required by the task contract.",
            ),
        )
    acceptance = task.metadata.get("acceptance")
    if acceptance == HARD_WEBSITE_ACCEPTANCE:
        return hard_website_diagnostics(output)
    return ()


def hard_website_accepts(output: str) -> bool:
    return not hard_website_diagnostics(output)


def hard_website_diagnostics(output: str) -> tuple[ContractDiagnostic, ...]:
    text = output.lower()
    diagnostics: list[ContractDiagnostic] = []
    if "chorus" not in text:
        diagnostics.append(
            _diagnostic(
                "missing_brand_chorus",
                "The artifact does not contain the product name.",
                "searched for the word 'chorus'",
                "Include the product name in visible page copy.",
            )
        )
    if not re.search(r"<!doctype\s+html|<html\b", text, re.I):
        diagnostics.append(
            _diagnostic(
                "missing_html_document",
                "The artifact does not look like an HTML document.",
                "no doctype or <html> tag found",
                "Provide a complete semantic HTML document.",
            )
        )
    if 'id="metrics"' not in text and "id='metrics'" not in text:
        diagnostics.append(
            _diagnostic(
                "missing_dom_id_metrics",
                "The metrics element is missing the required id.",
                "no id=\"metrics\" or id='metrics' attribute found",
                "Add a metrics element with the required id.",
            )
        )
    if "pass@1" not in text:
        diagnostics.append(
            _diagnostic(
                "missing_metric_pass_at_1",
                "The metrics strip is missing pass@1.",
                "searched for literal pass@1",
                "Include the single-run pass-rate metric required by the task.",
            )
        )
    if "pass^" not in text:
        diagnostics.append(
            _diagnostic(
                "missing_metric_pass_hat_k",
                "The metrics strip is missing the pass-horizon notation.",
                "searched for a pass^ metric",
                "Use the task's pass-horizon notation in the metrics strip.",
            )
        )
    if "variance" not in text:
        diagnostics.append(
            _diagnostic(
                "missing_metric_variance",
                "The metrics strip is missing variance.",
                "searched for literal variance",
                "Include a variance metric in the metrics strip.",
            )
        )
    if "e8192a" not in text:
        diagnostics.append(
            _diagnostic(
                "missing_accent_red",
                "The required accent color is missing.",
                "searched for hex color e8192a",
                "Use the specified accent color in the page styles.",
            )
        )
    if "<html" not in text:
        diagnostics.append(
            _diagnostic(
                "missing_html_root",
                "The HTML root element is missing.",
                "no <html> tag found",
                "Wrap the page in an html root element.",
            )
        )
    if "styles.css" not in text and "<style" not in text:
        diagnostics.append(
            _diagnostic(
                "missing_css",
                "No linked or embedded CSS was found.",
                "no styles.css link or <style> block found",
                "Provide the required stylesheet or embedded CSS.",
            )
        )
    return tuple(diagnostics)


def repair_feedback(diagnostics: tuple[ContractDiagnostic, ...]) -> str:
    """Compact neutral feedback suitable for a bounded repair turn."""

    if not diagnostics:
        return "No contract diagnostics."
    return "\n".join(
        f"- {diagnostic.predicate_id}: {diagnostic.repair_hint or diagnostic.message}"
        for diagnostic in diagnostics
    )


def _diagnostic(
    predicate_id: str, message: str, evidence: str, repair_hint: str
) -> ContractDiagnostic:
    return ContractDiagnostic(
        predicate_id=predicate_id,
        severity="error",
        message=message,
        evidence=evidence,
        repair_hint=repair_hint,
    )
