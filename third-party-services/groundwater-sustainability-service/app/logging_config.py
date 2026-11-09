from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from .config import Settings


def configure_logging(settings: Settings) -> None:
    settings.ensure_directories()
    root = logging.getLogger()
    root.setLevel(settings.log_level)
    if any(getattr(handler, "_gw_demo_handler", False) for handler in root.handlers):
        return

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s task_id=%(task_id)s %(message)s"
    )
    file_handler = RotatingFileHandler(
        settings.log_dir / "app.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler._gw_demo_handler = True  # type: ignore[attr-defined]
    file_handler.addFilter(TaskIdFilter())

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler._gw_demo_handler = True  # type: ignore[attr-defined]
    stream_handler.addFilter(TaskIdFilter())

    root.addHandler(file_handler)
    root.addHandler(stream_handler)


class TaskIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "task_id"):
            record.task_id = "-"
        return True
