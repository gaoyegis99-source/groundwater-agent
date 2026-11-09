"""Controlled, external research-corpus manifests for the Groundwater product.

This module deliberately stores *identity and governance metadata*, never a
copy of an authorised source document.  The original corpus stays outside the
repository and is verified only from an explicitly supplied external root.
It is therefore safe to version a manifest and its evaluation expectations
without accidentally turning the source repository into a second data store.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Iterable, Literal, Mapping

from pydantic import Field, model_validator

from platform_knowledge_next.models import DataBinding, Digest, ID, Model


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class CorpusTimeRange(Model):
    """Declared coverage, rather than an inferred date extracted from text."""

    start: date
    end: date

    @model_validator(mode="after")
    def ordered(self):
        if self.start > self.end:
            raise ValueError("CORPUS_TIME_RANGE_INVALID")
        return self


class AuthorizedCorpusSource(Model):
    """One externally held, explicitly authorised document source."""

    source_id: ID
    title: Annotated[str, Field(min_length=1, max_length=500)]
    authorization_id: Annotated[str, Field(min_length=1, max_length=160)]
    authorization_status: Literal["AUTHORIZED", "REVOKED", "PENDING"]
    document_version: Annotated[str, Field(min_length=1, max_length=100)]
    sha256: Digest
    # A manifest carries a relative identity only.  The caller supplies its
    # private root when verifying bytes, so no source-root path enters Git.
    relative_path: Annotated[str, Field(min_length=1, max_length=1024)]
    time_range: CorpusTimeRange
    coordinate_reference_system: Annotated[str, Field(min_length=1, max_length=256)]
    units: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(
        min_length=1, max_length=64
    )
    quality_rules: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(
        min_length=1, max_length=64
    )
    data_bindings: list[DataBinding] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def external_relative_path(self):
        path = PurePosixPath(self.relative_path)
        if path.is_absolute() or ".." in path.parts or "." in path.parts:
            raise ValueError("CORPUS_SOURCE_PATH_INVALID")
        if self.authorization_status != "AUTHORIZED":
            raise ValueError("CORPUS_SOURCE_NOT_AUTHORIZED")
        return self


class ControlledCorpusDocument(Model):
    """Expected Knowledge identity generated from an authorised source."""

    knowledge_id: ID
    content_version: ID
    source_id: ID
    expected_unit_count: Annotated[int, Field(ge=1, le=64)]


class ControlledCorpusManifestV1(Model):
    """Frozen manifest for one reproducible V6 research corpus import."""

    schema_version: Literal["groundwater-controlled-corpus.v1"] = (
        "groundwater-controlled-corpus.v1"
    )
    corpus_id: ID
    frozen_at: datetime
    sources: list[AuthorizedCorpusSource] = Field(min_length=1, max_length=128)
    documents: list[ControlledCorpusDocument] = Field(min_length=1, max_length=128)
    expected_document_count: Annotated[int, Field(ge=1, le=128)]
    expected_unit_count: Annotated[int, Field(ge=1, le=8192)]

    @model_validator(mode="after")
    def complete_and_unique(self):
        source_ids = [source.source_id for source in self.sources]
        document_ids = [document.knowledge_id for document in self.documents]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("DUPLICATE_CORPUS_SOURCE")
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("DUPLICATE_CORPUS_DOCUMENT")
        if set(document.source_id for document in self.documents) - set(source_ids):
            raise ValueError("CORPUS_DOCUMENT_SOURCE_UNKNOWN")
        if len(self.documents) != self.expected_document_count:
            raise ValueError("CORPUS_DOCUMENT_COUNT_MISMATCH")
        if sum(document.expected_unit_count for document in self.documents) != self.expected_unit_count:
            raise ValueError("CORPUS_UNIT_COUNT_MISMATCH")
        return self

    @property
    def manifest_digest(self) -> str:
        return _canonical_digest(self.model_dump(mode="json", by_alias=True))


def verify_external_sources(
    manifest: ControlledCorpusManifestV1, source_root: str | Path
) -> dict[str, Any]:
    """Verify byte identities without importing, copying, or reading text content.

    ``source_root`` must be an existing non-symlink directory outside this
    source tree.  A source under the repository would violate the product
    boundary and is rejected instead of being silently accepted.
    """

    root = Path(source_root)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ValueError("CORPUS_SOURCE_ROOT_INVALID")
    resolved_root = root.resolve(strict=True)
    repository_root = Path(__file__).resolve().parents[4]
    try:
        resolved_root.relative_to(repository_root)
    except ValueError:
        pass
    else:
        raise ValueError("CORPUS_SOURCE_ROOT_MUST_BE_EXTERNAL")

    rows = []
    for source in sorted(manifest.sources, key=lambda item: item.source_id):
        candidate = root.joinpath(*PurePosixPath(source.relative_path).parts)
        if candidate.is_symlink() or not candidate.is_file():
            raise ValueError("CORPUS_SOURCE_FILE_INVALID")
        resolved = candidate.resolve(strict=True)
        try:
            resolved.relative_to(resolved_root)
        except ValueError as exc:
            raise ValueError("CORPUS_SOURCE_PATH_ESCAPE") from exc
        observed = _sha256_file(resolved)
        if observed != source.sha256:
            raise ValueError("CORPUS_SOURCE_DIGEST_MISMATCH")
        rows.append(
            {
                "source_id": source.source_id,
                "relative_path": source.relative_path,
                "sha256": observed,
                "bytes": resolved.stat().st_size,
                "authorization_id": source.authorization_id,
            }
        )
    return {
        "schema_version": "groundwater-controlled-corpus-verification.v1",
        "status": "PASS",
        "corpus_id": manifest.corpus_id,
        "manifest_digest": manifest.manifest_digest,
        "source_count": len(rows),
        "rows": rows,
    }


def verify_import_baseline(
    manifest: ControlledCorpusManifestV1, versions: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    """Compare a completed Knowledge import with the frozen identity baseline.

    The caller supplies only import receipts/version views, never source text.
    This preserves the distinction between a reviewed corpus manifest and a
    successful service import.
    """

    observed = list(versions)
    expected = {
        (document.knowledge_id, document.content_version): document.expected_unit_count
        for document in manifest.documents
    }
    actual: dict[tuple[str, str], int] = {}
    for row in observed:
        # A compact review export may use snake_case, while V6's
        # VersionResponse carries the frozen document identity inside ``body``.
        # Never substitute a generated import/version ID for contentVersion.
        body = row.get("body")
        body = body if isinstance(body, Mapping) else {}
        knowledge_id = (
            row.get("knowledge_id")
            or row.get("knowledgeId")
            or row.get("document_id")
            or row.get("documentId")
            or body.get("knowledge_id")
            or body.get("knowledgeId")
        )
        content_version = (
            row.get("content_version")
            or row.get("contentVersion")
            or body.get("content_version")
            or body.get("contentVersion")
        )
        if knowledge_id is None or content_version is None:
            raise ValueError("CORPUS_IMPORTED_DOCUMENT_IDENTITY_INVALID")
        identity = (str(knowledge_id), str(content_version))
        if identity in actual:
            raise ValueError("CORPUS_IMPORTED_DOCUMENT_DUPLICATE")
        units = row.get("unit_count", row.get("unitCount"))
        if units is None and isinstance(body.get("units"), list):
            units = len(body["units"])
        if type(units) is not int or units < 1:
            raise ValueError("CORPUS_IMPORTED_UNIT_COUNT_INVALID")
        actual[identity] = units
    missing = sorted({"/".join(key) for key in expected.keys() - actual.keys()})
    unexpected = sorted({"/".join(key) for key in actual.keys() - expected.keys()})
    changed = sorted(
        "/".join(key)
        for key in expected.keys() & actual.keys()
        if expected[key] != actual[key]
    )
    passed = not (missing or unexpected or changed)
    return {
        "schema_version": "groundwater-controlled-corpus-import-baseline.v1",
        "status": "PASS" if passed else "FAIL",
        "corpus_id": manifest.corpus_id,
        "manifest_digest": manifest.manifest_digest,
        "expected": {
            "documents": manifest.expected_document_count,
            "units": manifest.expected_unit_count,
        },
        "observed": {
            "documents": len(actual),
            "units": sum(actual.values()),
        },
        "missing": missing,
        "unexpected": unexpected,
        "changed_unit_counts": changed,
    }
