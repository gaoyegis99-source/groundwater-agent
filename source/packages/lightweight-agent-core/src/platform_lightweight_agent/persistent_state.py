"""Immutable, lazy read view of v4 journal state cells.

The SQLite journal remains the only durable authority. This process-local
view keeps one verified revision's cells in ``immutables.Map`` and applies
only editor-minted writes after the journal commits them. It never enters a
snapshot, event, or SDK object. Importing the base package does not import
``immutables``; the dependency belongs to the optional workflow runtime.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from typing import Any

from .journal import (
    _CELL_ENTRY_SECTIONS,
    _CELL_MEMBER_SECTIONS,
    _CELL_V3_ENTRY_SECTIONS,
    _cell_address_for_write,
    _cell_path,
    _cell_updates_from_writes,
    _state_cells,
    _CellDeltaUnavailableError,
)
from .state_edits import StateEdit, StateMemberAdd, StateWriteSet
from .spec import WorkflowValidationError, canonical_json


def _map(values: Mapping[str, Any]) -> Any:
    try:
        import immutables
    except ImportError as error:
        raise RuntimeError(
            "Install platform-lightweight-agent-core[workflow] for v4 execution"
        ) from error
    return immutables.Map(values)


class PersistentCellState:
    """One detached, validated v4 state revision with local cell updates."""

    def __init__(
        self,
        cells: Any,
        members: Mapping[str, Any],
        children: Mapping[tuple[str, ...], Any],
    ) -> None:
        self._cells = cells
        self._members = members
        self._children = children

    @staticmethod
    def _child_index(cells: Mapping[str, str]) -> dict[tuple[str, ...], Any]:
        names: dict[tuple[str, ...], dict[str, bool]] = {}
        for path_json in cells:
            path = tuple(json.loads(path_json))
            names.setdefault(path[:-1], {})[path[-1]] = True
        return {prefix: _map(children) for prefix, children in names.items()}

    def _apply_updates(
        self, updates: Mapping[str, str | None], members: Mapping[str, Any]
    ) -> PersistentCellState:
        mutation = self._cells.mutate()
        children = dict(self._children)
        for path_json, value_json in updates.items():
            existed = path_json in self._cells
            if value_json is None:
                if existed:
                    del mutation[path_json]
            else:
                mutation[path_json] = value_json
            if existed == (value_json is not None):
                continue
            path = tuple(json.loads(path_json))
            prefix = path[:-1]
            siblings = children.get(prefix)
            if siblings is None:
                siblings = _map({})
            children[prefix] = (
                siblings.set(path[-1], True)
                if value_json is not None
                else siblings.delete(path[-1])
            )
        return PersistentCellState(mutation.finish(), members, children)

    @classmethod
    def from_state(cls, state: Mapping[str, Any]) -> PersistentCellState:
        cells = _map(_state_cells(state))
        members = {
            section: _map({member: True for member in state[section]})
            for section in _CELL_MEMBER_SECTIONS
            if isinstance(state.get(section), list)
            and cells.get(_cell_path(section)) == "[]"
        }
        return cls(cells, members, cls._child_index(cells))

    def after_commit(
        self, state: Mapping[str, Any], write_set: StateWriteSet | None
    ) -> PersistentCellState:
        """Reuse old cells only for editor-validated local writes.

        Whole-section edits and untrusted mutation callbacks rebuild from the
        already validated committed state. They do not claim incremental work.
        """

        updates = (
            _cell_updates_from_writes(state, write_set)
            if write_set is not None
            else None
        )
        if updates is None:
            return self.from_state(state)
        members = dict(self._members)
        for path_json, value_json in updates.items():
            path = json.loads(path_json)
            if len(path) == 2 and path[0] in _CELL_MEMBER_SECTIONS:
                membership = members.get(path[0])
                if membership is not None:
                    if value_json is None:
                        membership = membership.delete(path[1])
                    else:
                        membership = membership.set(path[1], True)
                    members[path[0]] = membership
        return self._apply_updates(updates, members)

    def apply_write_set(self, write_set: StateWriteSet) -> PersistentCellState | None:
        """Project editor-minted local writes without hydrating sibling cells.

        The caller must bind this view to the same validated revision that
        produced the write set. A whole-section replacement or a legacy
        noncanonical member list needs the existing full-state fallback.
        This is a process-local projection, never a journal commit authority.
        """

        if not write_set._from_editor:
            return None
        updates: dict[str, str | None] = {}
        for write in write_set.writes:
            address = _cell_address_for_write(write.path)
            if address is None:
                return None
            key = _cell_path(*address)
            if write.path == address:
                updates[key] = None if write.deleted else write.value_json
                continue
            raw = updates.get(key, self._cells.get(key))
            if raw is None:
                raise WorkflowValidationError("workflow state cell parent is missing")
            value = json.loads(raw)
            parent = value
            suffix = write.path[len(address) :]
            for part in suffix[:-1]:
                if not isinstance(parent, dict) or part not in parent:
                    raise WorkflowValidationError(
                        "workflow state cell parent is missing"
                    )
                parent = parent[part]
            if not isinstance(parent, dict):
                raise WorkflowValidationError("workflow state cell parent is invalid")
            if write.deleted:
                if suffix[-1] not in parent:
                    raise WorkflowValidationError("workflow state cell path is missing")
                del parent[suffix[-1]]
            else:
                parent[suffix[-1]] = write.value
            updates[key] = canonical_json(value)

        members = dict(self._members)
        for addition in write_set.member_additions:
            section = addition.path[0]
            membership = members.get(section)
            if membership is None:
                return None
            membership = membership.set(addition.member, True)
            members[section] = membership
            updates[_cell_path(section, addition.member)] = "true"

        return self._apply_updates(updates, members)

    def view(self) -> Mapping[str, Any]:
        return _CellView(self, ())

    def to_state(self) -> dict[str, Any]:
        """Assemble a detached JSON state only at a public/full-read boundary."""

        def expand(value: Any) -> Any:
            if isinstance(value, _CellView):
                return {key: expand(value[key]) for key in value}
            return value

        root = self.view()
        state = {key: expand(root[key]) for key in root}
        for section in _CELL_MEMBER_SECTIONS:
            members = self._members.get(section)
            if members is not None and section in state:
                state[section] = sorted(members)
        return state


_CELL_EDIT_MISSING = object()


class CellStateEdit(StateEdit):
    """Use the existing validated editor on only the cells a transition touches.

    ``StateEdit`` still owns JSON/credential validation and write-set minting.
    This adapter lazily supplies its expected mutable dict shape for one cell
    at a time; unsupported whole-section reads fall back to the old full path.
    """

    def __init__(self, cells: PersistentCellState) -> None:
        super().__init__({})
        self._cells = cells
        self._loaded: set[str] = set()

    def _load(self, path: tuple[str, ...]) -> None:
        self._validate_path(path)
        address = _cell_address_for_write(path)
        if address is None:
            raise _CellDeltaUnavailableError("state operation needs a whole section")
        key = _cell_path(*address)
        if key in self._loaded:
            return
        self._loaded.add(key)
        current = self._state
        for part in address[:-1]:
            child = current.setdefault(part, {})
            if not isinstance(child, dict):
                raise WorkflowValidationError("workflow state cell parent is invalid")
            current = child
        raw = self._cells._cells.get(key)
        if raw is not None:
            current[address[-1]] = json.loads(raw)

    def read(
        self, path: tuple[str, ...], *, default: object = _CELL_EDIT_MISSING
    ) -> Any:
        # Preserve StateEdit's sentinel semantics when no default is supplied.
        self._load(path)
        if default is _CELL_EDIT_MISSING:
            return super().read(path)
        return super().read(path, default=default)

    def set(self, path: tuple[str, ...], value: Any) -> None:
        self._load(path)
        super().set(path, value)

    def remove(self, path: tuple[str, ...], *, missing_ok: bool = False) -> None:
        self._load(path)
        super().remove(path, missing_ok=missing_ok)

    def add_sorted_member(self, path: tuple[str, ...], value: str) -> None:
        self._validate_path(path)
        if path not in {("completed",), ("approved_steps",)}:
            raise _CellDeltaUnavailableError("state member needs a whole section")
        if not isinstance(value, str) or not value:
            raise WorkflowValidationError("workflow state member is invalid")
        membership = self._cells._members.get(path[0])
        if membership is None:
            raise _CellDeltaUnavailableError("state member list is not canonical")
        addition = StateMemberAdd(path, value)
        if value in membership or addition in self._member_additions:
            return
        self._changed.add(path)
        self._member_additions.add(addition)


class _CellView(Mapping[str, Any]):
    """Resolve one child cell on demand, without hydrating its siblings."""

    def __init__(self, state: PersistentCellState, prefix: tuple[str, ...]) -> None:
        self._state = state
        self._prefix = prefix

    def __getitem__(self, key: str) -> Any:
        if not isinstance(key, str):
            raise KeyError(key)
        path = (*self._prefix, key)
        raw = self._state._cells.get(_cell_path(*path))
        if raw is None:
            raise KeyError(key)
        if not self._prefix and key in _CELL_MEMBER_SECTIONS:
            members = self._state._members.get(key)
            if members is not None:
                return members
        if (
            not self._prefix
            and (key in _CELL_ENTRY_SECTIONS or key == "v3")
            or self._prefix == ("v3",)
            and key in _CELL_V3_ENTRY_SECTIONS
        ) and raw == "{}":
            return _CellView(self._state, path)
        return json.loads(raw)

    def __iter__(self) -> Iterator[str]:
        yield from sorted(self._state._children.get(self._prefix, ()))

    def __len__(self) -> int:
        return len(self._state._children.get(self._prefix, ()))
