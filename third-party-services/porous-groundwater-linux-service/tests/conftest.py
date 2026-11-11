"""Expose the same shared modules copied by stage_service.py into the image."""
from pathlib import Path

import porous_remote

# The service build copies these unmodified upload/error modules from the
# sustainability service. Tests use those sources, not replacement stubs.
porous_remote.__path__.append(str(Path(__file__).resolve().parents[2] /
                                'groundwater-sustainability-service/app'))
