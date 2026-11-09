"""Canonical immutable-file format and media-type contracts."""

FORMAT_MEDIA_TYPES: dict[str, str] = {
    "arrow-ipc-file": "application/vnd.apache.arrow.file",
    "binary": "application/octet-stream",
    "cog": "image/tiff",
    "copc": "application/vnd.laz",
    "csv": "text/csv",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "flatgeobuf": "application/flatgeobuf",
    "geojson": "application/geo+json",
    "geopackage": "application/geopackage+sqlite3",
    "geoparquet": "application/vnd.apache.parquet",
    "geotiff": "image/tiff",
    "json": "application/json",
    "las": "application/vnd.las",
    "laz": "application/vnd.laz",
    "markdown": "text/markdown",
    "npy": "application/x-npy",
    "parquet": "application/vnd.apache.parquet",
    "pdf": "application/pdf",
    "png": "image/png",
    "pytorch-state-dict": "application/vnd.pytorch",
    "text": "text/plain",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}

FORMATS = tuple(FORMAT_MEDIA_TYPES)
QUERYABLE_FORMATS = frozenset({"csv", "parquet"})
