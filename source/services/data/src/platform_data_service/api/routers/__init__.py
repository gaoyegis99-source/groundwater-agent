from .catalog import register_catalog_routes
from .content import register_content_routes
from .preview import register_preview_routes
from .mlops import register_mlops_routes
from .quality import register_quality_routes
from .publication import register_publication_routes
from .query import register_query_routes
from .semantics import register_semantic_routes
from .system import register_system_routes
from .tools import register_tool_routes

__all__ = (
    "register_catalog_routes", "register_content_routes", "register_preview_routes",
    "register_mlops_routes", "register_publication_routes", "register_query_routes", "register_semantic_routes",
    "register_quality_routes", "register_system_routes", "register_tool_routes",
)
