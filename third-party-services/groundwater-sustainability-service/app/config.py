from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_path: Path
    results_dir: Path
    log_dir: Path
    log_level: str
    api_key: str = "development-only-key-change-me-000000000000"
    uploads_dir: Path | None = None
    max_upload_bytes: int = 1024**3
    upload_part_bytes: int = 16 * 1024**2

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(os.getenv("GW_DATA_DIR", "data")).resolve()
        key_file = os.getenv("GW_API_KEY_FILE")
        if key_file:
            try:
                api_key = Path(key_file).read_text(encoding="utf-8").strip()
            except OSError as error:
                raise RuntimeError("GW_API_KEY_FILE_UNREADABLE") from error
        else:
            api_key = os.getenv(
                "GW_API_KEY", "development-only-key-change-me-000000000000"
            ).strip()
        if len(api_key) < 32:
            raise RuntimeError("GW_API_KEY_TOO_SHORT")
        return cls(
            data_dir=data_dir,
            database_path=data_dir / "tasks.sqlite3",
            results_dir=data_dir / "results",
            log_dir=data_dir / "logs",
            log_level=os.getenv("GW_LOG_LEVEL", "INFO").upper(),
            api_key=api_key,
            uploads_dir=data_dir / "uploads",
            max_upload_bytes=int(os.getenv("GW_MAX_UPLOAD_BYTES", str(1024**3))),
            upload_part_bytes=int(
                os.getenv("GW_UPLOAD_PART_BYTES", str(16 * 1024**2))
            ),
        )

    def ensure_directories(self) -> None:
        if len(self.api_key) < 32:
            raise RuntimeError("GW_API_KEY_TOO_SHORT")
        if (
            self.max_upload_bytes <= 0
            or not 1024**2 <= self.upload_part_bytes <= 64 * 1024**2
        ):
            raise RuntimeError("GW_UPLOAD_LIMIT_INVALID")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.effective_uploads_dir.mkdir(parents=True, exist_ok=True)

    @property
    def effective_uploads_dir(self) -> Path:
        return self.uploads_dir or self.data_dir / "uploads"
