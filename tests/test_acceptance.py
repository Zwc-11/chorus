"""Hard-task acceptance and DeepSeek v4 request shaping."""

from __future__ import annotations

from chorus.core.acceptance import hard_website_accepts, hard_website_diagnostics, repair_feedback
from chorus.core.agent_tasks import hard_website_task, load_agent_task


def test_hard_website_accepts_valid_bundle() -> None:
    artifact = """=== index.html ===
<!DOCTYPE html>
<html><body><h1>chorus</h1><section id="metrics">pass@1 pass^k variance</section></body></html>
=== styles.css ===
:root { --bg: #e4e4e0; --accent: #e8192a; }
"""
    assert hard_website_accepts(artifact)


def test_hard_website_rejects_missing_metrics() -> None:
    assert not hard_website_accepts("<html><body>chorus #e8192a</body></html>")


def test_hard_website_diagnostics_name_missing_predicates() -> None:
    diagnostics = hard_website_diagnostics(
        '<!DOCTYPE html><html><body><h1>chorus</h1><section id="metrics">'
        "pass@1 pass@k variance</section></body></html><style>:root{--accent:#e8192a}</style>"
    )
    ids = {diagnostic.predicate_id for diagnostic in diagnostics}

    assert ids == {"missing_metric_pass_hat_k"}
    assert "missing_metric_pass_hat_k" in repair_feedback(diagnostics)


def test_task_spec_uses_acceptance() -> None:
    task = hard_website_task()
    assert task.accepts(
        """<!DOCTYPE html><html><body><h1>chorus</h1>
        <div id="metrics">pass@1 pass^k variance</div></body></html>
        <style>:root { --accent: #e8192a; }</style> index.html styles.css"""
    )


def test_load_agent_task_hard_default(monkeypatch) -> None:
    monkeypatch.delenv("CHORUS_TASK", raising=False)
    monkeypatch.setenv("CHORUS_TASK", "hard")
    task = load_agent_task()
    assert task.task_id == "hard.landing_site"


def test_deepseek_v4_completion_kwargs() -> None:
    from chorus.benchmarks.swe.model import DeepSeekPatchModel

    model = DeepSeekPatchModel(
        model="deepseek-v4-pro",
        api_key="test",
        reasoning_effort="high",
        thinking_enabled=True,
    )
    model._client = object()  # skip network
    kwargs = model._completion_kwargs(system="s", user="u", max_tokens=100)
    assert kwargs["model"] == "deepseek-v4-pro"
    assert kwargs["reasoning_effort"] == "high"
    assert kwargs["extra_body"] == {"thinking": {"type": "enabled"}}
    assert "temperature" not in kwargs
