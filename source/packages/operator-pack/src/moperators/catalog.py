from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MEDIA_TYPE_FORMATS = {
    "application/geopackage+sqlite3": ["geopackage"],
    "application/geo+json": ["geojson"],
    "application/flatgeobuf": ["flatgeobuf"],
    "application/vnd.apache.parquet": ["parquet", "geoparquet"],
    "image/tiff": ["geotiff", "cog"],
    "image/png": ["png"],
    "application/vnd.las": ["las"],
    "application/vnd.laz": ["laz", "copc"],
    "text/csv": ["csv"],
    "application/json": ["json"],
    "application/vnd.apache.arrow.file": ["arrow-ipc-file"],
    "application/x-npy": ["npy"],
    "application/vnd.pytorch": ["pytorch-state-dict"],
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ["xlsx"],
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ["docx"],
    "application/pdf": ["pdf"],
    "text/plain": ["text"],
    "text/markdown": ["markdown"],
    "application/zip": ["zip"],
    "application/octet-stream": ["binary"],
}


class OperatorContractModel(BaseModel):
    """Frozen JSON contracts with idiomatic Python attribute names."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=False,
        serialize_by_alias=True,
    )


class RemoteBinding(OperatorContractModel):

    service_id: str = Field(alias="serviceId", pattern=r"^[a-z][a-z0-9.-]*$")
    operation_type: str = Field(
        alias="operationType", pattern=r"^[a-z][a-z0-9_.-]*$"
    )


class InputPort(OperatorContractModel):

    name: str
    cardinality: Literal["one", "many"]
    required: bool = True
    media_types: list[str] = Field(alias="mediaTypes")
    formats: list[str] = Field(default_factory=list)
    max_items: int | None = Field(default=None, alias="maxItems")


class FormatCase(OperatorContractModel):

    input_port: str = Field(alias="inputPort")
    input_formats: list[str] = Field(alias="inputFormats")
    media_type: str = Field(alias="mediaType")
    format: str


class ParameterFormatCase(OperatorContractModel):

    parameter: str
    values: list[str]
    media_type: str = Field(alias="mediaType")
    format: str


class OutputPort(OperatorContractModel):

    name: str
    kind: Literal["artifact", "value"]
    formats: list[str] = Field(default_factory=list)
    media_types: list[str] = Field(default_factory=list, alias="mediaTypes")
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")
    format_cases: list[FormatCase] | None = Field(default=None, alias="formatCases")
    parameter_format_cases: list[ParameterFormatCase] | None = Field(
        default=None, alias="parameterFormatCases"
    )


class OperatorDefinition(OperatorContractModel):

    id: str
    version: str
    title: str
    description: str = ""
    domain: str
    tags: list[str] = Field(default_factory=list)
    runtime: Literal["native", "qgis", "remote_http"]
    remote_binding: RemoteBinding | None = Field(default=None, alias="remoteBinding")
    timeout_seconds: int = Field(alias="timeoutSeconds", ge=1, le=21600)
    memory_limit_bytes: int = Field(
        default=8 * 1024**3, alias="memoryLimitBytes", ge=1
    )
    inputs: list[InputPort]
    parameters_schema: dict[str, Any] = Field(alias="parametersSchema")
    outputs: list[OutputPort]
    error_codes: list[str] = Field(alias="errorCodes")
    examples: list[dict[str, Any]] = Field(default_factory=list)
    implementation_revision: str = Field(default="", alias="implementationRevision")

    def model_post_init(self, __context: Any) -> None:
        if (self.runtime == "remote_http") != (self.remote_binding is not None):
            raise ValueError("remoteBinding is required only for remote_http operators")

    def public_description(self) -> dict[str, Any]:
        body = self.model_dump(
            by_alias=True,
            exclude={"runtime", "memoryLimitBytes"},
        )
        identity = {
            "id": body.pop("id"),
            "version": body.pop("version"),
            "implementationRevision": body.pop("implementationRevision"),
        }
        return {"schemaVersion": "operator-description.v1", "operator": identity, **body}


def _validate_port_formats(definition: OperatorDefinition) -> None:
    inputs = {port.name: port for port in definition.inputs}
    for port in [*definition.inputs, *definition.outputs]:
        if isinstance(port, OutputPort) and port.kind == "value":
            if port.format_cases is not None or port.parameter_format_cases is not None:
                raise ValueError(f"value output cannot have format cases: {definition.id}")
            continue
        pairs = {
            (media_type, format_id)
            for media_type in port.media_types
            for format_id in port.formats
            if format_id in MEDIA_TYPE_FORMATS.get(media_type, [])
        }
        if (
            not pairs
            or {media_type for media_type, _ in pairs} != set(port.media_types)
            or {format_id for _, format_id in pairs} != set(port.formats)
        ):
            raise ValueError(f"invalid port format pair: {definition.id}.{port.name}")
        if not isinstance(port, OutputPort):
            continue
        if port.format_cases is not None and port.parameter_format_cases is not None:
            raise ValueError(f"invalid format cases: {definition.id}.{port.name}")
        if port.format_cases is not None:
            cases = port.format_cases
            if not cases or len({case.input_port for case in cases}) != 1:
                raise ValueError(f"invalid format cases: {definition.id}.{port.name}")
            source = inputs.get(cases[0].input_port)
            if source is None:
                raise ValueError(f"invalid format cases: {definition.id}.{port.name}")
            covered: set[str] = set()
            case_pairs: set[tuple[str, str]] = set()
            for case in cases:
                formats = set(case.input_formats)
                if (
                    not formats
                    or len(formats) != len(case.input_formats)
                    or covered & formats
                    or not formats <= set(source.formats)
                ):
                    raise ValueError(f"invalid format cases: {definition.id}.{port.name}")
                covered |= formats
                case_pairs.add((case.media_type, case.format))
            if covered != set(source.formats) or case_pairs != pairs:
                raise ValueError(f"invalid format cases: {definition.id}.{port.name}")
            continue

        cases = port.parameter_format_cases
        if cases is None:
            continue
        if not cases or len({case.parameter for case in cases}) != 1:
            raise ValueError(
                f"invalid parameter format cases: {definition.id}.{port.name}"
            )
        selector = cases[0].parameter
        schema = definition.parameters_schema.get("properties", {}).get(selector)
        enum = schema.get("enum") if isinstance(schema, dict) else None
        if (
            not isinstance(schema, dict)
            or schema.get("type") != "string"
            or not isinstance(enum, list)
            or not enum
            or any(not isinstance(value, str) for value in enum)
            or len(enum) != len(set(enum))
        ):
            raise ValueError(
                f"invalid parameter format cases: {definition.id}.{port.name}"
            )
        covered_values: set[str] = set()
        for case in cases:
            values = set(case.values)
            if (
                not values
                or len(values) != len(case.values)
                or covered_values & values
                or not values <= set(enum)
                or (case.media_type, case.format) not in pairs
            ):
                raise ValueError(
                    f"invalid parameter format cases: {definition.id}.{port.name}"
                )
            covered_values |= values
        if covered_values != set(enum):
            raise ValueError(
                f"invalid parameter format cases: {definition.id}.{port.name}"
            )


class SmokeOperatorRef(OperatorContractModel):

    id: str
    version: str


class SmokeInputRef(OperatorContractModel):

    fixture_id: str = Field(alias="fixtureId")


class SmokeExpectedOutput(OperatorContractModel):

    kind: Literal["artifact", "value"]
    media_type: str | None = Field(default=None, alias="mediaType")
    format: str | None = None


class OperatorSmokeCase(OperatorContractModel):

    schema_version: Literal["operator-smoke-test.v1"] = Field(alias="schemaVersion")
    operator: SmokeOperatorRef
    inputs: dict[str, SmokeInputRef | list[SmokeInputRef]]
    parameters: dict[str, Any]
    expected_outputs: dict[str, SmokeExpectedOutput] = Field(alias="expectedOutputs")


class FixtureDefinition(OperatorContractModel):

    path: str
    media_type: str = Field(alias="mediaType")
    format: str
    size_bytes: int = Field(alias="sizeBytes", ge=1)
    sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _contains_array_schema(value: Any) -> bool:
    if isinstance(value, dict):
        schema_type = value.get("type")
        if schema_type == "array" or (isinstance(schema_type, list) and "array" in schema_type):
            return True
        return any(_contains_array_schema(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_array_schema(item) for item in value)
    return False


def _example_value(name: str, schema: dict[str, Any]) -> Any:
    if "default" in schema:
        return schema["default"]
    if choices := schema.get("enum"):
        return choices[0]
    if name in {"targetCrs", "sourceCrs"}:
        return "EPSG:3857"
    if name == "expression":
        return "r1_b1"
    value_type = schema.get("type")
    if value_type == "string":
        return "value"
    if value_type == "integer":
        return max(1, schema.get("minimum", 1))
    if value_type == "number":
        return max(1.0, schema.get("minimum", 1.0))
    if value_type == "boolean":
        return False
    return None


def _default_example(raw: dict[str, Any]) -> dict[str, Any]:
    inputs: dict[str, Any] = {}
    artifact_number = 1
    for port in raw["inputs"]:
        reference = {"artifactId": f"art_example_{artifact_number:02d}"}
        inputs[port["name"]] = (
            [reference, {"artifactId": "art_example_99"}]
            if port["cardinality"] == "many"
            else reference
        )
        artifact_number += 1
    schema = raw["parametersSchema"]
    parameters = {
        name: _example_value(name, schema["properties"][name])
        for name in schema.get("required", [])
    }
    return {
        "schemaVersion": "operator-execution-request.v1",
        "clientRequestId": f"example-{raw['id'].replace('.', '-')}",
        "operator": {"id": raw["id"], "version": raw["version"]},
        "mode": "ASYNC",
        "inputs": inputs,
        "parameters": parameters,
    }


def _implementation_revision(raw: dict[str, Any], manifest: bytes) -> str:
    package_root = Path(__file__).parent
    files = [
        package_root / "operators" / "native.py",
        package_root / "operators" / "expressions.py",
        package_root / "runtime" / "task.py",
    ]
    if raw["id"].startswith("hydrology."):
        files.append(package_root / "operators" / "hydrology.py")
    if raw["runtime"] == "qgis":
        files.append(package_root / "operators" / "qgis.py")
    digest = hashlib.sha256(manifest)
    for path in files:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return f"sha256:{digest.hexdigest()}"


@dataclass(frozen=True)
class _ImplementationLock:
    source_root: Path
    source_prefix: Path | None
    entries: dict[str, dict[str, Any]]


def _implementation_lock(directory: Path) -> _ImplementationLock | None:
    lock_path = directory.parent / "implementation-lock.json"
    if not lock_path.is_file():
        return None
    document = json.loads(lock_path.read_text(encoding="utf-8"))
    if document.get("schemaVersion") != "operator-implementation-lock.v1":
        raise ValueError(f"unsupported implementation lock: {lock_path}")
    operators = document.get("operators")
    if not isinstance(operators, dict):
        raise ValueError(f"invalid implementation lock: {lock_path}")
    if directory.parent.name == "catalog_data":
        return _ImplementationLock(
            source_root=directory.parents[2].resolve(),
            source_prefix=Path("src"),
            entries=operators,
        )
    return _ImplementationLock(
        source_root=directory.parent.parent.resolve(),
        source_prefix=None,
        entries=operators,
    )


def _locked_implementation_revision(
    implementation_lock: _ImplementationLock,
    identity: tuple[str, str],
) -> str:
    key = f"{identity[0]}@{identity[1]}"
    entry = implementation_lock.entries.get(key)
    if not isinstance(entry, dict):
        raise ValueError(f"implementation lock is missing: {key}")
    revision = entry.get("implementationRevision")
    source_digest = entry.get("sourceDigest")
    source_files = entry.get("sourceFiles")
    if (
        not isinstance(revision, str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", revision) is None
        or not isinstance(source_digest, str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", source_digest) is None
        or not isinstance(source_files, list)
        or not source_files
        or any(not isinstance(item, str) or not item for item in source_files)
    ):
        raise ValueError(f"invalid implementation lock entry: {key}")
    digest = hashlib.sha256()
    for relative in source_files:
        source_path = Path(relative)
        if implementation_lock.source_prefix is not None:
            try:
                source_path = source_path.relative_to(
                    implementation_lock.source_prefix
                )
            except ValueError as error:
                raise ValueError(
                    f"implementation source is invalid: {key}"
                ) from error
        path = (implementation_lock.source_root / source_path).resolve()
        if (
            not path.is_relative_to(implementation_lock.source_root)
            or not path.is_file()
        ):
            raise ValueError(f"implementation source is invalid: {key}")
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    actual = f"sha256:{digest.hexdigest()}"
    if actual != source_digest:
        raise ValueError(f"implementation source digest mismatch: {key}")
    return revision


@dataclass(frozen=True)
class OperatorCatalog:
    operators: tuple[OperatorDefinition, ...]
    digest: str
    smoke_cases: dict[tuple[str, str], OperatorSmokeCase]
    manifest_digests: dict[tuple[str, str], str]
    manifest_documents: dict[tuple[str, str], dict[str, Any]]
    fixtures: dict[str, FixtureDefinition]
    fixture_root: Path

    @classmethod
    def load(cls, directory: Path) -> OperatorCatalog:
        definitions: list[OperatorDefinition] = []
        canonical_documents: list[bytes] = []
        smoke_cases: dict[tuple[str, str], OperatorSmokeCase] = {}
        manifest_digests: dict[tuple[str, str], str] = {}
        manifest_documents: dict[tuple[str, str], dict[str, Any]] = {}
        fixture_root = directory.parent / "fixtures"
        fixture_index_path = fixture_root / "index.json"
        fixtures: dict[str, FixtureDefinition] = {}
        implementation_lock = _implementation_lock(directory)
        if fixture_index_path.is_file():
            fixture_document = json.loads(fixture_index_path.read_text(encoding="utf-8"))
            if fixture_document.get("schemaVersion") != "operator-fixtures.v1":
                raise ValueError(f"unsupported fixture index: {fixture_index_path}")
            fixtures = {
                fixture_id: FixtureDefinition.model_validate(value)
                for fixture_id, value in fixture_document["fixtures"].items()
            }
        paths = [*directory.glob("*.json"), *directory.glob("*/*/manifest.json")]
        for path in sorted(paths):
            raw = json.loads(path.read_text(encoding="utf-8"))
            for port in raw["inputs"]:
                port.setdefault(
                    "formats",
                    sorted(
                        {
                            format_id
                            for media_type in port["mediaTypes"]
                            for format_id in MEDIA_TYPE_FORMATS[media_type]
                        }
                    ),
                )
            schema = raw.get("parametersSchema", {})
            if _contains_array_schema(schema):
                raise ValueError(f"{path.name}: array parameters are not allowed")
            if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
                raise ValueError(f"{path.name}: parametersSchema must be a strict object")
            if not raw.get("examples"):
                raise ValueError(f"{path}: at least one explicit example is required")
            canonical = _canonical_json(raw)
            identity_from_manifest = (str(raw["id"]), str(raw["version"]))
            if implementation_lock is not None:
                revision = _locked_implementation_revision(
                    implementation_lock, identity_from_manifest
                )
                raw["implementationRevision"] = revision
            else:
                raw["implementationRevision"] = _implementation_revision(raw, canonical)
            definition = OperatorDefinition.model_validate(raw)
            _validate_port_formats(definition)
            identity = (definition.id, definition.version)
            if identity in manifest_documents:
                raise ValueError(
                    f"duplicate operator version: {definition.id}@{definition.version}"
                )
            if path.name == "manifest.json":
                expected_parent = directory / definition.id / definition.version
                if path.parent != expected_parent:
                    expected = f"{definition.id}/{definition.version}/manifest.json"
                    raise ValueError(f"{path}: path must match {expected}")
                smoke_path = path.with_name("smoke.json")
                if not smoke_path.is_file():
                    raise ValueError(f"smoke case is missing: {smoke_path}")
                smoke = OperatorSmokeCase.model_validate_json(
                    smoke_path.read_text(encoding="utf-8")
                )
                if (smoke.operator.id, smoke.operator.version) != identity:
                    raise ValueError(f"smoke case identity does not match manifest: {smoke_path}")
                smoke_cases[identity] = smoke
            definitions.append(definition)
            canonical_documents.append(_canonical_json(raw))
            manifest_digests[identity] = f"sha256:{hashlib.sha256(canonical).hexdigest()}"
            manifest_documents[identity] = {
                key: value for key, value in raw.items() if key != "implementationRevision"
            }
        if not definitions:
            raise ValueError(f"operator catalog is empty: {directory}")
        if implementation_lock is not None:
            identities = {f"{item.id}@{item.version}" for item in definitions}
            if set(implementation_lock.entries) != identities:
                raise ValueError("implementation lock does not match the operator catalog")
        digest = hashlib.sha256(b"\n".join(canonical_documents)).hexdigest()
        return cls(
            tuple(definitions),
            f"sha256:{digest}",
            smoke_cases,
            manifest_digests,
            manifest_documents,
            fixtures,
            fixture_root,
        )

    def get(self, operator_id: str, version: str) -> OperatorDefinition | None:
        return next(
            (item for item in self.operators if item.id == operator_id and item.version == version),
            None,
        )

    def versions(self, operator_id: str) -> tuple[OperatorDefinition, ...]:
        return tuple(item for item in self.operators if item.id == operator_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Print the static operator catalog digest")
    parser.add_argument(
        "--catalog-dir", type=Path,
        default=Path(__file__).with_name("catalog_data") / "operators",
    )
    catalog = OperatorCatalog.load(parser.parse_args().catalog_dir)
    print(catalog.digest)
