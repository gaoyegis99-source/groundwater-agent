"""Pure projection for deterministic discovery over current Data catalog rows."""

from __future__ import annotations

import re
from typing import Iterable, Mapping


def project_discovery(
    rows: Iterable[Mapping[str, object]],
    *,
    query: str = "",
    tags: tuple[str, ...] = (),
    formats: tuple[str, ...] = (),
    field_terms: tuple[str, ...] = (),
    units: tuple[str, ...] = (),
    after: str | None = None,
    limit: int = 20,
) -> dict[str, object]:
    """Filter, explain, rank and page current catalog rows without I/O."""
    query_terms = _terms(query)
    requested_tags = {str(value).casefold() for value in tags}
    requested_formats = {str(value).casefold() for value in formats}
    requested_fields = _terms(" ".join(field_terms))
    requested_units = {str(value).casefold() for value in units}
    matches = []
    for raw in rows:
        item = dict(raw)
        semantics = item.get("semantics") or {}
        descriptor = item.get("descriptor") or {}
        semantic_fields = semantics.get("fields") or {}
        physical_fields = descriptor.get("fields") or []
        field_texts, fields, unit_values = _field_catalog(
            physical_fields, semantic_fields,
        )
        tag_values = {str(value).casefold() for value in item.get("tags") or []}
        format_values = {
            str(value).casefold() for value in item.get("formats") or []
        }
        title = str(item.get("title") or "")
        searchable = " ".join(
            (
                title,
                str(semantics.get("description") or ""),
                str(semantics.get("grain") or ""),
                str(semantics.get("time_range") or ""),
                *(str(value) for value in item.get("tags") or []),
                *field_texts,
            )
        ).casefold()
        folded_fields = " ".join(field_texts).casefold()
        if not _matches(
            requested_tags=requested_tags,
            requested_formats=requested_formats,
            requested_fields=requested_fields,
            requested_units=requested_units,
            query_terms=query_terms,
            tag_values=tag_values,
            format_values=format_values,
            unit_values=unit_values,
            folded_fields=folded_fields,
            searchable=searchable,
        ):
            continue
        score, reasons = _score_and_reasons(
            query_terms=query_terms,
            requested_tags=requested_tags,
            requested_formats=requested_formats,
            requested_fields=requested_fields,
            requested_units=requested_units,
            title=title,
            tag_values=tag_values,
            folded_fields=folded_fields,
        )
        field_match_terms = (*query_terms, *requested_fields)
        matches.append(
            {
                "id": item["id"],
                "title": title,
                "tags": item.get("tags") or [],
                "current_version": item["current_version"],
                "semantic_id": item["semantic_id"],
                "formats": item.get("formats") or [],
                "description": str(semantics.get("description") or "")[:500],
                "grain": semantics.get("grain"),
                "time_range": semantics.get("time_range"),
                "crs": semantics.get("crs"),
                "matched_fields": _matched_fields(fields, field_match_terms),
                "score": score,
                "match_reasons": reasons,
                "unknowns": _unknowns(
                    physical_fields=physical_fields,
                    semantic_fields=semantic_fields,
                    semantics=semantics,
                ),
            }
        )
    matches.sort(key=lambda item: (-item["score"], item["id"]))
    offset = 0 if after is None else int(after)
    page = matches[offset : offset + limit]
    next_offset = offset + len(page)
    return {
        "items": page,
        "next_cursor": str(next_offset) if next_offset < len(matches) else None,
        "total_matches": len(matches),
        "match_basis": "structured_current_catalog_projection.v1",
    }


def _field_catalog(
    physical_fields: list[object],
    semantic_fields: Mapping[str, object],
) -> tuple[list[str], list[dict[str, object]], set[str]]:
    field_texts = []
    fields = []
    unit_values = set()
    for field in physical_fields:
        if not isinstance(field, dict):
            continue
        field_id = str(field.get("id") or "")
        field_name = str(field.get("name") or "")
        meaning = semantic_fields.get(field_id) or {}
        field_texts.extend((field_id, field_name))
        fields.append(
            {
                "id": field_id,
                "name": field_name,
                "meaning": (
                    str(meaning.get("meaning") or "")
                    if isinstance(meaning, dict)
                    else ""
                ),
                "unit": meaning.get("unit") if isinstance(meaning, dict) else None,
            }
        )
    for field_id, meaning in semantic_fields.items():
        field_texts.append(str(field_id))
        if not isinstance(meaning, dict):
            continue
        field_texts.append(str(meaning.get("meaning") or ""))
        if meaning.get("unit"):
            unit_values.add(str(meaning["unit"]).casefold())
    return field_texts, fields, unit_values


def _matches(
    *,
    requested_tags: set[str],
    requested_formats: set[str],
    requested_fields: tuple[str, ...],
    requested_units: set[str],
    query_terms: tuple[str, ...],
    tag_values: set[str],
    format_values: set[str],
    unit_values: set[str],
    folded_fields: str,
    searchable: str,
) -> bool:
    return (
        (not requested_tags or requested_tags.issubset(tag_values))
        and (
            not requested_formats
            or bool(requested_formats.intersection(format_values))
        )
        and (
            not requested_fields
            or all(term in folded_fields for term in requested_fields)
        )
        and (
            not requested_units or bool(requested_units.intersection(unit_values))
        )
        and (not query_terms or all(term in searchable for term in query_terms))
    )


def _score_and_reasons(
    *,
    query_terms: tuple[str, ...],
    requested_tags: set[str],
    requested_formats: set[str],
    requested_fields: tuple[str, ...],
    requested_units: set[str],
    title: str,
    tag_values: set[str],
    folded_fields: str,
) -> tuple[int, list[dict[str, object]]]:
    reasons = []
    score = 0
    title_folded = title.casefold()
    for term in query_terms:
        if term in title_folded:
            score += 8
            reasons.append({"kind": "TITLE", "term": term})
        elif term in tag_values:
            score += 6
            reasons.append({"kind": "TAG", "term": term})
        elif term in folded_fields:
            score += 4
            reasons.append({"kind": "FIELD", "term": term})
        else:
            score += 2
            reasons.append({"kind": "SEMANTICS", "term": term})
    if requested_tags:
        reasons.append({"kind": "TAG_FILTER", "values": sorted(requested_tags)})
    if requested_formats:
        reasons.append(
            {"kind": "FORMAT_FILTER", "values": sorted(requested_formats)}
        )
    if requested_fields:
        reasons.append({"kind": "FIELD_FILTER", "values": list(requested_fields)})
    if requested_units:
        reasons.append({"kind": "UNIT_FILTER", "values": sorted(requested_units)})
    return score, reasons


def _matched_fields(
    fields: list[dict[str, object]],
    terms: tuple[str, ...],
) -> list[dict[str, object]]:
    return [
        field
        for field in fields
        if not terms
        or any(
            term
            in " ".join(str(value or "") for value in field.values()).casefold()
            for term in terms
        )
    ][:20]


def _unknowns(
    *,
    physical_fields: list[object],
    semantic_fields: Mapping[str, object],
    semantics: Mapping[str, object],
) -> list[str]:
    return [
        name
        for name, missing in (
            ("fields", not physical_fields),
            ("field_semantics", not semantic_fields),
            ("time_range", not semantics.get("time_range")),
            ("crs", not semantics.get("crs")),
        )
        if missing
    ]


def _terms(value: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            term.casefold()
            for term in re.findall(r"[\w.-]+", value, flags=re.UNICODE)
            if term
        )
    )
