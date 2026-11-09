"""HTTP route registration for the Executor transport boundary."""

from .execution import register_execution_routes
from .system import register_system_routes
from .workers import register_worker_routes

__all__ = (
    "register_execution_routes",
    "register_system_routes",
    "register_worker_routes",
)
