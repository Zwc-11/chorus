"""PR proof package data for contract-first runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from chorus.domain.contract import Contract
from chorus.domain.verification import VerificationResult


@dataclass(frozen=True, slots=True)
class ProofPackage:
    run_id: str
    verdict: str
    contract: Contract
    verification: VerificationResult
    diff: str
    model_calls: int
    tool_calls: int
    cost_usd: float
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["contract"] = self.contract.to_dict()
        data["verification"] = self.verification.to_dict()
        return data
