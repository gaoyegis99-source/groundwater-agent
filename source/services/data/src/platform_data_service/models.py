from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from platform_protocol_contracts import to_camel

from .formats import FORMAT_MEDIA_TYPES, QUERYABLE_FORMATS


class Model(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        allow_inf_nan=False,
        populate_by_name=True,
    )


Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,128}$")]


class Conversion(Model):
    types: dict[
        str, Literal["string", "int64", "float64", "date", "timestamp", "bool"]
    ] = Field(default_factory=dict)
    null_values: list[str] = Field(default_factory=list, max_length=20)


class FieldMeaning(Model):
    meaning: str | None = None
    unit: str | None = None
    quantity_kind: str | None = Field(None, pattern=r"^[a-z][a-z0-9_.-]{0,127}$")
    role: str | None = Field(None, pattern=r"^[a-z][a-z0-9_.-]{0,127}$")
    source: str | None = Field(None, min_length=1, max_length=500)
    applicability: str | None = Field(None, min_length=1, max_length=1000)


class SpatialExtent(Model):
    crs: str = Field(min_length=1, max_length=200)
    bbox: tuple[float, float, float, float]

    @model_validator(mode="after")
    def ordered_bounds(self):
        west, south, east, north = self.bbox
        if not self.crs.strip() or west > east or south > north:
            raise ValueError("spatial extent requires a CRS and ordered bounds")
        return self


class Semantics(Model):
    description: str = Field(default="", max_length=10000)
    fields: dict[str, FieldMeaning] = Field(default_factory=dict)
    grain: str | None = None
    time_range: str | None = None
    timezone: str | None = None
    crs: str | None = None
    vertical_datum: str | None = None
    spatial_extent: SpatialExtent | None = None
    limitations: list[str] = Field(default_factory=list)
    source: str = Field(default="caller_declared", max_length=500)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("timezone must be an installed IANA zone") from None
        return value


class Output(Model):
    name: Identifier
    title: str = Field(min_length=1, max_length=500)
    dataset_id: Identifier | None = None
    expected_version: Identifier | None = None
    format: Literal[
        "arrow-ipc-file", "binary", "cog", "copc", "csv", "docx",
        "flatgeobuf", "geojson", "geopackage", "geoparquet", "geotiff",
        "json", "las", "laz", "markdown", "npy", "parquet", "pdf",
        "png", "pytorch-state-dict", "text", "xlsx", "zip",
    ]
    media_type: str | None = Field(default=None, min_length=1, max_length=200)
    policy: Literal["FILE_ONLY", "FILE_QUERYABLE", "FILE_AND_METRICS"] = "FILE_ONLY"
    tags: list[str] = Field(default_factory=list, max_length=30)
    conversion: Conversion = Field(default_factory=Conversion)
    semantics: Semantics = Field(default_factory=Semantics)

    @model_validator(mode="after")
    def compatible(self):
        expected_media_type = FORMAT_MEDIA_TYPES[self.format]
        if self.media_type is None:
            self.media_type = expected_media_type
        elif self.media_type != expected_media_type:
            raise ValueError("format and media_type do not match")
        if self.policy == "FILE_QUERYABLE" and self.format not in QUERYABLE_FORMATS:
            raise ValueError("queryable outputs require csv or parquet")
        if self.policy == "FILE_AND_METRICS" and self.format != "json":
            raise ValueError("metrics outputs require json")
        if bool(self.dataset_id) != bool(self.expected_version):
            raise ValueError("updating a dataset requires its expected current version")
        return self


class BeginPublication(Model):
    idempotency_key: Identifier
    outputs: list[Output] = Field(min_length=1, max_length=32)
    input_versions: list[Identifier] = Field(default_factory=list, max_length=100)
    provenance: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique(self):
        if len({x.name for x in self.outputs}) != len(self.outputs):
            raise ValueError("duplicate output names")
        targets = [x.dataset_id for x in self.outputs if x.dataset_id]
        if len(set(targets)) != len(targets):
            raise ValueError("duplicate target datasets")
        return self


class Metric(Model):
    name: str = Field(min_length=1, max_length=200)
    definition: str = Field(min_length=1, max_length=4000)
    unit: str = Field(min_length=1, max_length=100)
    evaluation_scope: str = Field(min_length=1, max_length=4000)
    value: float


class Metrics(Model):
    metrics: list[Metric] = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def unique(self):
        if len({x.name for x in self.metrics}) != len(self.metrics):
            raise ValueError("duplicate metric names")
        return self


class Filter(Model):
    field: Identifier
    op: Literal["eq", "ne", "lt", "le", "gt", "ge", "contains", "is_null"]
    value: str | int | float | bool | None = None


class Sort(Model):
    field: Identifier
    direction: Literal["asc", "desc"] = "asc"


class Aggregate(Model):
    function: Literal["count", "min", "max", "sum", "avg"]
    field: Identifier | None = None
    alias: Identifier

    @model_validator(mode="after")
    def require_field(self):
        if self.function != "count" and self.field is None:
            raise ValueError("aggregate field required")
        return self


class Query(Model):
    idempotency_key: Identifier
    kind: Literal["table", "metrics"] = "table"
    version_ids: list[Identifier] = Field(min_length=1, max_length=32)
    semantic_ids: dict[str, Identifier] = Field(default_factory=dict)
    fields: list[Identifier] = Field(default_factory=list, max_length=50)
    filters: list[Filter] = Field(default_factory=list, max_length=20)
    group_by: list[Identifier] = Field(default_factory=list, max_length=10)
    aggregates: list[Aggregate] = Field(default_factory=list, max_length=20)
    sort: list[Sort] = Field(default_factory=list, max_length=10)
    metric_name: str | None = None

    @model_validator(mode="after")
    def shape(self):
        if len(set(self.fields)) != len(self.fields) or len(set(self.group_by)) != len(
            self.group_by
        ):
            raise ValueError("duplicate query fields")
        if len(set(self.version_ids)) != len(self.version_ids):
            raise ValueError("duplicate versions")
        if self.kind == "table" and len(self.version_ids) != 1:
            raise ValueError("table queries require one version")
        if self.kind == "metrics" and (
            not self.metric_name
            or self.fields
            or self.filters
            or self.group_by
            or self.aggregates
            or self.sort
        ):
            raise ValueError(
                "metrics query accepts only versions, semantics and metric_name"
            )
        if self.aggregates and self.fields:
            raise ValueError("use group_by to select fields with aggregates")
        if self.group_by and not self.aggregates:
            raise ValueError("grouping requires aggregates")
        return self


class Preview(Model):
    idempotency_key: Identifier
    kind: Literal["xlsx_table", "geopackage_layer"]
    version_id: Identifier
    semantic_id: Identifier
    object_id: Identifier
    sheet_name: str | None = Field(default=None, min_length=1, max_length=200)
    layer_name: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def selector(self):
        if self.kind == "xlsx_table" and self.layer_name is not None:
            raise ValueError("xlsx previews do not accept layer_name")
        if self.kind == "geopackage_layer" and self.sheet_name is not None:
            raise ValueError("geopackage previews do not accept sheet_name")
        return self
