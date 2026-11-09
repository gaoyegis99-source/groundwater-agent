"""Cross-check console defects against real V6 Web routes and CSV tools."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
import socket
import sys
from threading import Event, Lock, Thread
import time
from typing import Any

import anyio
from playwright.sync_api import Browser, Page, sync_playwright
from platform_lightweight_agent import (
    ApprovalDecision,
    FileWorkspace,
    FunctionToolExecutor,
    LocalWebSecurity,
    NodeContract,
    PortableTarget,
    SQLiteRunJournal,
    ToolContext,
    ToolDelivery,
    ToolExecutionFailedError,
    ToolRecoveryCapabilities,
    ToolReference,
    ValueBinding,
    WorkflowEngine,
    approval_step,
    compile_domain_pack,
    create_web_app,
    load_project_runtime,
    tool_step,
    workflow,
)
import uvicorn


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
PACK_ROOT = PACKAGE_ROOT / "examples" / "domain-packs" / "csv-statistics"
CHROMIUM = Path.home() / ".cache/ms-playwright/chromium-1243/chrome-linux64/chrome"
SESSION = "[REDACTED]"
EMPTY = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}
VALUE = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {"value": {"type": "integer"}},
    "required": ["value"],
    "additionalProperties": False,
}
FAIL_TOOL = ToolReference("probe.browser-failure", "1", "sha256:" + "3" * 64)


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


class RunningApp:
    def __init__(self, app: Any) -> None:
        self.port = _free_port()
        self.server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=self.port,
                log_level="error",
                access_log=False,
            )
        )
        self.thread = Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> str:
        self.thread.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if not self.thread.is_alive():
                raise RuntimeError("real Web server exited before accepting requests")
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.1):
                    return f"http://127.0.0.1:{self.port}"
            except OSError:
                time.sleep(0.01)
        raise TimeoutError("real Web server did not accept connections")

    def __exit__(self, *_args: object) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)
        if self.thread.is_alive():
            raise TimeoutError("real Web server did not stop")


@dataclass
class IdentityObservation:
    delayed: Event = field(default_factory=Event)
    release: Event = field(default_factory=Event)
    controls: list[str] = field(default_factory=list)
    lock: Lock = field(default_factory=Lock)
    delayed_once: bool = False

    def control(self, path: str) -> None:
        with self.lock:
            self.controls.append(path)


def _identity_engine(root: Path) -> WorkflowEngine:
    journal = SQLiteRunJournal(root / "identity.sqlite")
    engine = WorkflowEngine(journal, FunctionToolExecutor(), {})
    spec = workflow(
        "real-browser-identity",
        "1",
        (approval_step("review", "Continue?"),),
        schema_version="v6-light-workflow-v1",
    )
    for run_id in ("real-run-A", "real-run-B"):
        snapshot = engine.run(spec, {}, run_id=run_id)
        if snapshot.status != "WAITING_APPROVAL":
            raise AssertionError("identity fixture did not wait for approval")
    return engine


def _identity_app(engine: WorkflowEngine, observation: IdentityObservation) -> Any:
    app = create_web_app(
        engine,
        security=LocalWebSecurity(session_token=SESSION),
        sse_ping_seconds=30,
    )

    @app.middleware("http")
    async def delay_old_detail(request: Any, call_next: Any) -> Any:
        response = await call_next(request)
        path = request.url.path
        if request.method == "POST":
            observation.control(path)
        if path == "/v2/runs/real-run-A/view" and not observation.delayed_once:
            observation.delayed_once = True
            observation.delayed.set()
            await anyio.to_thread.run_sync(observation.release.wait)
        return response

    return app


def _select(page: Page, run_id: str) -> None:
    page.locator("button.run", has_text=run_id).first.click()


def _wait_control(observation: IdentityObservation) -> str:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with observation.lock:
            if observation.controls:
                return observation.controls[-1]
        time.sleep(0.01)
    raise TimeoutError("real Web backend did not receive the browser control")


def identity_trial(browser: Browser, root: Path) -> dict[str, object]:
    root.mkdir(parents=True, exist_ok=False)
    observation = IdentityObservation()
    engine = _identity_engine(root)
    app = _identity_app(engine, observation)
    try:
        with RunningApp(app) as base_url:
            context = browser.new_context()
            page = context.new_page()
            page.goto(base_url + "/console/")
            page.locator("button.run", has_text="real-run-A").wait_for()
            _select(page, "real-run-A")
            if not observation.delayed.wait(5):
                raise TimeoutError("real run-A detail response was not delayed")
            _select(page, "real-run-B")
            page.locator("#run-title", has_text="real-run-B").wait_for()
            observation.release.set()
            page.wait_for_timeout(200)
            visible_run = page.locator("#run-title").inner_text().split(" · ")[0]
            page.locator("#session-token").fill(SESSION)
            page.locator("#cancel-run").click()
            control_path = _wait_control(observation)
            control_run = (
                "real-run-B" if "/real-run-B/" in control_path else "real-run-A"
            )
            detail_text = page.locator("#detail").inner_text()
            context.close()
    finally:
        observation.release.set()
    requirements = {
        "visible_panel_matches_selected_run": visible_run == "real-run-B",
        "control_targets_visible_run": control_run == visible_run,
    }
    return {
        "case": "U01",
        "variant": "real_backend_delayed_detail_then_cancel",
        "contract_result": "PASS" if all(requirements.values()) else "FAIL",
        "requirements": requirements,
        "visible_run": visible_run,
        "control_run": control_run,
        "control_path": control_path,
        "detail_mentions_real_run_a": "real-run-A" in detail_text,
        "backend": "actual create_web_app over SQLiteRunJournal",
    }


def _csv_target() -> PortableTarget:
    manifests = json.loads((PACK_ROOT / "tools.json").read_text(encoding="utf-8"))
    tools = tuple(
        ToolReference.from_mapping(
            {key: item[key] for key in ("tool_id", "version", "package_digest")}
        )
        for item in manifests
    )
    raw_deliveries = json.loads(
        (PACK_ROOT / "release-deliveries.json").read_text(encoding="utf-8")
    )
    deliveries = []
    for name in ("validate", "calculate", "blocked"):
        raw = dict(raw_deliveries[name])
        raw["wheel_path"] = str(PACK_ROOT / raw["wheel_path"])
        deliveries.append(ToolDelivery.from_mapping(raw))
    return PortableTarget(
        trusted_tools=tools,
        allowed_permissions=frozenset({"files.read", "files.write"}),
        python_executable=sys.executable,
        tool_deliveries=tuple(deliveries),
    )


async def _prepare_csv_runs(root: Path) -> tuple[WorkflowEngine, dict[str, Any]]:
    compiled = compile_domain_pack(
        PACK_ROOT, root / "compiled", parameter_profile="small"
    )
    project = compiled.projects["analysis"]
    workspace = FileWorkspace.under(root / "workspace")
    valid_source = root / "valid.csv"
    valid_source.write_text("level\n1\n2\n3\n", encoding="utf-8")
    invalid_source = root / "invalid.csv"
    invalid_source.write_text("level\n1\nnot-a-number\n", encoding="utf-8")
    valid = workspace.stage_input(valid_source, "inputs/valid.csv")
    invalid = workspace.stage_input(invalid_source, "inputs/invalid.csv")
    journal = SQLiteRunJournal(root / "business.sqlite")

    def context_factory(request: Any, cancellation: Any) -> ToolContext:
        return ToolContext(request.action_id, cancellation, workspace)

    runtime = load_project_runtime(
        project,
        _csv_target(),
        journal,
        tool_context_factory=context_factory,
    )
    valid_waiting = await runtime.engine.run_async(
        runtime.workflow,
        {"file": valid.to_mapping(), "column": "level"},
        run_id="csv-real-valid",
    )
    approval_id = next(iter(valid_waiting.state["v3"]["pending_approvals"]))
    valid_complete = await runtime.engine.resume_async(
        valid_waiting.run_id,
        approvals=(ApprovalDecision(approval_id, True),),
    )
    invalid_complete = await runtime.engine.run_async(
        runtime.workflow,
        {"file": invalid.to_mapping(), "column": "level"},
        run_id="csv-real-invalid",
    )

    failing_executor = FunctionToolExecutor()

    def fail(_: Any) -> None:
        raise ToolExecutionFailedError("CONTROLLED_EXECUTION_FAILURE")

    failing_executor.register(FAIL_TOOL, fail)
    failure_spec = workflow(
        "real-browser-execution-error",
        "1",
        (
            tool_step(
                "fail",
                FAIL_TOOL,
                approval_required=False,
                capabilities=ToolRecoveryCapabilities(external_side_effects=False),
                contract=NodeContract(EMPTY, VALUE, {}),
            ),
        ),
        schema_version="v6-light-workflow-v4",
        input_schema=EMPTY,
        output_schema=VALUE,
        output_bindings={"/value": ValueBinding("step_output", "/value", "fail")},
    )
    failure = await WorkflowEngine(journal, failing_executor, {}).run_async(
        failure_spec, {}, run_id="csv-real-execution-error"
    )
    snapshots = {
        item.run_id: item for item in (valid_complete, invalid_complete, failure)
    }
    return runtime.engine, snapshots


def _business_row(page: Page, run_id: str, snapshot: Any) -> dict[str, object]:
    _select(page, run_id)
    page.locator("#run-title", has_text=f"{run_id} · {snapshot.status}").wait_for()
    # selectRun updates the title before its detail request settles.  Bind the
    # observation to the exact persisted response rather than that optimistic
    # title update.
    page.locator("#detail", has_text=run_id).wait_for()
    output = snapshot.state["v3"].get("output")
    expected_business = (
        output.get("business_status") if isinstance(output, dict) else "Not reported"
    )
    actual_business = page.locator("#business-status").inner_text()
    result_files = page.locator("#result-files").inner_text()
    expected_file = None
    if isinstance(output, dict) and isinstance(output.get("result_file"), dict):
        expected_file = output["result_file"].get("logical_id")
    requirements = {
        "execution_status_matches_snapshot": snapshot.status
        in page.locator("#run-title").inner_text(),
        "business_status_is_separate": actual_business == expected_business,
        "published_result_file_is_visible": expected_file is None
        or expected_file in result_files,
    }
    return {
        "case": "U02",
        "variant": run_id,
        "contract_result": "PASS" if all(requirements.values()) else "FAIL",
        "requirements": requirements,
        "execution_status": snapshot.status,
        "expected_business_status": expected_business,
        "displayed_business_status": actual_business,
        "failure": snapshot.state.get("failure"),
        "workflow_output": output,
        "displayed_result_files": result_files,
    }


def business_trials(
    browser: Browser,
    engine: WorkflowEngine,
    snapshots: dict[str, Any],
) -> list[dict[str, object]]:
    app = create_web_app(
        engine,
        security=LocalWebSecurity(session_token=SESSION),
        sse_ping_seconds=30,
    )
    with RunningApp(app) as base_url:
        context = browser.new_context()
        page = context.new_page()
        page.goto(base_url + "/console/")
        page.locator("button.run", has_text="csv-real-valid").wait_for()
        results = [
            _business_row(page, run_id, snapshots[run_id])
            for run_id in (
                "csv-real-valid",
                "csv-real-invalid",
                "csv-real-execution-error",
            )
        ]
        context.close()
    return results


def collect(root: Path) -> dict[str, object]:
    if not CHROMIUM.is_file():
        return {
            "schema_version": "v6-real-backend-browser-evidence-v1",
            "result": "NOT_ASSESSED",
            "results": [
                {
                    "case": "U01/U02",
                    "variant": "real_backend_browser",
                    "contract_result": "NOT_ASSESSED",
                    "reason": f"Chromium is unavailable at {CHROMIUM}",
                }
            ],
        }
    root.mkdir(parents=True, exist_ok=False)
    business_root = root / "business"
    business_root.mkdir()
    business_engine, business_snapshots = anyio.run(_prepare_csv_runs, business_root)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=str(CHROMIUM),
            headless=True,
            args=["--no-sandbox"],
        )
        try:
            results = [identity_trial(browser, root / "identity")]
            results.extend(
                business_trials(browser, business_engine, business_snapshots)
            )
        finally:
            browser.close()
    return {
        "schema_version": "v6-real-backend-browser-evidence-v1",
        "result": (
            "FAIL"
            if any(item["contract_result"] == "FAIL" for item in results)
            else "PASS"
        ),
        "environment": {
            "playwright": "1.63.0",
            "chromium_executable": str(CHROMIUM),
            "backend": "uvicorn + create_web_app + SQLiteRunJournal",
            "csv_capability": "v6-csv-statistics-capability==1.0.0",
        },
        "results": results,
        "control_token_recorded": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = collect(args.evidence_root)
        exit_code = 0
    except Exception as error:  # noqa: BLE001 - preserve infrastructure evidence
        report = {
            "schema_version": "v6-real-backend-browser-evidence-v1",
            "result": "ERROR",
            "results": [
                {
                    "case": "U01/U02",
                    "variant": "real_backend_browser",
                    "contract_result": "ERROR",
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
            ],
        }
        exit_code = 1
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
