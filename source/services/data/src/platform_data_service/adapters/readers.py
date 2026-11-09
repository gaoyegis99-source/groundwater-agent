import csv
from datetime import date, datetime
import pyarrow as pa
import pyarrow.parquet as pq

from ..errors import DataProblemError


TYPES = {
    "string": pa.string(),
    "int64": pa.int64(),
    "float64": pa.float64(),
    "date": pa.date32(),
    "timestamp": pa.timestamp("us"),
    "bool": pa.bool_(),
}


def convert(value, kind):
    if kind == "string":
        return value
    if kind == "int64":
        return int(value)
    if kind == "float64":
        import math

        result = float(value)
        if not math.isfinite(result):
            raise ValueError("nonfinite")
        return result
    if kind == "date":
        return date.fromisoformat(value)
    if kind == "timestamp":
        result = datetime.fromisoformat(value)
        if result.tzinfo:
            raise ValueError("timezone-aware CSV timestamps require pre-normalization")
        return result
    if kind == "bool" and value.lower() in {"true", "false"}:
        return value.lower() == "true"
    raise ValueError("invalid conversion")


class CSVReader:
    def read(self, stream, conversion):
        import io

        wrapper = io.TextIOWrapper(stream, encoding="utf-8-sig", newline="")
        try:
            reader = csv.reader(wrapper, strict=True)
            names = next(reader, None)
            if not names:
                raise DataProblemError("EMPTY_TABLE")
            ids = [f"f{i}" for i in range(len(names))]
            if set(conversion.types) - set(ids):
                raise DataProblemError("UNKNOWN_CONVERSION_FIELD")
            kinds = [conversion.types.get(fid, "string") for fid in ids]
            schema = pa.schema([(fid, TYPES[kind]) for fid, kind in zip(ids, kinds)])
            yield names, schema
            columns = [[] for _ in names]
            for index, values in enumerate(reader, 2):
                if len(values) != len(names):
                    raise DataProblemError(
                        "CSV_COLUMN_COUNT",
                        row=index,
                        expected=len(names),
                        actual=len(values),
                    )
                for i, (value, kind) in enumerate(zip(values, kinds)):
                    try:
                        val = (
                            None
                            if value in conversion.null_values
                            else convert(value, kind)
                        )
                        # Validate overflow before buffering, preserving the error location.
                        pa.scalar(val, type=TYPES[kind])
                        columns[i].append(val)
                    except (ValueError, OverflowError, pa.ArrowException):
                        raise DataProblemError(
                            "CONVERSION_INVALID",
                            row=index,
                            field=ids[i],
                            target_type=kind,
                        ) from None
                if len(columns[0]) == 4096:
                    yield pa.RecordBatch.from_arrays(
                        [pa.array(x, type=t) for x, t in zip(columns, schema.types)],
                        schema=schema,
                    )
                    columns = [[] for _ in names]
            if columns[0]:
                yield pa.RecordBatch.from_arrays(
                    [pa.array(x, type=t) for x, t in zip(columns, schema.types)],
                    schema=schema,
                )
        except (csv.Error, UnicodeError):
            raise DataProblemError("CSV_INVALID") from None
        finally:
            wrapper.detach()


class ParquetReader:
    def read(self, stream, conversion):
        if conversion.types or conversion.null_values:
            raise DataProblemError("PARQUET_PHYSICAL_TYPES_REQUIRED")
        try:
            source = pq.ParquetFile(stream)
            original = source.schema_arrow
            schema = pa.schema(
                [(f"f{i}", field.type) for i, field in enumerate(original)]
            )
            yield original.names, schema
            for batch in source.iter_batches(batch_size=4096):
                yield pa.RecordBatch.from_arrays(batch.columns, schema=schema)
        except pa.ArrowException:
            raise DataProblemError("PARQUET_INVALID") from None


READERS = {"csv": CSVReader(), "parquet": ParquetReader()}
