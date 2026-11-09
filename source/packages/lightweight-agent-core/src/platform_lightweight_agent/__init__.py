"""A small, embeddable workflow-Agent kernel.

The package deliberately has no mandatory model, HTTP, database, Web, or
container dependency.  Applications select those integrations at their edge.
"""

from importlib import import_module
from typing import Any

from ._legacy_exports import EXPORT_MODULE, LEGACY_ALL

__all__ = LEGACY_ALL


def __getattr__(name: str) -> Any:
    module = EXPORT_MODULE.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f".{module}", __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(LEGACY_ALL))
