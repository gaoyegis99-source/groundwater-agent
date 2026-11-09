"""One validated operational capacity profile for V6 Executor assembly."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os


_GIB = 1024**3


def _bounded_integer(
    values: Mapping[str, object], name: str, default: int, minimum: int, maximum: int
) -> int:
    """Read one profile value from an environment or a frozen audit mapping.

    Process environments carry strings while candidate metadata is JSON and
    therefore carries integers.  Treat both as the same operational profile;
    keeping that conversion here avoids a second capacity parser in candidate
    maintenance code.
    """

    raw = values.get(name, str(default))
    if isinstance(raw, bool):
        raise ValueError(f"{name}_INVALID")
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str) and raw.isascii() and raw.isdigit():
        value = int(raw)
    else:
        raise ValueError(f"{name}_INVALID")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name}_INVALID")
    return value


@dataclass(frozen=True)
class ExecutorOperationalProfile:
    """Capacity that is compiled into both the executor contract and Compose."""

    native_slots: int = 2
    native_memory_bytes: int = 16 * _GIB
    qgis_slots: int = 1
    qgis_memory_bytes: int = 8 * _GIB
    remote_http_slots: int = 2
    remote_http_memory_bytes: int = 2 * _GIB

    def __post_init__(self) -> None:
        for value, name, minimum, maximum in (
            (self.native_slots, "EXECUTOR_NATIVE_SLOTS", 1, 64),
            (self.qgis_slots, "EXECUTOR_QGIS_SLOTS", 1, 64),
            (self.native_memory_bytes, "EXECUTOR_NATIVE_MEMORY_BYTES", 256 * 1024**2, 1024**4),
            (self.qgis_memory_bytes, "EXECUTOR_QGIS_MEMORY_BYTES", 256 * 1024**2, 1024**4),
            (self.remote_http_slots, "EXECUTOR_REMOTE_HTTP_SLOTS", 1, 64),
            (self.remote_http_memory_bytes, "EXECUTOR_REMOTE_HTTP_MEMORY_BYTES", 256 * 1024**2, 1024**4),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                raise ValueError(f"{name}_INVALID")

    @classmethod
    def from_environment(
        cls, environ: Mapping[str, object] | None = None
    ) -> "ExecutorOperationalProfile":
        values = environ if environ is not None else os.environ
        return cls(
            native_slots=_bounded_integer(values, "EXECUTOR_NATIVE_SLOTS", 2, 1, 64),
            native_memory_bytes=_bounded_integer(
                values, "EXECUTOR_NATIVE_MEMORY_BYTES", 16 * _GIB, 256 * 1024**2, 1024**4
            ),
            qgis_slots=_bounded_integer(values, "EXECUTOR_QGIS_SLOTS", 1, 1, 64),
            qgis_memory_bytes=_bounded_integer(
                values, "EXECUTOR_QGIS_MEMORY_BYTES", 8 * _GIB, 256 * 1024**2, 1024**4
            ),
            remote_http_slots=_bounded_integer(
                values, "EXECUTOR_REMOTE_HTTP_SLOTS", 2, 1, 64
            ),
            remote_http_memory_bytes=_bounded_integer(
                values, "EXECUTOR_REMOTE_HTTP_MEMORY_BYTES", 2 * _GIB,
                256 * 1024**2, 1024**4
            ),
        )

    def worker_runtimes(self, selected: set[str]) -> dict[str, dict[str, int]]:
        capacities = {
            "native": {"slots": self.native_slots, "memoryBytes": self.native_memory_bytes},
            "qgis": {"slots": self.qgis_slots, "memoryBytes": self.qgis_memory_bytes},
            "remote_http": {
                "slots": self.remote_http_slots,
                "memoryBytes": self.remote_http_memory_bytes,
            },
        }
        if not selected or selected - set(capacities):
            raise ValueError("EXECUTOR_RUNTIME_PROFILE_INVALID")
        return {name: capacities[name] for name in sorted(selected)}

    def environment_values(self) -> dict[str, int]:
        return {
            "EXECUTOR_NATIVE_SLOTS": self.native_slots,
            "EXECUTOR_NATIVE_MEMORY_BYTES": self.native_memory_bytes,
            "EXECUTOR_QGIS_SLOTS": self.qgis_slots,
            "EXECUTOR_QGIS_MEMORY_BYTES": self.qgis_memory_bytes,
            "EXECUTOR_REMOTE_HTTP_SLOTS": self.remote_http_slots,
            "EXECUTOR_REMOTE_HTTP_MEMORY_BYTES": self.remote_http_memory_bytes,
        }
