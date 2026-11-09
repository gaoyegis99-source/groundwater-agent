"""Environment-backed secret loading for Kernel settings."""

import os
from pathlib import Path


def read_api_key(variable: str) -> str:
    path = os.getenv(variable)
    if not path:
        raise ValueError(f"{variable}_REQUIRED")
    try:
        value = Path(path).read_text(encoding="utf-8").rstrip("\r\n")
    except (OSError, UnicodeError) as error:
        raise ValueError(f"{variable}_INVALID") from error
    if not value:
        raise ValueError(f"{variable}_INVALID")
    return value
