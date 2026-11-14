"""Pure projection for deterministic discovery over current Data catalog rows."""

from __future__ import annotations

from calendar import monthrange
from datetime import date
import re
import unicodedata
from typing import Iterable, Mapping


def project_discovery(
    rows: Iterable[Mapping[str, object]],
    *,
    query: str = "",
    tags: tuple[str, ...] = (),
    formats: tuple[str, ...] = (),
    field_terms: tuple[str, ...] = (),
    units: tuple[str, ...] = (),
    quantity_kinds: tuple[str, ...] = (),
    time_window: tuple[date, date] | None = None,
    spatial_extent: dict | None = None,
    after: str | None = None,
    limit: int = 20,
) -> dict[str, object]:
    """Filter, explain, rank and page current catalog rows without I/O."""
    query_terms = _terms(query)
    requested_tags = {str(value).casefold() for value in tags}
    requested_formats = {str(value).casefold() for value in formats}
    requested_fields = _terms(" ".join(field_terms))
    requested_units = {_unit_key(value) for value in units}
    requested_quantities = set(quantity_kinds)
    matches = []
    for raw in rows:
        item = dict(raw)
        semantics = item.get("semantics") or {}
        descriptor = item.get("descriptor") or {}
        semantic_fields = semantics.get("fields") or {}
        physical_fields = descriptor.get("fields") or []
        field_texts, fields = _field_catalog(
            physical_fields, semantic_fields,
        )
        eligible_fields = [
            field for field in fields
            if (not requested_units or _unit_key(field["unit"]) in requested_units)
            and (not requested_quantities or field.get("quantity_kind") in requested_quantities)
        ]
        if (requested_units or requested_quantities) and not eligible_fields:
            continue
        if not all(any(term in _field_text(field) for field in eligible_fields)
                   for term in requested_fields):
            continue
        if time_window is not None and not _covers(semantics.get("time_range"), time_window):
            continue
        if spatial_extent is not None and not _intersects(semantics.get("spatial_extent"), spatial_extent):
            continue
        tag_values = {str(value).casefold() for value in item.get("tags") or []}
        format_values = {
            str(value).casefold() for value in item.get("formats") or []
        }
        title = str(item.get("title") or "")
        searchable = _fold(" ".join(
            (
                title,
                str(semantics.get("description") or ""),
                str(semantics.get("grain") or ""),
                str(semantics.get("time_range") or ""),
                *(str(value) for value in item.get("tags") or []),
                *field_texts,
            )
        ))
        folded_fields = _fold(" ".join(field_texts))
        if not _matches(
            requested_tags=requested_tags,
            requested_formats=requested_formats,
            query_terms=query_terms,
            tag_values=tag_values,
            format_values=format_values,
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
        if requested_quantities:
            reasons.append({"kind": "QUANTITY_FILTER", "values": sorted(requested_quantities)})
        if time_window is not None:
            reasons.append({"kind": "TIME_COVERAGE_FILTER"})
        if spatial_extent is not None:
            reasons.append({"kind": "SPATIAL_INTERSECTION_FILTER"})
        field_match_terms = requested_fields or (
            () if requested_units or requested_quantities else query_terms
        )
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
                "vertical_datum": semantics.get("vertical_datum"),
                "spatial_extent": semantics.get("spatial_extent"),
                "quality": None,
                "matched_fields": _matched_fields(eligible_fields, field_match_terms),
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
) -> tuple[list[str], list[dict[str, object]]]:
    field_texts = []
    fields = []
    for field in physical_fields:
        if not isinstance(field, dict):
            continue
        field_id = str(field.get("id") or "")
        field_name = str(field.get("name") or "")
        meaning = semantic_fields.get(field_id) or {}
        if not isinstance(meaning, dict):
            meaning = {}
        field_texts.extend((field_id, field_name))
        fields.append(
            {
                "id": field_id,
                "name": field_name,
                "meaning": str(meaning.get("meaning") or ""),
                "unit": meaning.get("unit"),
                **{key: meaning[key] for key in ("quantity_kind", "role") if meaning.get(key)},
            }
        )
    for field_id, meaning in semantic_fields.items():
        field_texts.append(str(field_id))
        if not isinstance(meaning, dict):
            continue
        field_texts.extend(str(meaning.get(key) or "") for key in ("meaning", "quantity_kind", "role"))
    return field_texts, fields


def _matches(
    *,
    requested_tags: set[str],
    requested_formats: set[str],
    query_terms: tuple[str, ...],
    tag_values: set[str],
    format_values: set[str],
    searchable: str,
) -> bool:
    return (
        (not requested_tags or requested_tags.issubset(tag_values))
        and (
            not requested_formats
            or bool(requested_formats.intersection(format_values))
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
    title_folded = _fold(title)
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
            term in _field_text(field)
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
            ("vertical_datum", not semantics.get("vertical_datum")),
            ("spatial_extent", not semantics.get("spatial_extent")),
        )
        if missing
    ]


def _terms(value: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            term
            for term in re.findall(r"[\w.-]+", _fold(value), flags=re.UNICODE)
            if term
        )
    )


def _fold(value: object) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).casefold().strip()


def _unit_key(value: object) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def _field_text(field: dict[str, object]) -> str:
    return _fold(" ".join(str(value or "") for value in field.values()))


def _date_boundary(value: str, *, end: bool) -> date:
    if re.fullmatch(r"[0-9]{4}", value):
        return date(int(value), 12 if end else 1, 31 if end else 1)
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}", value):
        year, month = map(int, value.split("-"))
        return date(year, month, monthrange(year, month)[1] if end else 1)
    return date.fromisoformat(value)


def _covers(declared: object, requested: tuple[date, date]) -> bool:
    if not isinstance(declared, str):
        return False
    try:
        start, end = declared.split("/")
        return (_date_boundary(start.strip(), end=False) <= requested[0]
                <= requested[1] <= _date_boundary(end.strip(), end=True))
    except ValueError:
        return False


def _intersects(declared: object, requested: dict) -> bool:
    if not isinstance(declared, dict) or _fold(declared["crs"]) != _fold(requested["crs"]):
        return False
    west, south, east, north = declared["bbox"]
    req_west, req_south, req_east, req_north = requested["bbox"]
    return west <= req_east and req_west <= east and south <= req_north and req_south <= north
