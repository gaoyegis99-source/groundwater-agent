"""Explicit, local changes to the validated workflow state tree.

The async coordinator still commits through its existing journal boundary.
This internal editor makes a transition's write set derive from the operations
that actually changed state, rather than from a full-document diff or an
unverified caller hint. It never escapes into a snapshot or SDK state.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
import json
from typing import Any

from .spec import (
    JsonValue,
    WorkflowValidationError,
    canonical_json,
    contains_secret_name,
    json_copy,
)

StatePath = tuple[str, ...]
_MISSING = object()
_EDITOR_ORIGIN = object()


@dataclass(frozen=True, init=False)
class StateWrite:
    """One immutable, validated object-member replacement or deletion."""

    path: StatePath
    deleted: bool
    value_json: str | None

    def __init__(self, path: StatePath, deleted: bool, value: JsonValue = None) -> None:
        StateEdit._validate_path(path)
        if not isinstance(deleted, bool):
            raise WorkflowValidationError("workflow state deletion flag is invalid")
        if deleted:
            if value is not None:
                raise WorkflowValidationError("deleted workflow state has a value")
            serialized = None
        else:
            copied = json_copy(value)
            if contains_secret_name(copied):
                raise WorkflowValidationError(
                    "run checkpoints must not contain credentials"
                )
            serialized = canonical_json(copied)
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "deleted", deleted)
        object.__setattr__(self, "value_json", serialized)

    @property
    def value(self) -> JsonValue:
        """Return a detached value; the canonical JSON remains authority."""

        return None if self.value_json is None else json.loads(self.value_json)


@dataclass(frozen=True)
class StateMemberAdd:
    """A new member of a bounded workflow fact list, not a JSON object key."""

    path: StatePath
    member: str

    def __post_init__(self) -> None:
        StateEdit._validate_path(self.path)
        if self.path not in {("completed",), ("approved_steps",)}:
            raise WorkflowValidationError("workflow state member path is invalid")
        if not isinstance(self.member, str) or not self.member:
            raise WorkflowValidationError("workflow state member is invalid")


@dataclass(frozen=True)
class StateWriteSet:
    """Process-local exact values, deletions, and additive membership facts."""

    writes: tuple[StateWrite, ...]
    member_additions: tuple[StateMemberAdd, ...] = ()
    _origin: object | None = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.writes and not self.member_additions:
            raise WorkflowValidationError("workflow state write set is empty")
        if any(not isinstance(write, StateWrite) for write in self.writes):
            raise WorkflowValidationError("workflow state write is invalid")
        paths = tuple(write.path for write in self.writes)
        if tuple(sorted(set(paths))) != paths:
            raise WorkflowValidationError("workflow state write set is not canonical")
        for index, path in enumerate(paths):
            if any(path[: len(previous)] == previous for previous in paths[:index]):
                raise WorkflowValidationError("workflow state write paths overlap")
        if any(
            not isinstance(addition, StateMemberAdd)
            for addition in self.member_additions
        ):
            raise WorkflowValidationError("workflow state member addition is invalid")
        if (
            tuple(
                sorted(
                    set(self.member_additions),
                    key=lambda item: (item.path, item.member),
                )
            )
            != self.member_additions
        ):
            raise WorkflowValidationError(
                "workflow state member additions are not canonical"
            )
        for addition in self.member_additions:
            if any(StateEdit._paths_overlap(addition.path, path) for path in paths):
                raise WorkflowValidationError(
                    "workflow state member conflicts with object write"
                )

    @property
    def paths(self) -> tuple[StatePath, ...]:
        paths = {write.path for write in self.writes}
        paths.update(addition.path for addition in self.member_additions)
        return tuple(sorted(paths))

    @property
    def _from_editor(self) -> bool:
        """Only in-process editor output may drive a partial journal write."""

        return self._origin is _EDITOR_ORIGIN


class StateEdit:
    """Mutate only named JSON object members and record exact changed paths.

    Reads return detached JSON so an integration cannot mutate the underlying
    state without going through ``set``/``remove``. This is intentionally not
    a general mutable-dict proxy: hidden nested writes would make the write
    set untrustworthy for a later local-cell journal commit.
    """

    def __init__(
        self,
        state: dict[str, Any],
        *,
        initial_changed_paths: Iterable[StatePath] = (),
    ) -> None:
        self._state = state
        self._changed: set[StatePath] = set()
        self._member_additions: set[StateMemberAdd] = set()
        for path in initial_changed_paths:
            self._validate_path(path)
            self._changed.add(path)

    @property
    def changed_paths(self) -> tuple[StatePath, ...]:
        return tuple(sorted(self._changed))

    def write_set(self) -> StateWriteSet:
        # A legacy snapshot may gain a missing parent field and then update a
        # child in the same transition. Keep only the parent with its final
        # value, so a later local-cell commit has one unambiguous write.
        minimal: list[StatePath] = []
        for path in sorted(self._changed, key=lambda item: (len(item), item)):
            if not any(path[: len(parent)] == parent for parent in minimal):
                minimal.append(path)
        membership_paths = {addition.path for addition in self._member_additions}
        writes: list[StateWrite] = []
        for path in sorted(minimal):
            if path in membership_paths:
                continue
            current: object = self._state
            for part in path:
                if not isinstance(current, Mapping) or part not in current:
                    writes.append(StateWrite(path, True))
                    break
                current = current[part]
            else:
                writes.append(StateWrite(path, False, current))  # type: ignore[arg-type]
        result = StateWriteSet(
            tuple(writes),
            tuple(
                sorted(
                    self._member_additions, key=lambda item: (item.path, item.member)
                )
            ),
        )
        object.__setattr__(result, "_origin", _EDITOR_ORIGIN)
        return result

    def read(
        self, path: StatePath, *, default: object = _MISSING
    ) -> JsonValue | object:
        self._validate_path(path)
        current: object = self._state
        for part in path:
            if not isinstance(current, Mapping) or part not in current:
                if default is _MISSING:
                    raise WorkflowValidationError("workflow state path is missing")
                return default
            current = current[part]
        return json_copy(current)  # type: ignore[arg-type]

    def set(self, path: StatePath, value: JsonValue) -> None:
        parent, key = self._parent(path)
        copied = json_copy(value)
        if contains_secret_name(copied):
            raise WorkflowValidationError(
                "run checkpoints must not contain credentials"
            )
        parent[key] = copied
        self._changed.add(path)
        self._member_additions = {
            addition
            for addition in self._member_additions
            if not self._paths_overlap(path, addition.path)
        }

    def remove(self, path: StatePath, *, missing_ok: bool = False) -> None:
        parent, key = self._parent(path)
        if key not in parent:
            if missing_ok:
                return
            raise WorkflowValidationError("workflow state path is missing")
        del parent[key]
        self._changed.add(path)
        self._member_additions = {
            addition
            for addition in self._member_additions
            if not self._paths_overlap(path, addition.path)
        }

    def add_sorted_member(self, path: StatePath, value: str) -> None:
        if not isinstance(value, str) or not value:
            raise WorkflowValidationError("workflow state member is invalid")
        members = self.read(path)
        if not isinstance(members, list) or any(
            not isinstance(item, str) for item in members
        ):
            raise WorkflowValidationError("workflow state member list is invalid")
        normalized = sorted({*members, value})
        if normalized != members:
            pure_addition = members == sorted(set(members)) and value not in members
            prior = {
                addition for addition in self._member_additions if addition.path == path
            }
            self.set(path, normalized)
            if pure_addition:
                self._member_additions.update(prior)
            if pure_addition and path in {("completed",), ("approved_steps",)}:
                self._member_additions.add(StateMemberAdd(path, value))

    @staticmethod
    def _paths_overlap(left: StatePath, right: StatePath) -> bool:
        return left[: len(right)] == right or right[: len(left)] == left

    @staticmethod
    def _validate_path(path: StatePath) -> None:
        if (
            not isinstance(path, tuple)
            or not path
            or any(not isinstance(part, str) or not part for part in path)
        ):
            raise WorkflowValidationError("workflow state path is invalid")
        if contains_secret_name(dict.fromkeys(path)):
            raise WorkflowValidationError(
                "run checkpoints must not contain credentials"
            )

    def _parent(self, path: StatePath) -> tuple[dict[str, Any], str]:
        self._validate_path(path)
        current: object = self._state
        for part in path[:-1]:
            if not isinstance(current, dict) or part not in current:
                raise WorkflowValidationError("workflow state parent path is missing")
            current = current[part]
        if not isinstance(current, dict):
            raise WorkflowValidationError("workflow state parent must be an object")
        return current, path[-1]


@dataclass(frozen=True)
class ExplicitStateMutation:
    """A transition callback whose writes are all made through ``StateEdit``."""

    apply: Callable[[StateEdit], None]

    def __call__(self, state: dict[str, Any]) -> None:
        """Keep composed legacy callbacks usable without bypassing validation.

        The coordinator passes this object directly to its editor when it is
        the whole transition.  A callback that composes several mutations can
        still call it against its detached state; that transition retains the
        journal's conservative full-state fallback.
        """

        self.apply(StateEdit(state))
