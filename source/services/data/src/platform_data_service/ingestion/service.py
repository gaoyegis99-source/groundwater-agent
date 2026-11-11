import json
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import ValidationError

from ..adapters.readers import READERS
from ..semantics.service import check_semantics
from ..errors import DataProblemError
from ..models import BeginPublication, Metrics
from .field_profile import TableFieldProfiler
from .json_validation import validate_json


def prepare(payload, store, settings, work):
    manifest = BeginPublication.model_validate(payload["manifest"])
    prepared = []
    for output in manifest.outputs:
        raw = {
            **payload["uploads"][output.name],
            "role": "raw",
            "format": output.format,
            "media_type": output.media_type,
        }
        objects, metrics = [raw], []
        descriptor = {
            "fields": [],
            "row_count": None,
            "format": output.format,
            "policy": output.policy,
            "provenance": manifest.provenance,
            "quality": {"scope": "not_inspected"},
        }
        with store.open(raw) as source:
            if output.format == "json":
                validate_json(
                    source,
                    size=raw["size"],
                    max_bytes=settings.json_validation_bytes,
                )
                source.seek(0)
                descriptor["quality"] = {
                    "scope": "whole_file",
                    "checks": [
                        "utf8",
                        "json_syntax",
                        "duplicate_keys",
                        "finite_numbers",
                    ],
                }
            if output.policy == "FILE_QUERYABLE":
                iterator = READERS[output.format].read(source, output.conversion)
                names, schema = next(iterator)
                row_count = 0
                profiler = TableFieldProfiler(schema)
                normalized = work / f"{output.name}.parquet"
                full_schema = schema.append(pa.field("_row", pa.int64()))
                with pq.ParquetWriter(normalized, full_schema) as writer:
                    for batch in iterator:
                        profiler.consume(batch)
                        augmented = pa.RecordBatch.from_arrays(
                            [
                                *batch.columns,
                                pa.array(
                                    range(row_count, row_count + batch.num_rows),
                                    type=pa.int64(),
                                ),
                            ],
                            schema=full_schema,
                        )
                        writer.write_batch(augmented)
                        row_count += batch.num_rows
                        if normalized.stat().st_size > settings.temp_mb * 1024**2:
                            raise DataProblemError("PREPARE_DISK_BUDGET", 413)
                with normalized.open("rb") as stream:
                    normalized_object = store.put(stream, settings.upload_bytes)
                objects.append(
                    {
                        **normalized_object,
                        "role": "query",
                        "format": "parquet",
                        "media_type": "application/vnd.apache.parquet",
                        "source_checksum": raw["checksum"],
                        "conversion": output.conversion.model_dump(),
                        "converter": "data-service/1",
                        "arrow_version": pa.__version__,
                    }
                )
                descriptor.update(
                    fields=[
                        {
                            "id": f"f{i}",
                            "name": name,
                            "type": str(schema.types[i]),
                            **profiler.field(i),
                        }
                        for i, name in enumerate(names)
                    ],
                    row_count=row_count,
                    profile=profiler.descriptor(),
                    quality={
                        "scope": "all_rows",
                        "checks": ["parse", "conversion", "null_count"],
                        "duplicates": "not_checked",
                    },
                )
            elif output.policy == "FILE_AND_METRICS":
                if raw["size"] > 4 * 1024**2:
                    raise DataProblemError("METRICS_TOO_LARGE", 413)
                try:
                    metrics = Metrics.model_validate_json(source.read()).model_dump()[
                        "metrics"
                    ]
                except ValidationError:
                    raise DataProblemError("METRICS_INVALID") from None
                descriptor["quality"] = {
                    "scope": "all_metrics",
                    "checks": [
                        "utf8",
                        "json_syntax",
                        "duplicate_keys",
                        "finite_numbers",
                        "contract_validation",
                    ],
                }
        check_semantics(output.semantics, descriptor["fields"])
        prepared.append(
            {
                "output": output.model_dump(),
                "descriptor": descriptor,
                "objects": objects,
                "metrics": metrics,
            }
        )
    # Also proves the result is strictly serializable before recording it.
    json.dumps(prepared, allow_nan=False)
    return prepared
