"""Pure recovery transitions shared by the async scheduler and invariant tests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from .spec import JsonValue, canonical_json, json_copy

type ResultDisposition = Literal["accept", "idempotent", "conflict", "closed"]
type UncertaintyDisposition = Literal["mark_unknown", "settled", "closed"]
type ClosureDisposition = Literal["close", "settled", "closed"]


@dataclass(frozen=True)
class ActionSettlement:
    disposition: ResultDisposition
    result: Mapping[str, JsonValue] | None


@dataclass(frozen=True)
class ActionUncertainty:
    disposition: UncertaintyDisposition
    result: Mapping[str, JsonValue] | None


@dataclass(frozen=True)
class ActionClosure:
    disposition: ClosureDisposition
    result: Mapping[str, JsonValue] | None


def classify_action_settlement(
    status: object,
    persisted: object,
    candidate: Mapping[str, JsonValue],
) -> ActionSettlement:
    """Classify a result without mutating state or comparing object identity."""

    copied = json_copy(candidate)
    if not isinstance(copied, dict):  # pragma: no cover - type contract guard
        raise TypeError("action result candidate must be a JSON object")
    if status in {"DISPATCHED", "RECONCILING"}:
        return ActionSettlement("accept", copied)
    if status != "RESOLVED" or not isinstance(persisted, Mapping):
        return ActionSettlement("closed", None)
    existing = json_copy(persisted)
    if not isinstance(existing, dict):
        return ActionSettlement("closed", None)
    disposition: ResultDisposition = (
        "idempotent"
        if canonical_json(existing) == canonical_json(copied)
        else "conflict"
    )
    return ActionSettlement(disposition, existing)


def classify_action_uncertainty(status: object, persisted: object) -> ActionUncertainty:
    """Keep a confirmed fact monotonic when a late transport error arrives."""

    if status in {"DISPATCHED", "RECONCILING"}:
        return ActionUncertainty("mark_unknown", None)
    if status == "RESOLVED" and isinstance(persisted, Mapping):
        existing = json_copy(persisted)
        if isinstance(existing, dict):
            return ActionUncertainty("settled", existing)
    return ActionUncertainty("closed", None)


def classify_action_closure(status: object, persisted: object) -> ActionClosure:
    """Classify a definitive stop/rejection without reopening a settled Action."""

    if status in {"DISPATCHED", "RECONCILING"}:
        return ActionClosure("close", None)
    if status == "RESOLVED" and isinstance(persisted, Mapping):
        existing = json_copy(persisted)
        if isinstance(existing, dict):
            return ActionClosure("settled", existing)
    return ActionClosure("closed", None)


def merge_cancel_intent(previous: object, requested: bool = True) -> bool:
    """Cancellation intent is monotonic across every subsequent transition."""

    return bool(previous) or requested
