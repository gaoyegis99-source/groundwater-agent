"""Knowledge HTTP route groups."""

from .documents import register_document_routes
from .imports import register_import_routes
from .retrieval import register_retrieval_routes
from .system import register_system_routes
from .tools import register_tool_routes

__all__ = (
    "register_document_routes", "register_import_routes", "register_retrieval_routes",
    "register_system_routes", "register_tool_routes",
)
