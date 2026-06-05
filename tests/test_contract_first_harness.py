"""Contract-first execution harness tests."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from chorus.adapters.sandboxes.local_worktree import LocalWorktreeSandbox
from chorus.adapters.tools.contract_proxy import ContractToolProxy
from chorus.application.contract_compiler import compile_fix_test_contract
from chorus.application.event_log import JsonlRunEventLog
from chorus.application.fix_test import compile_fix_test_workflow, validate_fix_test_workflow
from chorus.cli import app
from chorus.domain.contract import Contract
from chorus.domain.policy import BudgetState, PolicyEngine
from chorus.domain.tool import ToolRequest
from chorus.domain.workflow import WorkflowNode, WorkflowPlan


def test_contract_yaml_roundtrip_and_validation(tmp_path: Path) -> None:
    contract = compile_fix_test_contract(
        command="python -m pytest tests/test_checkout.py -q",
        repo_root=tmp_path,
        failure_output='File "checkout.py", line 2, in apply_discount',
        budget_usd=0.20,
    )
    path = tmp_path / "contract.yaml"

    contract.write(path)
    loaded = Contract.read(path)

    assert loaded == contract
    assert loaded.validate() == []
    assert loaded.budget.max_cost_usd == 0.20
    assert "checkout.py" in loaded.files.allow_read


def test_policy_enforces_files_shell_and_budget(tmp_path: Path) -> None:
    contract = compile_fix_test_contract(
        command="python -m pytest tests/test_checkout.py -q",
        repo_root=tmp_path,
    )
    budget = BudgetState(tool_calls=contract.budget.max_tool_calls)
    policy = PolicyEngine(contract, budget)

    assert not policy.evaluate(ToolRequest("read_file", {"path": ".env"})).allowed
    assert not policy.evaluate(ToolRequest("run_test", {"command": "curl example.com"})).allowed
    assert not policy.evaluate(ToolRequest("list_files", {"glob": "**/*"})).allowed

    budget.tool_calls = 0
    assert policy.evaluate(ToolRequest("read_file", {"path": "checkout.py"})).allowed
    assert policy.evaluate(
        ToolRequest("run_test", {"command": "python -m pytest tests/test_checkout.py -q"})
    ).allowed


def test_contract_create_and_check_cli(tmp_path: Path) -> None:
    runner = CliRunner()
    out = tmp_path / "task.yaml"

    created = runner.invoke(
        app,
        [
            "contract",
            "create",
            "--from-test",
            "python -m pytest tests/test_checkout.py -q",
            "--repo-root",
            str(tmp_path),
            "--out",
            str(out),
        ],
    )
    checked = runner.invoke(app, ["contract", "check", str(out)])

    assert created.exit_code == 0
    assert checked.exit_code == 0
    assert out.is_file()
    assert "Contract OK" in checked.output


def test_fix_test_scripted_agent_writes_pr_proof(tmp_path: Path) -> None:
    _write_checkout_repo(tmp_path)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "fix-test",
            "--cmd",
            "python -m pytest tests/test_checkout.py -q",
            "--repo-root",
            str(tmp_path),
            "--out-dir",
            str(tmp_path / ".chorus" / "runs"),
            "--agent",
            "scripted",
            "--budget",
            "0.20",
        ],
    )

    assert result.exit_code == 0, result.output
    run_dirs = list((tmp_path / ".chorus" / "runs").glob("run_*"))
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    assert (run_dir / "contract.yaml").is_file()
    assert (run_dir / "workflow.yaml").is_file()
    assert (run_dir / "events.jsonl").is_file()
    assert (run_dir / "diff.patch").is_file()
    assert (run_dir / "proof.json").is_file()
    assert (run_dir / "proof.md").is_file()
    assert (run_dir / "report.html").is_file()
    for node_id in (
        "reproduce",
        "generate",
        "run_tests",
        "repair",
        "rank",
        "verify",
        "report",
    ):
        assert (run_dir / "nodes" / node_id / "result.json").is_file()
    assert "Verdict: PASS" in (run_dir / "proof.md").read_text(encoding="utf-8")
    assert "checkout.py" in (run_dir / "diff.patch").read_text(encoding="utf-8")
    workflow = WorkflowPlan.read(run_dir / "workflow.yaml")
    assert workflow.validate() == []
    assert [node.op for node in workflow.nodes] == [
        "exec",
        "generate",
        "exec",
        "loop",
        "rank",
        "verify",
        "report",
    ]
    events = [
        json.loads(line)
        for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    finished_nodes = [
        event["payload"]["node_id"]
        for event in events
        if event["type"] == "workflow_node_finished"
    ]
    assert finished_nodes == [
        "reproduce",
        "generate",
        "run_tests",
        "repair",
        "rank",
        "verify",
        "report",
    ]


def test_fix_test_runs_multiple_isolated_attempts(tmp_path: Path) -> None:
    _write_checkout_repo(tmp_path)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "fix-test",
            "--cmd",
            "python -m pytest tests/test_checkout.py -q",
            "--repo-root",
            str(tmp_path),
            "--out-dir",
            str(tmp_path / ".chorus" / "runs"),
            "--agent",
            "scripted",
            "--n",
            "3",
        ],
    )

    assert result.exit_code == 0, result.output
    run_dir = next((tmp_path / ".chorus" / "runs").glob("run_*"))
    attempts = sorted((run_dir / "attempts").glob("attempt_*"))
    assert len(attempts) == 3
    assert (run_dir / "attempts.json").is_file()
    assert (run_dir / "winner" / "diff.patch").is_file()
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert len(summary["attempts"]) == 3
    assert any(attempt["passed"] for attempt in summary["attempts"])


def test_failed_attempt_keeps_exec_output_in_evidence(tmp_path: Path) -> None:
    _write_checkout_repo(tmp_path, unsupported_bug=True)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "fix-test",
            "--cmd",
            "python -m pytest tests/test_checkout.py -q",
            "--repo-root",
            str(tmp_path),
            "--out-dir",
            str(tmp_path / ".chorus" / "runs"),
            "--agent",
            "scripted",
        ],
    )

    assert result.exit_code == 1
    run_dir = next((tmp_path / ".chorus" / "runs").glob("run_*"))
    attempt = json.loads(
        (run_dir / "attempts" / "attempt_1" / "summary.json").read_text(encoding="utf-8")
    )
    assert attempt["passed"] is False
    assert attempt["test_results"][0]["passed"] is False
    assert "test_discount_percentage" in attempt["test_results"][0]["output"]


def test_tool_proxy_denies_forbidden_exec_command(tmp_path: Path) -> None:
    _write_checkout_repo(tmp_path)
    contract = compile_fix_test_contract(
        command="python -m pytest tests/test_checkout.py -q",
        repo_root=tmp_path,
    )
    sandbox = LocalWorktreeSandbox.create(tmp_path, tmp_path / ".chorus" / "proxy")
    budget = BudgetState()
    policy = PolicyEngine(contract, budget)
    events = JsonlRunEventLog(tmp_path / ".chorus" / "proxy-events.jsonl", run_id="proxy")
    proxy = ContractToolProxy(sandbox=sandbox, policy=policy, budget=budget, events=events)

    result = proxy.call("run_test", {"command": "curl example.com"})

    assert not result.ok
    assert "blocked" in result.error
    assert budget.tool_calls == 0


def test_workflow_plan_validation_rejects_bad_nodes() -> None:
    workflow = WorkflowPlan(
        version=1,
        goal="bad",
        budget={},
        nodes=(
            WorkflowNode(id="a", op="generate"),
            WorkflowNode(id="a", op="exec"),
            WorkflowNode(id="b", op="unknown", inputs=("missing",)),
        ),
    )

    issues = workflow.validate()

    assert "duplicate node id: a" in issues
    assert "unsupported op for node b: unknown" in issues
    assert "node b references missing dependency missing" in issues


def test_workflow_plan_validation_rejects_cycles_and_bad_budget() -> None:
    workflow = WorkflowPlan(
        version=1,
        goal="cycle",
        budget={"max_cost_usd": -1},
        nodes=(
            WorkflowNode(id="a", op="generate", inputs=("b",)),
            WorkflowNode(id="b", op="verify", inputs=("a",)),
        ),
    )

    issues = workflow.validate()

    assert "budget.max_cost_usd must be non-negative" in issues
    assert any("dependency cycle" in issue for issue in issues)


def test_workflow_check_cli_validates_generic_and_contract_shape(tmp_path: Path) -> None:
    contract = compile_fix_test_contract(
        command="python -m pytest tests/test_checkout.py -q",
        repo_root=tmp_path,
    )
    workflow = compile_fix_test_workflow(
        contract=contract,
        attempts=2,
        max_repairs=1,
        agent_name="scripted",
        provider="",
        model="",
    )
    contract_path = tmp_path / "contract.yaml"
    workflow_path = tmp_path / "workflow.yaml"
    contract.write(contract_path)
    workflow.write(workflow_path)
    runner = CliRunner()

    generic = runner.invoke(app, ["workflow", "check", str(workflow_path)])
    shaped = runner.invoke(
        app,
        ["workflow", "check", str(workflow_path), "--contract", str(contract_path)],
    )

    assert generic.exit_code == 0
    assert shaped.exit_code == 0
    assert "Workflow OK" in shaped.output


def test_workflow_check_cli_reports_invalid_workflow(tmp_path: Path) -> None:
    workflow = WorkflowPlan(
        version=1,
        goal="bad",
        budget={},
        nodes=(WorkflowNode(id="bad", op="unknown"),),
    )
    path = tmp_path / "workflow.yaml"
    workflow.write(path)
    runner = CliRunner()

    result = runner.invoke(app, ["workflow", "check", str(path)])

    assert result.exit_code == 1
    assert "unsupported op" in result.output


def test_workflow_explain_cli_prints_order_and_budget(tmp_path: Path) -> None:
    workflow = WorkflowPlan(
        version=1,
        schema_version=1,
        name="demo",
        goal="demo workflow",
        budget={"max_cost_usd": 0.01},
        nodes=(
            WorkflowNode(id="classify", op="classify"),
            WorkflowNode(id="report", op="report", inputs=("classify",)),
        ),
    )
    path = tmp_path / "workflow.yaml"
    workflow.write(path)
    runner = CliRunner()

    result = runner.invoke(app, ["workflow", "explain", str(path)])

    assert result.exit_code == 0
    assert "1. classify [classify]" in result.output
    assert "2. report [report]" in result.output
    assert '"max_cost_usd": 0.01' in result.output


def test_workflow_run_cli_writes_node_evidence_and_proof(tmp_path: Path) -> None:
    workflow = WorkflowPlan(
        version=1,
        schema_version=1,
        name="demo",
        goal="summarize a document",
        budget={"max_cost_usd": 0.01},
        nodes=(
            WorkflowNode(id="classify", op="classify"),
            WorkflowNode(id="report", op="report", inputs=("classify",)),
        ),
    )
    path = tmp_path / "workflow.yaml"
    workflow.write(path)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "workflow",
            "run",
            str(path),
            "--repo-root",
            str(tmp_path),
            "--out-dir",
            str(tmp_path / ".chorus" / "runs"),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    run_dir = Path(payload["run_dir"])
    assert (run_dir / "workflow.yaml").is_file()
    assert (run_dir / "events.jsonl").is_file()
    assert (run_dir / "proof.json").is_file()
    assert (run_dir / "nodes" / "classify" / "result.json").is_file()
    assert (run_dir / "nodes" / "report" / "result.json").is_file()
    proof = json.loads((run_dir / "proof.json").read_text(encoding="utf-8"))
    assert proof["status"] == "pass"


def test_workflow_run_cli_denies_forbidden_exec_command(tmp_path: Path) -> None:
    workflow = WorkflowPlan(
        version=1,
        goal="blocked exec",
        budget={},
        nodes=(WorkflowNode(id="blocked", op="exec", params={"command": "curl example.com"}),),
    )
    path = tmp_path / "workflow.yaml"
    workflow.write(path)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "workflow",
            "run",
            str(path),
            "--repo-root",
            str(tmp_path),
            "--out-dir",
            str(tmp_path / ".chorus" / "runs"),
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    run_dir = Path(payload["run_dir"])
    node = json.loads(
        (run_dir / "nodes" / "blocked" / "result.json").read_text(encoding="utf-8")
    )
    assert node["result"]["result"]["summary"] == "command is blocked: curl example.com"


def test_workflow_run_cli_blocks_tainted_exec_without_policy(tmp_path: Path) -> None:
    workflow = WorkflowPlan(
        version=1,
        goal="tainted exec",
        budget={},
        nodes=(
            WorkflowNode(id="generate", op="generate", params={"prompt": "write code"}),
            WorkflowNode(
                id="test",
                op="exec",
                inputs=("generate",),
                params={"command": "python -m pytest tests/test_checkout.py -q"},
            ),
        ),
    )
    path = tmp_path / "workflow.yaml"
    workflow.write(path)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "workflow",
            "run",
            str(path),
            "--repo-root",
            str(tmp_path),
            "--out-dir",
            str(tmp_path / ".chorus" / "runs"),
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    run_dir = Path(payload["run_dir"])
    node = json.loads((run_dir / "nodes" / "test" / "result.json").read_text(encoding="utf-8"))
    assert node["result"]["status"] == "quarantined"
    assert "tainted dependency" in node["result"]["error"]


def test_workflow_run_cli_resumes_completed_node_evidence(tmp_path: Path) -> None:
    workflow = WorkflowPlan(
        version=1,
        goal="resume workflow",
        budget={},
        nodes=(WorkflowNode(id="classify", op="classify"),),
    )
    path = tmp_path / "workflow.yaml"
    workflow.write(path)
    runner = CliRunner()
    base_args = [
        "workflow",
        "run",
        str(path),
        "--repo-root",
        str(tmp_path),
        "--out-dir",
        str(tmp_path / ".chorus" / "runs"),
        "--run-id",
        "resume_demo",
    ]

    first = runner.invoke(app, base_args)
    second = runner.invoke(app, [*base_args, "--resume"])

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    run_dir = tmp_path / ".chorus" / "runs" / "resume_demo"
    events = [
        json.loads(line)
        for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert any(event["type"] == "workflow_node_reused" for event in events)


def test_plan_cli_writes_template_workflow(tmp_path: Path) -> None:
    runner = CliRunner()
    out = tmp_path / "planned.yaml"

    result = runner.invoke(
        app,
        [
            "plan",
            "--task",
            "Fix the checkout test",
            "--template",
            "coding_fix_test",
            "--cmd",
            "python -m pytest tests/test_checkout.py -q",
            "--n",
            "3",
            "--max-repairs",
            "2",
            "--out",
            str(out),
        ],
    )

    assert result.exit_code == 0, result.output
    workflow = WorkflowPlan.read(out)
    assert workflow.name == "coding_fix_test"
    assert workflow.validate() == []
    assert [node.id for node in workflow.nodes] == [
        "reproduce",
        "generate",
        "run_tests",
        "repair",
        "rank",
        "verify",
        "report",
    ]


def test_fix_test_workflow_shape_validation_rejects_command_mismatch(tmp_path: Path) -> None:
    contract = compile_fix_test_contract(
        command="python -m pytest tests/test_checkout.py -q",
        repo_root=tmp_path,
    )
    workflow = compile_fix_test_workflow(
        contract=contract,
        attempts=2,
        max_repairs=1,
        agent_name="scripted",
        provider="",
        model="",
    )
    nodes = tuple(
        WorkflowNode(
            id=node.id,
            op=node.op,
            inputs=node.inputs,
            params={**node.params, "command": "python -m pytest other.py -q"},
        )
        if node.id == "run_tests"
        else node
        for node in workflow.nodes
    )
    bad = WorkflowPlan(
        version=workflow.version,
        goal=workflow.goal,
        budget=workflow.budget,
        nodes=nodes,
    )

    try:
        validate_fix_test_workflow(bad, contract=contract)
    except RuntimeError as exc:
        assert "run_tests command" in str(exc)
    else:
        raise AssertionError("expected fixed-shape workflow validation to fail")


def test_failure_not_reproduced_exits_nonzero_with_proof(tmp_path: Path) -> None:
    _write_checkout_repo(tmp_path, fixed=True)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "fix-test",
            "--cmd",
            "python -m pytest tests/test_checkout.py -q",
            "--repo-root",
            str(tmp_path),
            "--out-dir",
            str(tmp_path / ".chorus" / "runs"),
            "--agent",
            "scripted",
        ],
    )

    assert result.exit_code == 1
    proof = next((tmp_path / ".chorus" / "runs").glob("run_*/proof.md"))
    assert "failure_not_reproduced" in proof.read_text(encoding="utf-8")


def test_bench_command_is_deprecated() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["bench", "--subset", "1", "--n", "1", "--k", "1"])

    assert result.exit_code == 2
    assert "fix-test" in result.output


def _write_checkout_repo(
    path: Path,
    *,
    fixed: bool = False,
    unsupported_bug: bool = False,
) -> None:
    (path / "tests").mkdir(parents=True)
    if fixed:
        implementation = "def apply_discount(price, discount):\n    return price * (1 - discount)\n"
    elif unsupported_bug:
        implementation = "def apply_discount(price, discount):\n    return price + discount\n"
    else:
        implementation = "def apply_discount(price, discount):\n    return price - discount\n"
    (path / "checkout.py").write_text(implementation, encoding="utf-8")
    (path / "tests" / "test_checkout.py").write_text(
        "from checkout import apply_discount\n\n"
        "def test_discount_percentage():\n"
        "    assert apply_discount(100, 0.2) == 80\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init"], cwd=path, capture_output=True, text=True, check=False)
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True, text=True, check=False)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=path,
        capture_output=True,
        text=True,
        check=False,
    )
