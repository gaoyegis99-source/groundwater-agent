from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import threading
from typing import Callable
from urllib.parse import parse_qs, urlsplit

from .events import EventStore


_DASHBOARD = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>增量开发控制台</title><style>
body{font:14px/1.5 system-ui;margin:0;background:#10151d;color:#e8eef8}main{max-width:1100px;margin:auto;padding:24px}
h1{font-size:22px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}section{background:#18212e;border:1px solid #2e3c50;border-radius:10px;padding:16px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;color:#b9d6ff}a{color:#7ec8ff}@media(max-width:760px){.grid{grid-template-columns:1fr}}
</style></head><body><main><h1>增量开发控制台</h1><p>仅监听本机 loopback；状态、事件和指标均来自独立开发控制面。</p>
<p><a href="/status">JSON 状态</a> · <a href="/events">事件</a> · <a href="/metrics">控制器指标</a></p>
<div class="grid"><section><h2>状态</h2><pre id="status">载入中…</pre></section><section><h2>最近事件</h2><pre id="events">载入中…</pre></section></div>
<script>async function refresh(){for(const id of ['status','events']){try{const r=await fetch('/'+id,{cache:'no-store'});document.getElementById(id).textContent=JSON.stringify(await r.json(),null,2)}catch(e){document.getElementById(id).textContent=String(e)}}}refresh();setInterval(refresh,1500)</script>
</main></body></html>"""


def _metric_label(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _controller_metrics(
    events: EventStore, status_callback: Callable[[], dict[str, object]]
) -> str:
    event_lines = events.render_metrics().splitlines()
    if event_lines and event_lines[-1] == "# EOF":
        event_lines.pop()
    status = status_callback()
    supervisor = status.get("supervisor", {})
    runtime = status.get("runtime", {})
    supervisor = supervisor if isinstance(supervisor, dict) else {}
    runtime = runtime if isinstance(runtime, dict) else {}
    phase = _metric_label(status.get("phase", "UNKNOWN"))
    runtime_status = _metric_label(runtime.get("status", "UNKNOWN"))
    lines = [
        *event_lines,
        "# HELP platform_dev_controller_info Interactive controller state.",
        "# TYPE platform_dev_controller_info gauge",
        f'platform_dev_controller_info{{phase="{phase}"}} 1',
        "# HELP platform_dev_runtime_info Development runtime state.",
        "# TYPE platform_dev_runtime_info gauge",
        f'platform_dev_runtime_info{{status="{runtime_status}"}} 1',
        "# HELP platform_dev_supervisor_active Incremental batch currently active.",
        "# TYPE platform_dev_supervisor_active gauge",
        f"platform_dev_supervisor_active {int(bool(supervisor.get('active', False)))}",
    ]
    for field, metric in (
        ("submittedBatches", "submitted_batches_total"),
        ("completedBatches", "completed_batches_total"),
        ("failedBatches", "failed_batches_total"),
        ("supersededBatches", "superseded_batches_total"),
    ):
        value = supervisor.get(field, 0)
        lines.extend(
            (
                f"# HELP platform_dev_{metric} Incremental supervisor {field}.",
                f"# TYPE platform_dev_{metric} counter",
                f"platform_dev_{metric} {value if isinstance(value, int) else 0}",
            )
        )
    lines.extend(("# EOF", ""))
    return "\n".join(lines)


class ObservabilityServer:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        events: EventStore,
        status: Callable[[], dict[str, object]],
        service_metrics: Callable[[str], str | None],
    ) -> None:
        if host not in {"127.0.0.1", "localhost"}:
            raise ValueError("DEV_SERVER_LOOPBACK_REQUIRED")
        if not 0 <= port <= 65535:
            raise ValueError("DEV_SERVER_PORT_INVALID")
        self.host = host
        self.requested_port = port
        self.events = events
        self.status_callback = status
        self.service_metrics_callback = service_metrics
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._bound_port = port

    @property
    def port(self) -> int:
        if self._httpd is None:
            return self._bound_port
        return int(self._httpd.server_address[1])

    def start(self) -> None:
        if self._httpd is not None:
            return
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, _format: str, *_args: object) -> None:
                return

            def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, value: object) -> None:
                self._send(
                    HTTPStatus.OK,
                    (
                        json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n"
                    ).encode(),
                    "application/json; charset=utf-8",
                )

            def _stream(self, after: int) -> None:
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                cursor = after
                try:
                    while True:
                        batch = owner.events.wait_after(cursor, 15)
                        if not batch:
                            self.wfile.write(b": keepalive\n\n")
                            self.wfile.flush()
                            continue
                        for event in batch:
                            cursor = int(event["id"])
                            payload = json.dumps(
                                event, ensure_ascii=False, sort_keys=True
                            )
                            self.wfile.write(
                                f"id: {cursor}\nevent: update\ndata: {payload}\n\n".encode()
                            )
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return

            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API.
                parsed = urlsplit(self.path)
                if parsed.path == "/":
                    self._send(
                        HTTPStatus.OK,
                        _DASHBOARD.encode(),
                        "text/html; charset=utf-8",
                    )
                    return
                if parsed.path == "/status":
                    self._json(owner.status_callback())
                    return
                if parsed.path == "/events":
                    query = parse_qs(parsed.query)
                    try:
                        after = int(query.get("after", ["0"])[0])
                    except ValueError:
                        after = 0
                    self._json(owner.events.snapshot(after=max(0, after)))
                    return
                if parsed.path == "/events/stream":
                    query = parse_qs(parsed.query)
                    header = self.headers.get("Last-Event-ID", "0")
                    raw = query.get("after", [header])[0]
                    try:
                        after = max(0, int(raw))
                    except ValueError:
                        after = 0
                    self._stream(after)
                    return
                if parsed.path == "/metrics":
                    self._send(
                        HTTPStatus.OK,
                        _controller_metrics(
                            owner.events, owner.status_callback
                        ).encode(),
                        "application/openmetrics-text; version=1.0.0; charset=utf-8",
                    )
                    return
                prefix = "/metrics/services/"
                if parsed.path.startswith(prefix):
                    service = parsed.path.removeprefix(prefix)
                    if re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", service) is None:
                        self._send(HTTPStatus.NOT_FOUND, b"not found\n", "text/plain")
                        return
                    payload = owner.service_metrics_callback(service)
                    if payload is None:
                        self._send(
                            HTTPStatus.SERVICE_UNAVAILABLE,
                            b"service metrics unavailable\n",
                            "text/plain; charset=utf-8",
                        )
                        return
                    self._send(
                        HTTPStatus.OK,
                        payload.encode(),
                        "application/openmetrics-text; version=1.0.0; charset=utf-8",
                    )
                    return
                self._send(HTTPStatus.NOT_FOUND, b"not found\n", "text/plain")

        self._httpd = ThreadingHTTPServer((self.host, self.requested_port), Handler)
        self._httpd.daemon_threads = True
        self._bound_port = int(self._httpd.server_address[1])
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            name="dev-observability-server",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        httpd, thread = self._httpd, self._thread
        self._httpd = None
        self._thread = None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        if thread is not None:
            thread.join(3)
