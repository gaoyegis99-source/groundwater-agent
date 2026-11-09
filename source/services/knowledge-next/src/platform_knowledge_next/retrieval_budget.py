from collections.abc import Iterable, Mapping, Set
from typing import Any


UnitRows = Mapping[tuple[str, str], Any]


def required_context_ids(
    units: UnitRows, version_id: str, root_unit_id: str
) -> set[str]:
    """Resolve the complete transitive context for one unit, including cycles."""
    group: set[str] = set()
    pending = [root_unit_id]
    while pending:
        unit_id = pending.pop()
        if unit_id in group:
            continue
        group.add(unit_id)
        pending.extend(units[(version_id, unit_id)]["body"]["required_units"])
    return group


def additional_context_units(
    units: UnitRows,
    version_id: str,
    context_ids: Set[str],
    included: Set[tuple[str, str]],
) -> list[Any]:
    """Return context rows not already included in the current bundle."""
    return [
        units[(version_id, unit_id)]
        for unit_id in context_ids
        if (version_id, unit_id) not in included
    ]


def context_content_chars(units: Iterable[Any]) -> int:
    """Count content characters added by a required-context group."""
    return sum(len(unit["body"]["content"]) for unit in units)


def fits_context_budget(
    *,
    included_count: int,
    included_chars: int,
    additional_count: int,
    additional_chars: int,
    max_units: int,
    max_chars: int,
) -> bool:
    return (
        included_count + additional_count <= max_units
        and included_chars + additional_chars <= max_chars
    )
