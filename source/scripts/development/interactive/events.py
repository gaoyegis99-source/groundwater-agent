from __future__ import annotations

from collections import Counter, deque
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import threading
from typing import Any, Iterable


_SENSITIVE_KEY = re.compile(r"(secret|password|token|api.?key|credential)", re.I)


def _metric_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class EventStore:
    def __init__(
        self,
        *,
        root: Path,
        capacity: int = 500,
        log_path: Path | None = None,
    ) -> None:
        if capacity < 1:
            raise ValueError("DEV_EVENT_CAPACITY_INVALID")
        self.root = root.resolve()
        self.capacity = capacity
        self.log_path = log_path
        self._events: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._totals: Counter[str] = Counter()
        self._sequence = 0
        self._condition = threading.Condition()
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.touch(exist_ok=True)
            log_path.chmod(0o600)

    def _path(self, value: str | Path) -> str:
        path = Path(value)
        if not path.is_absolute():
            normalized = Path(str(value).replace("\\", "/"))
            if ".." not in normalized.parts:
                return normalized.as_posix()
            return "<invalid>"
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except (OSError, ValueError):
            return "<external>"

    def _details(self, value: dict[str, Any] | None) -> dict[str, Any]:
        if not value:
            return {}
        result: dict[str, Any] = {}
        for key, item in value.items():
            if _SENSITIVE_KEY.search(key):
                result[key] = "<redacted>"
            elif isinstance(item, (str, int, float, bool)) or item is None:
                result[key] = item
            elif isinstance(item, (list, tuple)) and all(
                isinstance(entry, (str, int, float, bool)) or entry is None
                for entry in item
            ):
                result[key] = list(item)
        return result

    def publish(
        self,
        kind: str,
        message: str,
        *,
        paths: Iterable[str | Path] = (),
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if re.fullmatch(r"[a-z][a-z0-9_.-]{0,79}", kind) is None:
            raise ValueError("DEV_EVENT_KIND_INVALID")
        with self._condition:
            self._sequence += 1
            event: dict[str, Any] = {
                "id": self._sequence,
                "time": datetime.now(timezone.utc)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z"),
                "kind": kind,
                "message": message[:500],
            }
            safe_paths = sorted({self._path(path) for path in paths})
            if safe_paths:
                event["paths"] = safe_paths
            safe_details = self._details(details)
            if safe_details:
                event["details"] = safe_details
            self._events.append(event)
            self._totals[kind] += 1
            if self.log_path is not None:
                with self.log_path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True))
                    stream.write("\n")
            self._condition.notify_all()
            return dict(event)

    def snapshot(self, *, after: int = 0) -> list[dict[str, Any]]:
        with self._condition:
            return [dict(event) for event in self._events if event["id"] > after]

    def wait_after(self, after: int, timeout: float) -> list[dict[str, Any]]:
        with self._condition:
            available = [event for event in self._events if event["id"] > after]
            if not available:
                self._condition.wait(timeout)
                available = [event for event in self._events if event["id"] > after]
            return [dict(event) for event in available]

    def render_metrics(self) -> str:
        with self._condition:
            lines = [
                "# HELP platform_dev_events_total Development control-plane events.",
                "# TYPE platform_dev_events_total counter",
            ]
            lines.extend(
                f'platform_dev_events_total{{kind="{_metric_escape(kind)}"}} {count}'
                for kind, count in sorted(self._totals.items())
            )
            lines.extend(
                (
                    "# HELP platform_dev_events_buffered Events retained in memory.",
                    "# TYPE platform_dev_events_buffered gauge",
                    f"platform_dev_events_buffered {len(self._events)}",
                    "# EOF",
                    "",
                )
            )
            return "\n".join(lines)
