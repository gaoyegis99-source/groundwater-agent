"""Deterministic browser probes for V6 console identity and business status.

This is a defect-discovery executable, not a product test that turns known
failures green.  It serves the repository's unchanged console assets, drives a
real Chromium process through Playwright, and writes structured contract
results.  The HTTP service is loopback-only and records no control-token value.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Event, Lock, Thread
import time
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Browser, Page, sync_playwright


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
CONSOLE_ROOT = PACKAGE_ROOT / "src" / "platform_lightweight_agent" / "console"
CHROMIUM = Path.home() / ".cache/ms-playwright/chromium-1243/chrome-linux64/chrome"


def _run_item(run_id: str) -> dict[str, object]:
    suffix = run_id.rsplit("-", 1)[-1]
    return {
        "run_id": run_id,
        "execution_id": f"execution-{suffix}",
        "status": "WAITING_APPROVAL",
        "workflow_id": "probe.workflow",
        "workflow_version": "1",
        "pending_approval_count": 1,
        "failure_code": None,
    }


def _view(
    run_id: str, *, business: str | None = None, security: bool = False
) -> dict[str, object]:
    output: dict[str, object] | None = None
    business_result: dict[str, object] | None = None
    status = "WAITING_APPROVAL"
    if business is not None:
        status = "SUCCEEDED" if business != "EXECUTION_ERROR" else "BLOCKED"
        if business != "EXECUTION_ERROR":
            output = {
                "business_status": business,
                "reason_code": (
                    None if business == "APPROVED" else "CSV_VALUE_NOT_NUMBER"
                ),
                "result_file": None,
                "summary": None,
                "validation": {"status": "FAIL" if business == "BLOCKED" else "PASS"},
            }
            business_result = {
                "status": business,
                "reason_code": output["reason_code"],
            }
    result = {
        "schema_version": "v6-run-view-v1",
        "run_id": run_id,
        "execution_id": f"execution-{run_id.rsplit('-', 1)[-1]}",
        "revision": 1,
        "execution_status": status,
        "business_result": business_result,
        "output": output,
        "explanation": _explanation(run_id, business=business, security=security),
        "progress": [],
    }
    if security:
        result["model_text"] = (
            '<img src=x onerror="window.__v6_probe_xss=true">'
            "<script>window.__v6_probe_xss=true</script>"
        )
    return result


def _explanation(
    run_id: str, *, business: str | None = None, security: bool = False
) -> dict[str, object]:
    suffix = run_id.rsplit("-", 1)[-1]
    if business is None:
        pending = [
            {
                "approval_id": f"approval-{suffix}",
                "step_id": f"approve-{suffix}",
            }
        ]
        reason = {"code": "APPROVAL_REQUIRED"}
    else:
        pending = []
        reason = {
            "code": "WORKFLOW_SUCCEEDED" if business != "EXECUTION_ERROR" else "TOOL_FAILED"
        }
    if security:
        reason["code"] = '<script>window.__v6_probe_xss=true</script>'
    return {"reason": reason, "pending_approvals": pending}


@dataclass
class ProbeState:
    delayed_component: str | None = None
    business_variant: str | None = None
    security_mode: bool = False
    delay_started: Event = field(default_factory=Event)
    release_delay: Event = field(default_factory=Event)
    stop_streams: Event = field(default_factory=Event)
    requests: list[dict[str, object]] = field(default_factory=list)
    lock: Lock = field(default_factory=Lock)

    def record(self, method: str, path: str, body: object = None) -> None:
        with self.lock:
            # Deliberately omit all headers: the synthetic control token must
            # not enter either raw or delivered evidence.
            self.requests.append({"method": method, "path": path, "body": body})

    def controls(self) -> list[dict[str, object]]:
        with self.lock:
            return [item for item in self.requests if item["method"] == "POST"]


class ProbeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, state: ProbeState) -> None:
        self.state = state
        super().__init__(("127.0.0.1", 0), ProbeHandler)


class ProbeHandler(BaseHTTPRequestHandler):
    server: ProbeServer

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _send(
        self, status: int, body: bytes, content_type: str = "application/json"
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # Identity trials deliberately abort the stale run-A request when
            # run-B is selected.  That is the behavior under test, not a
            # synthetic server failure worth printing to stderr.
            return

    def _json(self, value: object, status: int = 200) -> None:
        self._send(status, json.dumps(value).encode("utf-8"))

    def _maybe_delay(self, run_id: str, component: str) -> None:
        state = self.server.state
        if run_id == "run-A" and state.delayed_component == component:
            state.delay_started.set()
            if not state.release_delay.wait(10):
                raise TimeoutError(f"probe did not release delayed {component}")

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        parsed = urlparse(self.path)
        path = parsed.path
        self.server.state.record("GET", self.path)
        if path == "/console/":
            self._send(200, (CONSOLE_ROOT / "index.html").read_bytes(), "text/html")
            return
        if path == "/console/assets/app.js":
            self._send(
                200,
                (CONSOLE_ROOT / "app.js").read_bytes(),
                "application/javascript",
            )
            return
        if path == "/console/assets/style.css":
            self._send(200, (CONSOLE_ROOT / "style.css").read_bytes(), "text/css")
            return
        if path == "/v2/runs":
            if self.server.state.business_variant is None and not self.server.state.security_mode:
                items = [_run_item("run-A"), _run_item("run-B")]
            else:
                items = [_run_item("run-A")]
                if self.server.state.business_variant is not None:
                    items[0]["status"] = (
                        "BLOCKED"
                        if self.server.state.business_variant == "EXECUTION_ERROR"
                        else "SUCCEEDED"
                    )
                    items[0]["pending_approval_count"] = 0
            self._json({"items": items, "next_cursor": None})
            return
        parts = path.strip("/").split("/")
        if len(parts) >= 4 and parts[:2] == ["v2", "runs"]:
            run_id, component = parts[2], parts[3]
            query = parse_qs(parsed.query)
            expected = f"execution-{run_id.rsplit('-', 1)[-1]}"
            if query.get("expected_execution_id") != [expected]:
                self._json({"code": "RUN_EXECUTION_MISMATCH"}, 409)
                return
            if component == "view":
                self._maybe_delay(run_id, "view")
                self._json(
                    _view(
                        run_id,
                        business=self.server.state.business_variant,
                        security=self.server.state.security_mode,
                    )
                )
                return
            if component == "events" and len(parts) == 4:
                self._maybe_delay(run_id, "events")
                self._json(
                    [
                        {
                            "sequence": 1,
                            "type": "STEP_STARTED",
                            "payload": {"step_id": f"step-{run_id}"},
                        }
                    ]
                )
                return
            if component == "events" and parts[4:] == ["stream"]:
                # Keep the synthetic stream quiet.  Emitting a stream error
                # schedules an unrelated refresh and makes the deliberate
                # persisted-events response ordering nondeterministic.
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(b": probe stream open\n\n")
                self.wfile.flush()
                self.server.state.stop_streams.wait(10)
                return
        self._json({"code": "NOT_FOUND"}, 404)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            body = None
        self.server.state.record("POST", self.path, body)
        self._json({"accepted": True}, 202)


class RunningServer:
    def __init__(self, state: ProbeState) -> None:
        self.server = ProbeServer(state)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> str:
        self.thread.start()
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def __exit__(self, *_args: object) -> None:
        self.server.state.stop_streams.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def _wait_for_control(state: ProbeState) -> dict[str, object]:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        controls = state.controls()
        if controls:
            return controls[-1]
        time.sleep(0.01)
    raise TimeoutError("browser did not issue a control request")


def _select(page: Page, run_id: str) -> None:
    page.locator("button.run", has_text=run_id).click()


def _control(page: Page, name: str) -> None:
    # Trace archives are deliverable evidence.  Put the redaction marker in
    # the form from the outset so no usable control-token value can enter one.
    page.locator("#session-token").fill("[REDACTED]")
    if name == "cancel":
        page.locator("#cancel-run").click()
    elif name == "approval":
        page.locator("#approvals button", has_text="Approve").click()
    elif name == "reconcile":
        page.locator("#reconcile-action-id").fill("action-A")
        page.locator("#reconcile-form button[type=submit]").click()
    else:  # pragma: no cover - CLI construction prevents this
        raise AssertionError(name)


def identity_trial(
    browser: Browser,
    *,
    component: str,
    control: str,
    trace_path: Path | None,
) -> dict[str, object]:
    state = ProbeState(delayed_component=component)
    with RunningServer(state) as base_url:
        context = browser.new_context()
        if trace_path is not None:
            context.tracing.start(screenshots=True, snapshots=True, sources=False)
        page = context.new_page()
        page.goto(base_url + "/console/")
        page.locator("button.run").first.wait_for()

        _select(page, "run-A")
        if component == "view":
            if not state.delay_started.wait(5):
                raise TimeoutError(f"run-A {component} request was not delayed")
            _select(page, "run-B")
            page.locator("#detail", has_text='"run_id": "run-B"').wait_for()
            state.release_delay.set()
            page.wait_for_timeout(250)
            visible_identity = page.locator("#run-title").inner_text().split(" · ")[0]
            panel_mismatch = '"run_id": "run-A"' in page.locator(
                "#detail"
            ).inner_text()
        else:
            page.locator("#detail", has_text='"run_id": "run-A"').wait_for()
            if not state.delay_started.wait(5):
                raise TimeoutError("run-A events request was not delayed")
            _select(page, "run-B")
            page.locator("#detail", has_text='"run_id": "run-B"').wait_for()
            page.locator("#events", has_text="step-run-B").wait_for()
            state.release_delay.set()
            # Record the scheduling outcome instead of requiring the stale A
            # response to win.  A legitimate B-wins race is a passing sample,
            # not a test-infrastructure timeout.
            page.wait_for_timeout(250)
            visible_identity = page.locator("#run-title").inner_text().split(" · ")[0]
            panel_mismatch = (
                "step-run-A" in page.locator("#events").inner_text()
                or '"run_id": "run-A"' in page.locator("#detail").inner_text()
            )

        _control(page, control)
        observed = _wait_for_control(state)
        selected_target = "run-B" if "/runs/run-B/" in str(observed["path"]) else "run-A"
        wrong_target = selected_target != visible_identity
        result = {
            "contract_result": "FAIL" if panel_mismatch or wrong_target else "PASS",
            "delayed_component": component,
            "control": control,
            "visible_run": visible_identity,
            "control_target": selected_target,
            "control_path": observed["path"],
            "control_body": observed["body"],
            "panel_identity_mixed": panel_mismatch,
            "wrong_control_target": wrong_target,
        }
        if trace_path is not None:
            trace_path.parent.mkdir(parents=True, exist_ok=True)
            context.tracing.stop(path=trace_path)
        context.close()
        return result


def business_trial(browser: Browser, variant: str) -> dict[str, object]:
    state = ProbeState(business_variant=variant)
    with RunningServer(state) as base_url:
        context = browser.new_context()
        page = context.new_page()
        page.goto(base_url + "/console/")
        _select(page, "run-A")
        execution_status = (
            "BLOCKED" if variant == "EXECUTION_ERROR" else "SUCCEEDED"
        )
        page.locator("#detail", has_text='"run_id": "run-A"').wait_for()
        page.locator("#run-title", has_text=execution_status).wait_for()
        expected = variant if variant != "EXECUTION_ERROR" else "Not reported"
        actual = page.locator("#business-status").inner_text()
        reason = page.locator("#run-reason").inner_text()
        result = {
            "contract_result": "PASS" if actual == expected else "FAIL",
            "variant": variant,
            "execution_status": execution_status,
            "expected_business_status": expected,
            "displayed_business_status": actual,
            "displayed_reason": reason,
        }
        context.close()
        return result


def security_trial(browser: Browser) -> dict[str, object]:
    state = ProbeState(security_mode=True)
    with RunningServer(state) as base_url:
        context = browser.new_context()
        page = context.new_page()
        page.goto(base_url + "/console/")
        _select(page, "run-A")
        page.locator("#detail", has_text="__v6_probe_xss").wait_for()
        payload_executed = bool(page.evaluate("Boolean(window.__v6_probe_xss)"))
        injected_elements = page.locator(
            "#detail script, #detail img, #run-reason script, #run-reason img"
        ).count()
        storage = page.evaluate(
            "({local: Object.keys(localStorage), session: Object.keys(sessionStorage)})"
        )
        url_contains_token = "REDACTED" in page.url

        # A missing token is rejected in the browser before a control request
        # is emitted; the real server Host/Origin/session checks are exercised
        # by the mapped Web contract tests.
        page.locator("#cancel-run").click()
        page.locator("#notice", has_text="Enter the local control token").wait_for()
        missing_token_posts = len(state.controls())

        page.locator("#session-token").fill("[REDACTED]")
        # Dispatch both activations in one browser task.  Two sequential
        # Playwright ``click`` calls auto-wait for the disabled button to be
        # re-enabled and therefore model two intentional operations rather
        # than a rapid duplicate click.
        page.locator("#cancel-run").evaluate(
            "button => { button.click(); button.click(); }"
        )
        deadline = time.monotonic() + 5
        while not state.controls() and time.monotonic() < deadline:
            time.sleep(0.01)
        page.wait_for_timeout(250)
        duplicate_posts = len(state.controls())
        requirements = {
            "model_text_does_not_execute": not payload_executed,
            "model_text_creates_no_active_elements": injected_elements == 0,
            "missing_token_sends_no_control": missing_token_posts == 0,
            "duplicate_click_sends_one_control": duplicate_posts == 1,
            "token_not_persisted": storage == {"local": [], "session": []},
            "token_not_in_url": not url_contains_token,
        }
        result = {
            "contract_result": "PASS" if all(requirements.values()) else "FAIL",
            "variant": "xss_token_storage_and_duplicate_click",
            "requirements": requirements,
            "duplicate_click_transport_requests": duplicate_posts,
            "storage": storage,
            "url": page.url,
        }
        context.close()
        return result


def run(
    repetitions: int, trace_dir: Path | None, selected_cases: str = "all"
) -> dict[str, object]:
    if repetitions < 1:
        raise ValueError("repetitions must be positive")
    if not CHROMIUM.is_file():
        return {
            "schema_version": "v6-browser-defect-evidence-v1",
            "result": "NOT_ASSESSED",
            "reason": f"Chromium is unavailable at {CHROMIUM}",
        }
    identity: list[dict[str, object]] = []
    business: list[dict[str, object]] = []
    security: list[dict[str, object]] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(CHROMIUM),
            headless=True,
            args=["--no-sandbox"],
        )
        try:
            if selected_cases in {"all", "identity"}:
                for component in ("view", "events"):
                    for control in ("cancel", "approval", "reconcile"):
                        for repetition in range(repetitions):
                            trace = None
                            if trace_dir is not None and repetition == 0:
                                trace = trace_dir / f"U01-{component}-{control}.zip"
                            item = identity_trial(
                                browser,
                                component=component,
                                control=control,
                                trace_path=trace,
                            )
                            item["repetition"] = repetition + 1
                            identity.append(item)
            if selected_cases in {"all", "business"}:
                for variant in ("APPROVED", "BLOCKED", "EXECUTION_ERROR"):
                    business.append(business_trial(browser, variant))
            if selected_cases in {"all", "security"}:
                security.append(security_trial(browser))
        finally:
            browser.close()
    return {
        "schema_version": "v6-browser-defect-evidence-v1",
        "result": "FAIL" if any(
            item["contract_result"] == "FAIL"
            for item in [*identity, *business, *security]
        ) else "PASS",
        "environment": {
            "playwright": "1.63.0",
            "chromium_executable": str(CHROMIUM),
        },
        "cases": {"U01": identity, "U02": business, "U04": security},
        "control_token_recorded": False,
        "scope": "real Chromium, unchanged console assets, loopback mock API",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=int, default=20)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--trace-dir", type=Path)
    parser.add_argument(
        "--cases",
        choices=("all", "identity", "business", "security"),
        default="all",
    )
    args = parser.parse_args()
    report = run(args.repetitions, args.trace_dir, args.cases)
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    # A discovered product defect is evidence, not test-infrastructure failure.
    return 0 if report["result"] in {"PASS", "FAIL", "NOT_ASSESSED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
