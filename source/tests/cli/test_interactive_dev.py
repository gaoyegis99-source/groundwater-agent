from __future__ import annotations

import json
from pathlib import Path
import subprocess
import threading
from urllib.request import urlopen

import pytest

from scripts.development.interactive.events import EventStore
from scripts.development.interactive.controller import InteractiveController
from scripts.development.interactive.command import InteractiveCommand, InteractiveHooks
from scripts.development.interactive.generation import source_digest
from scripts.development.interactive.profile import (
    ChangePlan,
    build_compose_override,
    classify_changes,
)
from scripts.development.interactive.runtime import CallbackRuntime
from scripts.development.interactive.server import ObservabilityServer
from scripts.development.interactive.supervisor import IncrementalSupervisor
from scripts.development.interactive.watcher import SourceSnapshot, diff_snapshots


def test_change_classification_separates_hmr_reload_restart_and_reconcile() -> None:
    web = classify_changes(("web/src/App.tsx",))
    assert web.hmr == ("web",)
    assert web.reload_services == ()
    assert web.restart_services == ()
    assert web.reconcile is False
    assert web.check_scopes == ("web",)

    data = classify_changes(
        (
            "services/data/src/platform_data_service/api/app.py",
            "services/data/src/platform_data_service/operations/worker.py",
        )
    )
    assert data.hmr == ()
    assert data.reload_services == ("data-service",)
    assert data.restart_services == ("data-worker",)
    assert data.reconcile is False
    assert data.check_scopes == ("data",)

    assembly = classify_changes(("products/alpha/product-manifest.json",))
    assert assembly.reconcile is True
    assert assembly.check_scopes == ("assembler", "contracts")

    controller = classify_changes(("scripts/development/interactive/controller.py",))
    assert controller.controller_restart_required is True


def test_compose_override_is_dev_only_loopback_and_source_read_only(
    tmp_path: Path,
) -> None:
    available = {
        "kernel-service": {},
        "data-service": {},
        "data-worker": {},
        "knowledge-next-service": {},
        "knowledge-next-worker": {},
        "executor-control": {},
        "executor-native-worker": {},
        "executor-qgis-worker": {},
        "gateway": {},
    }
    override = build_compose_override(tmp_path, available)

    assert set(override) == {"services"}
    services = override["services"]
    assert "gateway" not in services
    assert services["kernel-service"]["command"][-1] == "--reload"
    assert "/workspace/kernel/src" in services["kernel-service"]["command"]
    assert services["data-service"]["command"][-1] == "--reload"
    assert "/workspace/services/data/src" in services["data-service"]["command"]
    assert "--factory" in services["knowledge-next-service"]["command"]
    assert services["knowledge-next-service"]["command"][-1] == "--reload"
    assert (
        "/workspace/services/knowledge-next/src"
        in services["knowledge-next-service"]["command"]
    )
    assert services["data-worker"].get("command") is None
    native = services["executor-native-worker"]["environment"]["PYTHONPATH"].split(":")
    qgis = services["executor-qgis-worker"]["environment"]["PYTHONPATH"].split(":")
    assert qgis == [*native, "/usr/share/qgis/python", "/usr/share/qgis/python/plugins"]

    for service in services.values():
        source_mount = next(
            item
            for item in service.get("volumes", [])
            if item.get("target") == "/workspace"
        )
        assert source_mount == {
            "type": "bind",
            "source": str(tmp_path.resolve()),
            "target": "/workspace",
            "read_only": True,
        }
        assert (
            "/workspace/packages/operator-pack/src"
            in service["environment"]["PYTHONPATH"]
        )

    assert all("ports" not in service for service in services.values())


def test_build_inputs_reconcile_instead_of_pretending_to_hot_reload() -> None:
    for path in (
        "services/data/Dockerfile",
        "services/executor/Dockerfile.native",
        "services/knowledge-next/pyproject.toml",
        "environments/common/requirements.lock",
        "web/package.json",
    ):
        plan = classify_changes((path,))
        assert plan.reconcile is True
        assert plan.check_scopes == ("all",)

    controller_glue = classify_changes(("scripts/development/dev.py",))
    assert controller_glue.controller_restart_required is True


def test_snapshot_detects_add_modify_delete_and_ignores_generated_trees(
    tmp_path: Path,
) -> None:
    source = tmp_path / "kernel/src/example.py"
    source.parent.mkdir(parents=True)
    source.write_text("value = 1\n", encoding="utf-8")
    ignored = tmp_path / "node_modules/package/index.js"
    ignored.parent.mkdir(parents=True)
    ignored.write_text("old", encoding="utf-8")
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM scratch\n", encoding="utf-8")

    before = SourceSnapshot.capture(tmp_path)
    dockerfile.write_text("FROM scratch\nLABEL stage=test\n", encoding="utf-8")
    source.write_text("value = 2\n", encoding="utf-8")
    added = tmp_path / "web/src/new.ts"
    added.parent.mkdir(parents=True)
    added.write_text("export {};\n", encoding="utf-8")
    ignored.write_text("new", encoding="utf-8")
    after = SourceSnapshot.capture(tmp_path)
    changes = diff_snapshots(before, after)

    assert [(item.kind, item.path) for item in changes] == [
        ("modified", "Dockerfile"),
        ("modified", "kernel/src/example.py"),
        ("added", "web/src/new.ts"),
    ]

    source.unlink()
    deleted = diff_snapshots(after, SourceSnapshot.capture(tmp_path))
    assert ("deleted", "kernel/src/example.py") in [
        (item.kind, item.path) for item in deleted
    ]
    assert all("node_modules" not in item.path for item in changes + deleted)


def test_event_store_is_bounded_redacts_absolute_paths_and_renders_metrics(
    tmp_path: Path,
) -> None:
    store = EventStore(root=tmp_path, capacity=2)
    secret = tmp_path / "kernel/src/secret.py"
    store.publish("change.detected", "first", paths=(secret,))
    store.publish("validation.passed", "second")
    store.publish("runtime.restarted", "third")

    events = store.snapshot()
    assert [event["message"] for event in events] == ["second", "third"]
    encoded = json.dumps(events, ensure_ascii=False)
    assert str(tmp_path) not in encoded
    metrics = store.render_metrics()
    assert "platform_dev_events_total" in metrics
    assert 'kind="change.detected"' in metrics
    assert "platform_dev_events_buffered 2" in metrics


class _BlockingAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self.active = 0
        self.max_active = 0
        self.first_started = threading.Event()
        self.release_first = threading.Event()

    def apply(self, plan) -> None:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.calls.append(("apply", plan.paths))
        if len(self.calls) == 1:
            self.first_started.set()
            assert self.release_first.wait(2)
        self.active -= 1

    def validate(self, scopes: tuple[str, ...]) -> None:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.calls.append(("validate", scopes))
        self.active -= 1


def test_incremental_supervisor_serializes_and_coalesces_superseded_batches(
    tmp_path: Path,
) -> None:
    store = EventStore(root=tmp_path)
    adapter = _BlockingAdapter()
    supervisor = IncrementalSupervisor(adapter=adapter, events=store)
    supervisor.start()
    try:
        supervisor.submit(classify_changes(("kernel/src/one.py",)))
        assert adapter.first_started.wait(2)
        supervisor.submit(classify_changes(("web/src/two.tsx",)))
        supervisor.submit(classify_changes(("web/src/three.tsx",)))
        adapter.release_first.set()
        assert supervisor.wait_idle(3)
    finally:
        supervisor.stop()

    assert adapter.max_active == 1
    applied = [payload for kind, payload in adapter.calls if kind == "apply"]
    assert applied == [
        ("kernel/src/one.py",),
        ("web/src/three.tsx", "web/src/two.tsx"),
    ]
    assert supervisor.status()["supersededBatches"] == 1


def test_validation_failure_preserves_observed_runtime_update(tmp_path: Path) -> None:
    class Adapter:
        def apply(self, _plan):
            return {"apiGenerations": {"kernel-service": {"sourceDigest": "sha256:new"}}}

        def validate(self, _scopes):
            raise RuntimeError("DEV_VALIDATION_FAILED:kernel:1")

    events = EventStore(root=tmp_path)
    supervisor = IncrementalSupervisor(adapter=Adapter(), events=events)
    supervisor.start()
    try:
        supervisor.submit(classify_changes(("kernel/src/example.py",)))
        assert supervisor.wait_idle(3)
    finally:
        supervisor.stop()

    batch = supervisor.status()["latestBatch"]
    assert batch["phase"] == "FAILED"
    assert batch["failedStage"] == "validation"
    assert batch["runtimeUpdate"]["apiGenerations"]["kernel-service"]["sourceDigest"] == "sha256:new"
    assert [event["kind"] for event in events.snapshot()] == [
        "increment.started", "runtime.api-observed", "increment.applied", "increment.failed",
    ]


def test_observability_server_exposes_status_events_metrics_and_service_proxy(
    tmp_path: Path,
) -> None:
    events = EventStore(root=tmp_path)
    events.publish("controller.ready", "ready")
    server = ObservabilityServer(
        host="127.0.0.1",
        port=0,
        events=events,
        status=lambda: {"status": "PASS", "mode": "interactive"},
        service_metrics=lambda service: (
            "# TYPE example gauge\nexample 1\n" if service == "kernel" else None
        ),
    )
    server.start()
    try:
        base = f"http://127.0.0.1:{server.port}"
        with urlopen(base + "/status", timeout=2) as response:
            assert json.load(response) == {"mode": "interactive", "status": "PASS"}
        with urlopen(base + "/events", timeout=2) as response:
            assert json.load(response)[0]["kind"] == "controller.ready"
        with urlopen(base + "/metrics", timeout=2) as response:
            metrics = response.read().decode()
            assert "platform_dev_events_total" in metrics
            assert "platform_dev_supervisor_active" in metrics
        with urlopen(base + "/metrics/services/kernel", timeout=2) as response:
            assert "example 1" in response.read().decode()
        with urlopen(base + "/", timeout=2) as response:
            assert "增量开发控制台" in response.read().decode()
    finally:
        server.stop()


def test_observability_server_refuses_non_loopback_bind(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="DEV_SERVER_LOOPBACK_REQUIRED"):
        ObservabilityServer(
            host="0.0.0.0",
            port=28185,
            events=EventStore(root=tmp_path),
            status=dict,
            service_metrics=lambda _service: None,
        )


def test_callback_runtime_keeps_reload_in_process_and_restarts_only_workers(
    tmp_path: Path,
) -> None:
    calls: list[object] = []
    runtime = CallbackRuntime(
        root=tmp_path,
        state_directory=tmp_path / "state",
        product_id="alpha",
        prepare_runtime=lambda: calls.append("prepare"),
        reconcile_runtime=lambda: calls.append("reconcile"),
        restart_services=lambda services: calls.append(("restart", services)),
        runtime_status=lambda: {"status": "PASS"},
        validate_target=lambda target: calls.append(("validate", target)),
        ui_enabled=False,
        validation_enabled=True,
    )

    runtime.prepare()
    runtime.apply(
        ChangePlan(
            paths=("services/data/src/example.py",),
            reload_services=("data-service",),
            restart_services=("data-worker",),
        )
    )
    runtime.validate(("data", "data"))
    runtime.apply(
        ChangePlan(
            paths=("products/alpha/product-manifest.json",),
            reconcile=True,
            restart_services=("data-worker",),
        )
    )
    runtime.close()

    assert calls == [
        "prepare",
        ("restart", ("data-worker",)),
        ("validate", "data"),
        "reconcile",
        ("restart", ("data-worker",)),
    ]
    assert runtime.status()["reloadMode"] == "uvicorn-stat-reload"


def test_callback_runtime_proxies_only_declared_service_metrics(
    tmp_path: Path,
) -> None:
    requested: list[str] = []

    runtime = CallbackRuntime(
        root=tmp_path,
        state_directory=tmp_path / "state",
        product_id="alpha",
        prepare_runtime=lambda: None,
        reconcile_runtime=lambda: None,
        restart_services=lambda _services: None,
        runtime_status=lambda: {"status": "PASS"},
        validate_target=lambda _target: None,
        ui_enabled=False,
        validation_enabled=False,
        service_metrics_target=lambda alias: requested.append(alias) or "# EOF\n",
    )

    assert runtime.service_metrics("kernel") == "# EOF\n"
    assert runtime.service_metrics("unknown") is None
    assert requested == ["kernel"]


def test_controller_once_records_xdg_state_and_closes_owned_runtime(
    tmp_path: Path,
) -> None:
    class _Runtime:
        def __init__(self) -> None:
            self.prepared = False
            self.closed = False

        def prepare(self) -> None:
            self.prepared = True

        def apply(self, _plan) -> None:
            raise AssertionError("one-shot mode must not apply a change")

        def validate(self, _scopes) -> None:
            raise AssertionError("one-shot mode must not validate a change")

        def status(self) -> dict[str, object]:
            return {"status": "PASS", "prepared": self.prepared}

        def service_metrics(self, _alias: str) -> None:
            return None

        def close(self) -> None:
            self.closed = True

    state = tmp_path / "xdg-state" / "interactive"
    runtime = _Runtime()
    controller = InteractiveController(
        root=tmp_path / "source",
        state_directory=state,
        runtime=runtime,
        dashboard_port=0,
        poll_interval=0.01,
        debounce_seconds=0.01,
    )

    result = controller.run(once=True)

    assert runtime.prepared is True
    assert runtime.closed is True
    assert result["phase"] == "STOPPED"
    assert result["dashboard"] != "http://127.0.0.1:0/"
    assert result["runtime"]["status"] == "PASS"
    assert result["separation"] == {
        "controlPlane": "development-only",
        "runtimeImports": "none",
        "writableState": "xdg-only",
    }
    status = json.loads((state / "status.json").read_text(encoding="utf-8"))
    assert status == result
    assert not (tmp_path / "source" / "status.json").exists()
    events = [
        json.loads(line)
        for line in (state / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [event["kind"] for event in events] == [
        "controller.starting",
        "controller.ready",
        "controller.stopped",
    ]


def test_interactive_command_generates_override_only_in_xdg_state(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    output = tmp_path / "build" / "assembly"
    output.mkdir(parents=True)
    (output / "compose.json").write_text(
        json.dumps(
            {
                "services": {
                    "kernel-service": {},
                    "data-worker": {},
                    "knowledge-bootstrap": {},
                }
            }
        ),
        encoding="utf-8",
    )
    compose_calls: list[tuple[list[str], tuple[Path, ...], bool]] = []
    bootstraps: list[int] = []

    def run_compose(
        arguments: list[str], extra_files: tuple[Path, ...], check: bool
    ) -> subprocess.CompletedProcess[str]:
        compose_calls.append((arguments, extra_files, check))
        output_text = "metric 1\n" if arguments[0] == "exec" else ""
        return subprocess.CompletedProcess(arguments, 0, output_text, "")

    command = InteractiveCommand(
        root=root,
        state_directory=tmp_path / "xdg-state" / "interactive",
        product_id="alpha",
        hooks=InteractiveHooks(
            ensure_dependencies=lambda: None,
            compile_product=lambda: output,
            run_compose=run_compose,
            runtime_status=lambda: {"status": "PASS"},
            record_bootstrap=lambda completed: bootstraps.append(completed.returncode),
        ),
    )
    runtime = command.runtime(ui_enabled=False, validation_enabled=False)

    runtime.prepare()

    override = command.override_path
    assert override.is_file()
    assert override.is_relative_to(tmp_path / "xdg-state")
    assert not (root / "compose.interactive.json").exists()
    payload = json.loads(override.read_text(encoding="utf-8"))
    assert set(payload["services"]) == {"kernel-service", "data-worker"}
    assert compose_calls == [
        (["config", "--quiet"], (override,), True),
        (
            ["up", "--build", "--pull", "missing", "-d", "--wait"],
            (override,),
            True,
        ),
        (
            ["run", "--rm", "--no-deps", "knowledge-bootstrap"],
            (override,),
            False,
        ),
    ]
    assert bootstraps == [0]
    assert runtime.service_metrics("kernel") == "metric 1\n"
    metrics_call = compose_calls[-1]
    assert metrics_call[0][:4] == ["exec", "-T", "kernel-service", "python"]
    assert metrics_call[1:] == ((override,), False)


def test_api_probe_waits_until_server_reports_current_source(tmp_path: Path) -> None:
    root = tmp_path / "source"
    module = root / "kernel/src/probe.py"
    module.parent.mkdir(parents=True)
    module.write_text("value = 1\n", encoding="utf-8")
    before = source_digest(root, "kernel-service")
    module.write_text("value = 2\n", encoding="utf-8")
    after = source_digest(root, "kernel-service")
    assert before != after
    replies = iter((
        {"sourceDigest": before, "generation": "0" * 32},
        {"sourceDigest": after, "generation": "1" * 32},
    ))

    def run_compose(arguments, _extra, _check):
        assert arguments[:4] == ["exec", "-T", "kernel-service", "python"]
        return subprocess.CompletedProcess(arguments, 0, json.dumps(next(replies)), "")

    command = InteractiveCommand(
        root=root,
        state_directory=tmp_path / "state",
        product_id="alpha",
        hooks=InteractiveHooks(
            ensure_dependencies=lambda: None,
            compile_product=lambda: root,
            run_compose=run_compose,
            runtime_status=dict,
            record_bootstrap=lambda _completed: None,
        ),
    )
    command.override_path.parent.mkdir()
    command.override_path.write_text('{"services":{"kernel-service":{}}}')
    assert command._observe_apis(("kernel-service",)) == {
        "kernel-service": {"sourceDigest": after, "generation": "1" * 32},
    }


@pytest.mark.parametrize("failure_stage", ["apply", "validation"])
def test_later_batch_reconciles_failed_work_without_reapplying_valid_runtime(
    failure_stage: str,
) -> None:
    class Adapter:
        def __init__(self):
            self.applied = []
            self.validated = []

        def apply(self, plan):
            self.applied.append(plan.paths)
            if failure_stage == "apply" and len(self.applied) == 1:
                raise RuntimeError("DEV_RUNTIME_UPDATE_FAILED")

        def validate(self, scopes):
            self.validated.append(scopes)
            if failure_stage == "validation" and len(self.validated) == 1:
                raise RuntimeError("DEV_VALIDATION_FAILED:kernel:1")

    adapter = Adapter()
    supervisor = IncrementalSupervisor(adapter=adapter)
    supervisor.start()
    try:
        kernel_path = "kernel/src/example.py"
        web_path = "web/src/example.tsx"
        supervisor.submit(classify_changes((kernel_path,)))
        assert supervisor.wait_idle(3)
        assert supervisor.status()["latestBatch"]["phase"] == "FAILED"
        supervisor.submit(classify_changes((web_path,)))
        assert supervisor.wait_idle(3)
        assert supervisor.status()["latestBatch"]["phase"] == "COMPLETED"
    finally:
        supervisor.stop()

    assert adapter.applied == [
        (kernel_path,), (kernel_path, web_path) if failure_stage == "apply" else (web_path,),
    ]
    assert adapter.validated[-1] == ("kernel", "web")


def test_controller_observes_source_changes_during_runtime_preparation(tmp_path: Path) -> None:
    root = tmp_path / "source"
    edited = root / "web" / "src" / "App.tsx"
    edited.parent.mkdir(parents=True)
    edited.write_text("before", encoding="utf-8")

    class Runtime:
        checked = ()

        def prepare(self):
            edited.write_text("changed while starting", encoding="utf-8")

        def apply(self, plan):
            assert plan.paths == ("web/src/App.tsx",)

        def validate(self, scopes):
            self.checked = scopes
            controller.request_stop()
            return scopes

        def status(self):
            return {"status": "PASS"}

        def service_metrics(self, _alias):
            return None

        def close(self):
            pass

    runtime = Runtime()
    controller = InteractiveController(
        root=root, state_directory=tmp_path / "state", runtime=runtime,
        dashboard_port=0, poll_interval=0.01, debounce_seconds=0.01,
    )
    watchdog = threading.Timer(1.5, controller.request_stop)
    watchdog.start()
    try:
        result = controller.run()
    finally:
        watchdog.cancel()
    assert runtime.checked == ("web",)
    assert result["supervisor"]["completedBatches"] == 1


def test_supervisor_finishes_an_accepted_batch_during_shutdown() -> None:
    adapter = _BlockingAdapter()
    supervisor = IncrementalSupervisor(adapter=adapter)
    supervisor.start()
    shutdown = threading.Thread(target=supervisor.stop)
    try:
        supervisor.submit(classify_changes(("kernel/src/one.py",)))
        assert adapter.first_started.wait(2)
        supervisor.submit(classify_changes(("web/src/two.tsx",)))
        shutdown.start()
        with supervisor._condition:
            assert supervisor._condition.wait_for(lambda: supervisor._stopping, timeout=2)
        with pytest.raises(RuntimeError, match="DEV_SUPERVISOR_STOPPED"):
            supervisor.submit(classify_changes(("web/src/rejected.tsx",)))
    finally:
        adapter.release_first.set()
        if shutdown.ident is not None:
            shutdown.join(3)
        else:
            supervisor.stop()
    assert not shutdown.is_alive()
    assert supervisor.status()["completedBatches"] == 2
    assert ("validate", ("web",)) in adapter.calls
