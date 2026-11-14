from datetime import date
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator
from platform_protocol_contracts import CamelModel, reject_snake_wire

from ..models import BeginPublication, Model, Preview, Query as DataQuery, Semantics, SpatialExtent


class ToolActor(CamelModel):
    subject: str = Field(min_length=1, max_length=128)
    roles: list[str] = Field(default_factory=lambda: ["researcher"], min_length=1)


class ToolRequest(CamelModel):
    call_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    arguments: dict[str, Any]
    actor: ToolActor | None = None


class StrictWireModel:
    @model_validator(mode="before")
    @classmethod
    def strict_wire_fields(cls, value):
        reject_snake_wire(value, cls)
        return value


class SemanticUpdate(StrictWireModel, Model):
    expected_semantic_id: str = Field(min_length=1)
    semantics: Semantics


class DiscoveryRequest(StrictWireModel, Model):
    query: str = Field(default="", max_length=500)
    tags: list[str] = Field(default_factory=list, max_length=20)
    formats: list[str] = Field(default_factory=list, max_length=20)
    field_terms: list[str] = Field(default_factory=list, max_length=20)
    units: list[str] = Field(default_factory=list, max_length=20)
    quantity_kinds: list[Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{0,127}$")]] = Field(default_factory=list, max_length=20)
    time_window: tuple[date, date] | None = None
    spatial_extent: SpatialExtent | None = None
    after: str | None = Field(default=None, pattern=r"^[0-9]+$")
    limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def ordered_time_window(self):
        if self.time_window and self.time_window[0] > self.time_window[1]:
            raise ValueError("time window must be ordered")
        return self


class BeginPublicationRequest(StrictWireModel, BeginPublication):
    pass


class ResourceAccessUpdateRequest(StrictWireModel, Model):
    visibility: Literal["PRIVATE", "PROJECT"]


class RequiredFieldConstraint(StrictWireModel, Model):
    id: str = Field(min_length=1, max_length=128)
    kind: Literal["requiredField"]
    field: str = Field(min_length=1, max_length=256)


class TypeConstraint(StrictWireModel, Model):
    id: str = Field(min_length=1, max_length=128)
    kind: Literal["type"]
    field: str = Field(min_length=1, max_length=256)
    data_type: str = Field(min_length=1, max_length=128)


class NotNullConstraint(StrictWireModel, Model):
    id: str = Field(min_length=1, max_length=128)
    kind: Literal["notNull"]
    field: str = Field(min_length=1, max_length=256)


class RangeConstraint(StrictWireModel, Model):
    id: str = Field(min_length=1, max_length=128)
    kind: Literal["range"]
    field: str = Field(min_length=1, max_length=256)
    minimum: float | None = None
    maximum: float | None = None

    @model_validator(mode="after")
    def bounded(self):
        if self.minimum is None and self.maximum is None:
            raise ValueError("range requires minimum or maximum")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("range minimum exceeds maximum")
        return self


class LengthConstraint(StrictWireModel, Model):
    id: str = Field(min_length=1, max_length=128)
    kind: Literal["length"]
    field: str = Field(min_length=1, max_length=256)
    minimum: int | None = Field(default=None, ge=0)
    maximum: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def bounded(self):
        if self.minimum is None and self.maximum is None:
            raise ValueError("length requires minimum or maximum")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError("length minimum exceeds maximum")
        return self


class EnumConstraint(StrictWireModel, Model):
    id: str = Field(min_length=1, max_length=128)
    kind: Literal["enum"]
    field: str = Field(min_length=1, max_length=256)
    values: list[str | int | float | bool | None] = Field(min_length=1, max_length=1024)


class UniqueConstraint(StrictWireModel, Model):
    id: str = Field(min_length=1, max_length=128)
    kind: Literal["unique"]
    fields: list[str] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def unique_fields(self):
        if any(not field or len(field) > 256 for field in self.fields):
            raise ValueError("unique fields must be non-empty")
        if len(self.fields) != len(set(self.fields)):
            raise ValueError("duplicate unique fields")
        return self


TableValidationConstraint = Annotated[
    RequiredFieldConstraint
    | TypeConstraint
    | NotNullConstraint
    | RangeConstraint
    | LengthConstraint
    | EnumConstraint
    | UniqueConstraint,
    Field(discriminator="kind"),
]


class QualityRuleRequest(StrictWireModel, Model):
    severity: Literal["ERROR", "WARN", "INFO"]
    constraint: TableValidationConstraint


class QualitySuiteVersionRequest(StrictWireModel, Model):
    expected_latest_version_id: str | None = Field(default=None, max_length=128)
    title: str = Field(min_length=1, max_length=500)
    rules: list[QualityRuleRequest] = Field(min_length=1, max_length=1024)

    @model_validator(mode="after")
    def unique_rule_ids(self):
        identifiers = [rule.constraint.id for rule in self.rules]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("duplicate quality rule ids")
        return self


class QualityBindingUpdateRequest(StrictWireModel, Model):
    expected_revision: int = Field(ge=0)
    suite_version_id: str = Field(min_length=1, max_length=128)
    enabled: bool = True


class QualityRuleResultRequest(StrictWireModel, Model):
    rule_id: str = Field(min_length=1, max_length=128)
    status: Literal["PASS", "FAIL", "ERROR"]
    violation_count: int = Field(default=0, ge=0)
    code: str | None = Field(default=None, min_length=1, max_length=128)
    evidence: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def coherent_result(self):
        if self.status == "PASS" and self.violation_count != 0:
            raise ValueError("passing rules cannot report violations")
        if self.status == "FAIL" and self.violation_count == 0:
            raise ValueError("failed rules must report violations")
        if self.status == "ERROR" and not self.code:
            raise ValueError("errored rules require a code")
        return self


class QualityRunRequest(StrictWireModel, Model):
    idempotency_key: str = Field(min_length=1, max_length=128)
    dataset_id: str = Field(min_length=1, max_length=128)
    version_id: str = Field(min_length=1, max_length=128)
    semantic_id: str = Field(min_length=1, max_length=128)
    binding_revision: int = Field(ge=1)
    operator_run_id: str = Field(min_length=1, max_length=256)
    operator_id: Literal["table.validate"] = "table.validate"
    operator_version: Literal["1.0.0"] = "1.0.0"
    implementation_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    results: list[QualityRuleResultRequest] = Field(min_length=1, max_length=1024)

    @model_validator(mode="after")
    def unique_results(self):
        identifiers = [result.rule_id for result in self.results]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("duplicate quality rule results")
        return self


class MlExperimentRequest(StrictWireModel, Model):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=4000)


class MlModelAliasInput(StrictWireModel, Model):
    model_id: str = Field(min_length=1, max_length=128)
    alias: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")


class MlEnvironmentRequest(StrictWireModel, Model):
    python_version: str = Field(min_length=1, max_length=64)
    platform: str = Field(min_length=1, max_length=200)
    packages: dict[str, str] = Field(default_factory=dict)
    image_digest: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )

    @model_validator(mode="after")
    def bounded_packages(self):
        if len(self.packages) > 1000 or any(
            not name or len(name) > 200 or not version or len(version) > 200
            for name, version in self.packages.items()
        ):
            raise ValueError("invalid environment packages")
        return self


class MlOperatorImplementationRequest(StrictWireModel, Model):
    operator_id: str = Field(min_length=1, max_length=200)
    operator_version: str = Field(min_length=1, max_length=100)
    implementation_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class MlRunRequest(StrictWireModel, Model):
    idempotency_key: str = Field(min_length=1, max_length=128)
    experiment_id: str = Field(min_length=1, max_length=128)
    input_versions: list[str] = Field(default_factory=list, max_length=100)
    model_inputs: list[MlModelAliasInput] = Field(default_factory=list, max_length=32)
    parameters: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    metrics: dict[str, float] = Field(default_factory=dict)
    environment: MlEnvironmentRequest
    operator_implementations: list[MlOperatorImplementationRequest] = Field(
        default_factory=list, max_length=256
    )

    @model_validator(mode="after")
    def bounded_and_unique(self):
        if len(self.input_versions) != len(set(self.input_versions)):
            raise ValueError("duplicate ML input versions")
        aliases = [(item.model_id, item.alias) for item in self.model_inputs]
        if len(aliases) != len(set(aliases)):
            raise ValueError("duplicate ML model aliases")
        if len(self.parameters) > 1000 or len(self.metrics) > 1000:
            raise ValueError("too many ML parameters or metrics")
        if any(not key or len(key) > 200 for key in self.parameters | self.metrics):
            raise ValueError("invalid ML parameter or metric name")
        implementations = [
            (item.operator_id, item.operator_version)
            for item in self.operator_implementations
        ]
        if len(implementations) != len(set(implementations)):
            raise ValueError("duplicate operator implementations")
        return self


class ModelRegistryVersionRequest(StrictWireModel, Model):
    data_version_id: str = Field(min_length=1, max_length=128)
    experiment_run_id: str | None = Field(default=None, max_length=128)
    tags: list[str] = Field(default_factory=list, max_length=100)
    benchmark_status: Literal["NOT_ASSESSED", "PASSED", "FAILED"] = "NOT_ASSESSED"


class ModelBenchmarkUpdateRequest(StrictWireModel, Model):
    expected_status: Literal["NOT_ASSESSED", "PASSED", "FAILED"]
    benchmark_status: Literal["NOT_ASSESSED", "PASSED", "FAILED"]
    tags: list[str] = Field(default_factory=list, max_length=100)


class ModelAliasUpdateRequest(StrictWireModel, Model):
    expected_revision: int = Field(ge=0)
    registry_version: int = Field(ge=1)


class DataQueryRequest(StrictWireModel, DataQuery):
    pass


class PreviewRequest(StrictWireModel, Preview):
    pass
