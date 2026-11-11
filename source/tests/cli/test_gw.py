from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.maintenance import gw


def test_bootstrap_sync_routes_to_locked_toolchain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[Path, object]] = []
    paths = object()

    def sync(source_root: Path, lifecycle_paths: object) -> dict[str, object]:
        calls.append((source_root, lifecycle_paths))
        return {
            "status": "PASS",
            "digest": "fixture-digest",
            "path": str(tmp_path / "toolchain"),
        }

    monkeypatch.setattr(
        gw,
        "bootstrap_toolchain",
        SimpleNamespace(sync=sync, BootstrapRefusal=RuntimeError),
        raising=False,
    )
    monkeypatch.setattr(gw, "SOURCE_ROOT", tmp_path / "source")
    monkeypatch.setattr(gw, "lifecycle", lambda: paths)

    gw.main(["bootstrap", "--sync"])

    assert calls == [(tmp_path / "source", paths)]
    assert json.loads(capsys.readouterr().out) == {
        "status": "PASS",
        "digest": "fixture-digest",
        "path": str(tmp_path / "toolchain"),
    }


def test_bootstrap_sync_reports_refusal_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class BootstrapRefusalError(RuntimeError):
        pass

    def refuse(_source_root: Path, _lifecycle_paths: object) -> dict[str, object]:
        raise BootstrapRefusalError("UV_MISSING")

    monkeypatch.setattr(
        gw,
        "bootstrap_toolchain",
        SimpleNamespace(sync=refuse, BootstrapRefusal=BootstrapRefusalError),
        raising=False,
    )

    with pytest.raises(SystemExit, match="1"):
        gw.main(["bootstrap", "--sync"])

    assert json.loads(capsys.readouterr().out) == {
        "status": "FAILED",
        "code": "UV_MISSING",
    }


def test_bootstrap_status_is_read_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    paths = object()
    calls: list[tuple[Path, object]] = []

    def status(source_root: Path, lifecycle_paths: object) -> dict[str, object]:
        calls.append((source_root, lifecycle_paths))
        return {"status": "MISSING", "digest": "fixture-digest"}

    monkeypatch.setattr(
        gw,
        "bootstrap_toolchain",
        SimpleNamespace(status=status, BootstrapRefusal=RuntimeError),
    )
    monkeypatch.setattr(gw, "lifecycle", lambda: paths)

    gw.main(["bootstrap", "--status"])

    assert calls == [(gw.SOURCE_ROOT, paths)]
    assert json.loads(capsys.readouterr().out) == {
        "status": "MISSING",
        "digest": "fixture-digest",
    }


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (["deps", "status"], ("status", None)),
        (["deps", "verify", "--scope", "python"], ("verify", "python")),
        (
            [
                "deps",
                "materialize",
                "--scope",
                "python",
                "--scope",
                "npm",
            ],
            ("materialize", {"npm", "python"}),
        ),
    ],
)
def test_dependency_commands_route_to_locked_materializer(
    arguments: list[str],
    expected: tuple[str, object],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[str, object]] = []
    paths = object()

    def status(_source_root: Path, lifecycle_paths: object) -> dict[str, object]:
        calls.append(("status", None))
        assert lifecycle_paths is paths
        return {"status": "PASS"}

    def verify_scope(
        _source_root: Path, lifecycle_paths: object, scope: str
    ) -> dict[str, object]:
        calls.append(("verify", scope))
        assert lifecycle_paths is paths
        return {"status": "PASS", "scope": scope}

    def materialize(
        _source_root: Path, lifecycle_paths: object, scopes: set[str]
    ) -> dict[str, object]:
        calls.append(("materialize", scopes))
        assert lifecycle_paths is paths
        return {"status": "PASS", "scopes": sorted(scopes)}

    monkeypatch.setattr(
        gw,
        "dependency_materializer",
        SimpleNamespace(
            SCOPES=frozenset({"npm", "python", "system"}),
            DependencyRefusal=RuntimeError,
            materialize=materialize,
            status=status,
            verify_scope=verify_scope,
        ),
        raising=False,
    )
    monkeypatch.setattr(gw, "lifecycle", lambda: paths)

    gw.main(arguments)

    assert calls == [expected]
    assert json.loads(capsys.readouterr().out)["status"] == "PASS"


def test_dev_reexecutes_with_verified_locked_toolchain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    locked_python = tmp_path / "toolchain/bin/python"
    locked_python.parent.mkdir(parents=True)
    locked_python.write_bytes(b"")
    paths = object()
    executed: list[tuple[str, list[str], dict[str, str]]] = []

    class ExecCalledError(RuntimeError):
        pass

    def execve(
        executable: str, arguments: list[str], environment: dict[str, str]
    ) -> None:
        executed.append((executable, arguments, environment))
        raise ExecCalledError

    monkeypatch.setattr(
        gw.bootstrap_toolchain,
        "status",
        lambda _source_root, lifecycle_paths: {
            "status": "PASS" if lifecycle_paths is paths else "MISSING",
            "python": str(locked_python),
        },
    )
    monkeypatch.setattr(gw, "lifecycle", lambda: paths)
    monkeypatch.setattr(
        gw,
        "child_environment",
        lambda: {"V6_PYTHON": str(locked_python), "MARKER": "locked"},
    )
    monkeypatch.setattr(gw.os, "execve", execve)
    script = tmp_path / "dev.py"

    with pytest.raises(ExecCalledError):
        gw._exec_python(script, ["check", "--scope", "kernel"])

    assert executed == [
        (
            str(locked_python),
            [
                str(locked_python),
                "-B",
                str(script),
                "check",
                "--scope",
                "kernel",
            ],
            {"V6_PYTHON": str(locked_python), "MARKER": "locked"},
        )
    ]


def test_dev_refuses_unverified_toolchain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        gw.bootstrap_toolchain,
        "status",
        lambda _source_root, _lifecycle_paths: {"status": "MISSING"},
    )
    monkeypatch.setattr(
        gw.os,
        "execve",
        lambda *_arguments: pytest.fail("unverified toolchain was executed"),
    )

    with pytest.raises(gw.EnvironmentRefusal, match="PROJECT_TOOLCHAIN_NOT_READY"):
        gw._exec_python(Path("dev.py"), [])
