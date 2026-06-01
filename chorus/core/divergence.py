"""Agreement and divergence analysis derived from the event log."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from chorus.core.events import Event, EventType, stable_json

Signature = tuple[Any, ...]
Similarity = Callable[[Signature, Signature], bool]


@dataclass(frozen=True, slots=True)
class OverlayCell:
    trajectory_id: str
    step: int
    state: str
    signature: Signature | None
    in_majority: bool


@dataclass(frozen=True, slots=True)
class DivergenceOverlay:
    trajectory_ids: tuple[str, ...]
    steps: tuple[int, ...]
    agreement: tuple[float | None, ...]
    divergence_step: int | None
    divergence_name: str | None
    cells: tuple[OverlayCell, ...]
    low_confidence: bool


def group_trajectory_events(events: Iterable[Event]) -> dict[str, list[Event]]:
    grouped: dict[str, list[Event]] = defaultdict(list)
    for event in events:
        if event.trajectory_id is not None:
            grouped[event.trajectory_id].append(event)
    return {key: sorted(value, key=lambda item: item.seq) for key, value in grouped.items()}


def normalize_args(args: Any) -> Any:
    """Make tool arguments comparable across dict ordering and incidental reprs."""

    if isinstance(args, dict):
        return tuple((key, normalize_args(value)) for key, value in sorted(args.items()))
    if isinstance(args, list | tuple):
        return tuple(normalize_args(value) for value in args)
    return args


def exact_match(left: Signature, right: Signature) -> bool:
    return left == right


def step_signature(events: list[Event], step: int, *, capture: bool = False) -> Signature | None:
    """Return the structural action a trajectory took at a step index."""

    start_index = None
    end_seq = None
    for idx, event in enumerate(events):
        if event.type == EventType.STEP_STARTED and int(event.payload.get("index", -1)) == step:
            start_index = idx
            for later in events[idx + 1 :]:
                if later.type == EventType.STEP_STARTED:
                    end_seq = later.seq
                    break
            break
    if start_index is None:
        return None

    window = [
        event for event in events[start_index + 1 :] if end_seq is None or event.seq < end_seq
    ]
    for event in window:
        if event.type == EventType.TOOL_CALL:
            payload = event.payload
            args = payload.get("args", {}) if capture else normalize_args(payload.get("args", {}))
            return ("tool", payload.get("tool"), args)
    for event in window:
        if event.type == EventType.MODEL_CALL:
            payload = event.payload
            if capture and "content" in payload:
                return (
                    "model",
                    payload.get("model"),
                    payload.get("finish_reason"),
                    payload["content"],
                )
            return ("model", payload.get("model"), payload.get("finish_reason"))
    return ("step", step)


def cluster_signatures(
    signatures: list[Signature],
    similar: Similarity = exact_match,
) -> list[list[Signature]]:
    clusters: list[list[Signature]] = []
    for signature in signatures:
        for cluster in clusters:
            if similar(signature, cluster[0]):
                cluster.append(signature)
                break
        else:
            clusters.append([signature])
    return clusters


def agreement_at(
    trajectories: dict[str, list[Event]],
    step: int,
    *,
    similar: Similarity = exact_match,
) -> float | None:
    signatures = [step_signature(events, step) for events in trajectories.values()]
    active = [signature for signature in signatures if signature is not None]
    if not active:
        return None
    majority = max(cluster_signatures(active, similar), key=len)
    return len(majority) / len(active)


def divergence_step(
    trajectories: dict[str, list[Event]],
    *,
    n_steps: int | None = None,
    tol: float = 1.0,
    similar: Similarity = exact_match,
) -> int | None:
    for step in range(n_steps if n_steps is not None else max_step_count(trajectories)):
        agreement = agreement_at(trajectories, step, similar=similar)
        if agreement is not None and agreement < tol:
            return step
    return None


def max_step_count(trajectories: dict[str, list[Event]]) -> int:
    max_index = -1
    for events in trajectories.values():
        for event in events:
            if event.type == EventType.STEP_STARTED:
                max_index = max(max_index, int(event.payload.get("index", -1)))
    return max_index + 1


def build_divergence_overlay(
    events: Iterable[Event],
    *,
    similar: Similarity = exact_match,
    tol: float = 1.0,
) -> DivergenceOverlay:
    trajectories = group_trajectory_events(events)
    steps = tuple(range(max_step_count(trajectories)))
    agreement = tuple(agreement_at(trajectories, step, similar=similar) for step in steps)
    split = divergence_step(trajectories, n_steps=len(steps), tol=tol, similar=similar)

    cells: list[OverlayCell] = []
    for step in steps:
        signatures = {
            trajectory_id: step_signature(trajectory_events, step)
            for trajectory_id, trajectory_events in trajectories.items()
        }
        active = [signature for signature in signatures.values() if signature is not None]
        majority: list[Signature] = []
        if active:
            majority = max(cluster_signatures(active, similar), key=len)
        for trajectory_id, trajectory_events in trajectories.items():
            signature = signatures[trajectory_id]
            in_majority = bool(
                signature is not None and any(similar(signature, m) for m in majority)
            )
            state = _cell_state(trajectory_events, step, signature, in_majority)
            cells.append(
                OverlayCell(
                    trajectory_id=trajectory_id,
                    step=step,
                    state=state,
                    signature=signature,
                    in_majority=in_majority,
                )
            )

    return DivergenceOverlay(
        trajectory_ids=tuple(trajectories),
        steps=steps,
        agreement=agreement,
        divergence_step=split,
        divergence_name=None if split is None else f"step {split}",
        cells=tuple(cells),
        low_confidence=len(trajectories) < 5,
    )


def _cell_state(
    events: list[Event],
    step: int,
    signature: Signature | None,
    in_majority: bool,
) -> str:
    if signature is None:
        return "inactive"
    failure_step = _failure_step(events)
    if failure_step is not None and step >= failure_step:
        return "failed"
    return "converged" if in_majority else "diverged"


def _failure_step(events: list[Event]) -> int | None:
    current_step: int | None = None
    for event in events:
        if event.type == EventType.STEP_STARTED:
            current_step = int(event.payload.get("index", 0))
        elif event.type == EventType.TOOL_RESULT and "error" in event.payload:
            return current_step
        elif event.type == EventType.CONTRACT_CHECK and not bool(
            event.payload.get("accepted", True)
        ):
            value = event.payload.get("step")
            return int(value) if value is not None else current_step
        elif event.type == EventType.VERDICT and event.payload.get("failure_step") is not None:
            return int(event.payload["failure_step"])
    return None


def signature_label(signature: Signature | None) -> str:
    if signature is None:
        return "inactive"
    return stable_json(signature)
