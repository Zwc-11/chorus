"""Contract-first failing-test workflow."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from chorus.adapters.agents.contract_lite import build_contract_agent
from chorus.adapters.sandboxes.local_worktree import LocalWorktreeSandbox
from chorus.adapters.tools.contract_proxy import ContractToolProxy
from chorus.application.contract_compiler import compile_fix_test_contract
from chorus.application.event_log import JsonlRunEventLog
from chorus.application.proof_builder import write_proof_package
from chorus.application.verifier import verify_contract
from chorus.domain.contract import Contract
from chorus.domain.policy import BudgetState, PolicyEngine
from chorus.domain.proof import ProofPackage


def run_fix_test(
    *,
    command: str,
    repo_root: Path,
    out_root: Path,
    budget_usd: float = 0.50,
    agent_name: str = "scripted",
    provider: str = "",
    model: str = "",
) -> ProofPackage:
    run_id = f"run_{uuid4().hex[:12]}"
    run_dir = out_root / run_id
    sandbox = LocalWorktreeSandbox.create(repo_root, run_dir)
    events = JsonlRunEventLog(run_dir / "events.jsonl", run_id=run_id)
    events.emit("run_started", {"command": command, "repo_root": str(repo_root)})

    before = sandbox.run(command)
    failure_reproduced = before.returncode != 0
    events.emit(
        "test_finished",
        {
            "phase": "reproduce",
            "command": command,
            "returncode": before.returncode,
            "output": before.output,
        },
    )
    contract = compile_fix_test_contract(
        command=command,
        repo_root=repo_root,
        failure_output=before.output,
        budget_usd=budget_usd,
    )
    contract.write(run_dir / "contract.yaml")
    events.emit("contract_generated", {"contract": contract.to_dict()})

    budget = BudgetState()
    policy = PolicyEngine(contract, budget)
    summary = ""
    if failure_reproduced:
        agent = build_contract_agent(agent=agent_name, provider=provider, model=model)
        proxy = ContractToolProxy(sandbox=sandbox, policy=policy, budget=budget, events=events)
        summary = agent.run(contract=contract, tools=proxy)
    else:
        events.emit("verification_failed", {"failure": "failure_not_reproduced"})

    verification = verify_contract(
        contract=contract,
        sandbox=sandbox,
        policy=policy,
        failure_reproduced=failure_reproduced,
    )
    diff = sandbox.git_diff()
    proof = ProofPackage(
        run_id=run_id,
        verdict="pass" if verification.passed else "fail",
        contract=contract,
        verification=verification,
        diff=diff,
        model_calls=budget.model_calls,
        tool_calls=budget.tool_calls,
        cost_usd=budget.cost_usd,
        summary=summary,
    )
    write_proof_package(proof, run_dir)
    events.emit("verification_finished", verification.to_dict())
    events.emit("proof_generated", {"run_dir": str(run_dir), "verdict": proof.verdict})
    events.emit("run_finished", {"verdict": proof.verdict})
    return proof


def run_contract(
    *,
    contract: Contract,
    out_root: Path,
    agent_name: str = "scripted",
    provider: str = "",
    model: str = "",
) -> ProofPackage:
    return run_fix_test(
        command=contract.task.command,
        repo_root=Path(contract.repo.root),
        out_root=out_root,
        budget_usd=contract.budget.max_cost_usd,
        agent_name=agent_name,
        provider=provider,
        model=model,
    )


def proof_summary(proof: ProofPackage) -> str:
    return json.dumps(
        {
            "run_id": proof.run_id,
            "verdict": proof.verdict,
            "changed_files": proof.verification.changed_files,
            "failures": proof.verification.failures,
        },
        indent=2,
    )
