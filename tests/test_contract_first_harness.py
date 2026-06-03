"""Contract-first execution harness tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

from typer.testing import CliRunner

from chorus.application.contract_compiler import compile_fix_test_contract
from chorus.cli import app
from chorus.domain.contract import Contract
from chorus.domain.policy import BudgetState, PolicyEngine
from chorus.domain.tool import ToolRequest


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
    assert (run_dir / "events.jsonl").is_file()
    assert (run_dir / "diff.patch").is_file()
    assert (run_dir / "proof.md").is_file()
    assert (run_dir / "report.html").is_file()
    assert "Verdict: PASS" in (run_dir / "proof.md").read_text(encoding="utf-8")
    assert "checkout.py" in (run_dir / "diff.patch").read_text(encoding="utf-8")


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


def _write_checkout_repo(path: Path, *, fixed: bool = False) -> None:
    (path / "tests").mkdir(parents=True)
    implementation = (
        "def apply_discount(price, discount):\n"
        "    return price * (1 - discount)\n"
        if fixed
        else "def apply_discount(price, discount):\n    return price - discount\n"
    )
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
