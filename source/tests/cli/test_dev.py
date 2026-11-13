from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.development.dev as dev
from scripts.development.dev import (
    _RESEARCH_DEPENDENCY_INPUTS,
    _compose_json_rows,
    _compose_project,
    _ensure_research_dependencies,
    _research_dependency_root,
    _select_product,
    _worktree_identity,
    clean_workspace,
)


@pytest.fixture(autouse=True)
def isolated_fixture_layout(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    monkeypatch.delenv("PLATFORM_GENERATED_ROOT", raising=False)


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".gitignore").write_text(
        ".generated/\n__pycache__/\nbuild/\ndist/\nnode_modules/\n"
        "evidence/\nrelease/\n.venv/\n.env\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", ".gitignore"], cwd=root, check=True)
    return root


def test_clean_dry_run_and_clean_preserve_active_assembly_and_protected_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = _repository(tmp_path)
    generated = tmp_path / "external-build"
    monkeypatch.setenv("PLATFORM_GENERATED_ROOT", str(generated))
    monkeypatch.setattr(dev, "_container_bind_sources", lambda: set())
    active = generated / ".releases/groundwater/active"
    stale = generated / ".releases/groundwater/stale"
    active.mkdir(parents=True)
    stale.mkdir()
    (active / "compose.json").write_text("{}")
    (stale / "compose.json").write_text("{}")
    (generated / "groundwater.current.json").write_text(
        json.dumps(
            {
                "schemaVersion": "assembly-current-pointer.v1",
                "productId": "groundwater",
                "releaseDirectory": ".releases/groundwater/active",
                "releaseDigest": "active",
            }
        )
    )
    generated_evidence = generated / "webui-evidence/artifacts"
    generated_evidence.mkdir(parents=True)
    (generated_evidence / "results.json").write_text("{}")
    for relative in ("module/__pycache__", "web/dist", "package/build"):
        directory = root / relative
        directory.mkdir(parents=True)
        (directory / "output").write_text("generated")
    for relative in (
        "node_modules/cache",
        "service/node_modules/dependency/dist",
        "evidence/artifacts",
        "release/bundle",
        ".venv/cache",
    ):
        directory = root / relative
        directory.mkdir(parents=True)
        (directory / "keep").write_text("keep")
    (root / ".env").write_text("SECRET=keep")

    expected = {
        "@build/.releases/groundwater/stale",
        "@build/webui-evidence",
        "module/__pycache__",
        "package/build",
        "web/dist",
    }
    assert set(clean_workspace(root, dry_run=True)) == expected
    assert stale.exists()
    assert generated_evidence.parent.exists()
    assert all(
        (root / relative).exists()
        for relative in ("module/__pycache__", "package/build", "web/dist")
    )

    assert set(clean_workspace(root)) == expected
    assert not stale.exists()
    assert not generated_evidence.parent.exists()
    assert not any(
        (root / relative).exists()
        for relative in ("module/__pycache__", "package/build", "web/dist")
    )
    assert (generated / "groundwater.current.json").is_file()
    assert active.is_dir()
    assert all(
        (root / relative).exists()
        for relative in (
            "node_modules/cache",
            "service/node_modules/dependency/dist",
            "evidence/artifacts",
            "release/bundle",
            ".venv/cache",
            ".env",
        )
    )


@pytest.mark.parametrize("inspect_available", [True, False])
def test_clean_preserves_releases_in_use_by_containers(
    tmp_path: Path, monkeypatch, inspect_available: bool,
) -> None:
    root = _repository(tmp_path)
    generated = tmp_path / "external-build"
    monkeypatch.setenv("PLATFORM_GENERATED_ROOT", str(generated))
    releases = generated / ".releases" / "groundwater"
    mounted = releases / "mounted"
    unmounted = releases / "unmounted"
    for directory in (mounted, unmounted):
        directory.mkdir(parents=True)
        (directory / "kernel-config.json").write_text("{}")
    (generated / "disposable").mkdir()
    monkeypatch.setattr(dev, "docker_arguments", lambda args: ["test-docker", *args])
    original_check_output = subprocess.check_output

    def check_output(command, **kwargs):
        if command[0] != "test-docker":
            return original_check_output(command, **kwargs)
        if command[1] == "ps":
            assert "--all" in command
            return "created-container\n"
        assert command[1] == "inspect"
        assert command[-1] == "created-container"
        if not inspect_available:
            raise subprocess.CalledProcessError(1, command)
        return json.dumps([
            {"Type": "bind", "Source": str(mounted / "kernel-config.json")},
            {"Type": "volume", "Source": str(unmounted)},
        ]) + "\n"

    monkeypatch.setattr(subprocess, "check_output", check_output)
    expected = {"@build/disposable"}
    if inspect_available:
        expected.add("@build/.releases/groundwater/unmounted")
    assert set(clean_workspace(root, dry_run=True)) == expected
    assert set(clean_workspace(root)) == expected
    assert mounted.is_dir()
    assert unmounted.is_dir() is not inspect_available
    assert not (generated / "disposable").exists()


def test_clean_refuses_a_tracked_candidate(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    tracked = root / "build/owned.txt"
    tracked.parent.mkdir()
    tracked.write_text("owned")
    subprocess.run(["git", "add", "-f", "build/owned.txt"], cwd=root, check=True)

    with pytest.raises(ValueError, match="CLEAN_TRACKED_PATH:build/owned.txt"):
        clean_workspace(root)
    assert tracked.is_file()


def test_clean_removes_ignored_legacy_generated_tree(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    legacy = root / ".generated/old/report.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("{}")

    assert ".generated" in clean_workspace(root, dry_run=True)
    clean_workspace(root)
    assert not (root / ".generated").exists()


def test_clean_preserves_unignored_source_directory_named_build(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    ignore = root / ".gitignore"
    ignore.write_text(
        ignore.read_text() + "!environments/build/\n" + "!environments/build/**\n",
        encoding="utf-8",
    )
    lock = root / "environments/build/requirements.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("setuptools==75.8.0\n", encoding="utf-8")

    assert "environments/build" not in clean_workspace(root, dry_run=True)
    clean_workspace(root)
    assert lock.is_file()


def test_product_selection_is_explicit_when_multiple_products_exist(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    for product_id in ("alpha", "beta"):
        manifest = root / "products" / product_id / "product-manifest.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("{}")

    with pytest.raises(ValueError, match="PRODUCT_SELECTION_REQUIRED:alpha,beta"):
        _select_product(root, None)
    assert _select_product(root, "beta") == "beta"
    with pytest.raises(ValueError, match="PRODUCT_NOT_FOUND:missing"):
        _select_product(root, "missing")


def test_compose_project_is_derived_from_repository_and_product() -> None:
    assert _compose_project(Path("/workspace/platform_v6"), "alpha") == (
        "platform-v6-alpha"
    )


def test_research_dependency_root_reuses_cache_when_build_inputs_match(
    tmp_path: Path,
) -> None:
    root = tmp_path / "source"
    cache = tmp_path / "deps"
    configured = cache / "sha256-new"
    reusable = cache / "sha256-old"
    inputs = {}
    for relative in _RESEARCH_DEPENDENCY_INPUTS:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative, encoding="utf-8")
        inputs[relative] = "sha256:" + __import__("hashlib").sha256(
            path.read_bytes()
        ).hexdigest()
    for scope in ("images", "npm", "python", "system"):
        (reusable / scope).mkdir(parents=True, exist_ok=True)
    (reusable / "materialization-manifest.json").write_text(
        json.dumps({"inputs": inputs}), encoding="utf-8"
    )

    assert _research_dependency_root(configured, root) == reusable


def test_research_dependency_root_rejects_stale_cache(tmp_path: Path) -> None:
    root = tmp_path / "source"
    configured = tmp_path / "deps/sha256-new"
    for relative in _RESEARCH_DEPENDENCY_INPUTS:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative, encoding="utf-8")

    with pytest.raises(ValueError, match="RESEARCH_DEPENDENCIES_NOT_READY"):
        _research_dependency_root(configured, root)


def test_ensure_research_dependencies_materializes_only_missing_scopes(
    tmp_path: Path, monkeypatch
) -> None:
    paths = object()
    target = tmp_path / "dependencies"
    requested: list[set[str]] = []
    monkeypatch.setattr(dev, "lifecycle", lambda: paths)
    monkeypatch.setattr(
        dev.dependency_materializer,
        "status",
        lambda root, resolved: {
            "status": "NOT_READY",
            "path": str(target),
            "scopes": {"npm": "PASS", "python": "MISSING", "system": "MISSING"},
        },
    )

    def materialize(root, resolved, scopes):
        assert root == tmp_path
        assert resolved is paths
        requested.append(scopes)
        return {
            "status": "PASS",
            "path": str(target),
            "scopes": {"npm": "PASS", "python": "PASS", "system": "PASS"},
        }

    monkeypatch.setattr(dev.dependency_materializer, "materialize", materialize)

    assert _ensure_research_dependencies(tmp_path) == target
    assert requested == [{"python", "system"}]


def test_compose_json_rows_accepts_array_and_json_lines() -> None:
    expected = [{"Service": "gateway"}, {"Service": "kernel"}]
    assert _compose_json_rows(json.dumps(expected)) == expected
    assert _compose_json_rows("\n".join(json.dumps(row) for row in expected)) == expected


def test_research_qgis_source_mount_keeps_image_python_paths(
    tmp_path: Path, monkeypatch
) -> None:
    state = tmp_path / "state"
    data = tmp_path / "data"
    config = tmp_path / "config"
    assembly = tmp_path / "assembly"
    assembly.mkdir()
    (assembly / "compose.json").write_text(json.dumps({
        "services": {"executor-native-worker": {}, "executor-qgis-worker": {}},
    }))
    (config / "profiles").mkdir(parents=True)
    (config / "profiles/research.env").write_text("TEST_RESEARCH_VALUE=enabled\n")
    (config / "secrets/research").mkdir(parents=True)
    monkeypatch.setattr(dev, "lifecycle", lambda: SimpleNamespace(
        state=state, data=data, config=config,
    ))
    monkeypatch.setattr(dev, "current_bundle", lambda *_args: assembly)
    monkeypatch.setattr(dev, "_research_dependency_root", lambda root: root)
    monkeypatch.setenv("PLATFORM_DEPENDENCY_ROOT", str(tmp_path / "dependencies"))

    _, override, _, _, _ = dev._research_runtime("groundwater")

    services = json.loads(override.read_text())["services"]
    native = services["executor-native-worker"]["environment"]["PYTHONPATH"].split(":")
    qgis = services["executor-qgis-worker"]["environment"]["PYTHONPATH"].split(":")
    assert qgis == [*native, "/usr/share/qgis/python", "/usr/share/qgis/python/plugins"]


def test_worktree_identity_covers_untracked_source_but_excludes_ignored_files(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True
    )
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=root, check=True)

    revision, dirty, clean_digest = _worktree_identity(root)
    assert revision
    assert dirty is False

    (root / ".env").write_text("SECRET=first\n", encoding="utf-8")
    assert _worktree_identity(root) == (revision, False, clean_digest)

    source = root / "feature.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    _revision, dirty, first_digest = _worktree_identity(root)
    assert dirty is True
    assert first_digest != clean_digest

    source.write_text("VALUE = 2\n", encoding="utf-8")
    _revision, dirty, second_digest = _worktree_identity(root)
    assert dirty is True
    assert second_digest != first_digest

    (root / ".env").write_text("SECRET=second\n", encoding="utf-8")
    assert _worktree_identity(root)[2] == second_digest


def test_status_reports_missing_dependencies_without_a_traceback(monkeypatch) -> None:
    monkeypatch.setattr(
        dev,
        "_worktree_identity",
        lambda _root: ("abc123", True, "f" * 64),
    )

    def missing_dependencies(_product_id: str):
        raise ValueError("RESEARCH_DEPENDENCIES_NOT_READY")

    monkeypatch.setattr(dev, "_research_runtime", missing_dependencies)

    status = dev._status("groundwater")

    assert status == {
        "status": "NOT_READY",
        "issues": [
            {
                "code": "RESEARCH_DEPENDENCIES_NOT_READY",
                "summary": "本地依赖缓存尚未准备。",
            }
        ],
        "sourceRevision": "abc123",
        "sourceDirty": True,
        "sourceDigest": "sha256:" + "f" * 64,
        "services": {},
        "readiness": {},
        "runtimeCapabilities": {},
        "images": {},
        "initialization": {},
    }


def test_bootstrap_status_is_structured_and_scoped_to_current_assembly(
    tmp_path: Path, monkeypatch
) -> None:
    state = tmp_path / "state"
    assembly = tmp_path / "assembly-a"
    assembly.mkdir()
    monkeypatch.setattr(dev, "lifecycle", lambda: SimpleNamespace(state=state))
    monkeypatch.setattr(dev, "current_bundle", lambda *_args: assembly)

    completed = subprocess.CompletedProcess(
        ["compose", "run"],
        0,
        stdout=(
            '{"bootstrapState":"ALREADY_APPLIED","documentCount":99}\n'
        ),
        stderr="",
    )
    recorded = dev._record_bootstrap_status("groundwater", completed)
    assert recorded == {
        "status": "PASS",
        "state": "ALREADY_APPLIED",
        "documentCount": 99,
        "changedDocumentCount": 0,
        "assemblyDigest": "assembly-a",
    }
    assert dev._load_bootstrap_status("groundwater") == recorded

    later = tmp_path / "assembly-b"
    later.mkdir()
    monkeypatch.setattr(dev, "current_bundle", lambda *_args: later)
    assert dev._load_bootstrap_status("groundwater") == {
        "status": "NOT_RUN",
        "assemblyDigest": "assembly-b",
    }


def test_bootstrap_failure_records_specific_service_error(tmp_path: Path, monkeypatch):
    state = tmp_path / "state"
    assembly = tmp_path / "assembly-a"
    assembly.mkdir()
    monkeypatch.setattr(dev, "lifecycle", lambda: SimpleNamespace(state=state))
    monkeypatch.setattr(dev, "current_bundle", lambda *_args: assembly)

    completed = subprocess.CompletedProcess(
        ["compose", "run"],
        1,
        stdout="",
        stderr="KNOWLEDGE_BOOTSTRAP_FAILED:VERSION_CONFLICT\n",
    )
    recorded = dev._record_bootstrap_status("groundwater", completed)
    assert recorded["status"] == "FAILED"
    assert recorded["errorCode"] == (
        "KNOWLEDGE_BOOTSTRAP_FAILED:VERSION_CONFLICT"
    )


def test_status_uses_running_release_and_matching_bootstrap_receipt(tmp_path, monkeypatch):
    generated = tmp_path / 'assemblies'
    releases = generated / '.releases' / 'groundwater'
    running = releases / ('a' * 64)
    compiled = releases / ('b' * 64)
    services = ['kernel-service', 'data-service', 'knowledge-next-service', 'executor-control']
    for root, names in [(running, services), (compiled, [*services, 'future-worker'])]:
        root.mkdir(parents=True)
        (root / 'compose.json').write_text(json.dumps({'services': dict.fromkeys(names, {})}))
    records = [
        {'Name': name, 'Service': name, 'State': 'running', 'Health': 'healthy', 'Image': f'{name}:latest'}
        for name in services
    ]
    inspected = [
        {'Name': name, 'Config': {'Labels': {'com.docker.compose.service': name}}, 'Mounts': [
            {'Destination': f'/config/{filename}', 'Source': str(running / filename), 'Type': 'bind'},
        ]}
        for name, filename in [('kernel-service', 'kernel-config.json'), ('executor-control', 'executor-config.json')]
    ]
    monkeypatch.setattr(dev, 'generated_root', lambda _root: generated)
    monkeypatch.setattr(dev, 'current_bundle', lambda *_args: compiled)
    monkeypatch.setattr(dev, 'lifecycle', lambda: SimpleNamespace(state=tmp_path / 'state'))
    monkeypatch.setattr(dev, '_worktree_identity', lambda _root: ('test-head', True, 'f' * 64))
    monkeypatch.setattr(dev, '_research_runtime', lambda _product: (compiled / 'compose.json', None, None, None, 'source-groundwater'))
    monkeypatch.setattr(dev, 'docker_arguments', lambda args: args)
    monkeypatch.setattr(dev.subprocess, 'run', lambda args, **_kwargs: subprocess.CompletedProcess(args, 0, json.dumps(inspected), ''))
    monkeypatch.setattr(dev, '_run_compose', lambda _product, args: subprocess.CompletedProcess(
        args, 0, json.dumps(records if args[0] == 'ps' else {'status': 'UP', 'components': {'native': 'UP', 'qgis': 'UP'}}), ''
    ))
    receipt = {'status': 'PASS', 'state': 'ALREADY_APPLIED', 'documentCount': 111, 'assemblyDigest': running.name}
    path = dev._bootstrap_status_path('groundwater')
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(receipt))

    report = dev._status('groundwater')
    assert report['status'] == 'PASS'
    assert report['initialization']['knowledgeBootstrap'] == receipt
    assert report['runningAssemblyDigest'] == running.name
    assert report['compiledAssemblyDigest'] == compiled.name

    inspected[1]['Mounts'][0]['Source'] = str(compiled / 'executor-config.json')
    mixed = dev._status('groundwater')
    assert mixed['status'] != 'PASS'
    assert mixed['initialization']['knowledgeBootstrap']['status'] == 'UNKNOWN'
    assert any(issue['code'] == 'RESEARCH_RUNNING_ASSEMBLY_UNKNOWN' for issue in mixed['issues'])
