from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from .api import create_app as create_app
    from .composition import PlatformApplication as PlatformApplication

__all__ = ["PlatformApplication", "create_app"]


def __getattr__(name: str) -> Any:
    if name == "create_app":
        from .api import create_app

        return create_app
    if name == "PlatformApplication":
        from .composition import PlatformApplication

        return PlatformApplication
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
