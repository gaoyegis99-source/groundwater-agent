"""Interactive incremental-development control plane.

This package is development tooling. Runtime distributions and product
assemblies must never import or package it.
"""

from .profile import ChangePlan, classify_changes

__all__ = ["ChangePlan", "classify_changes"]
