from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from scripts.maintenance import workspace_cleanup
from scripts.maintenance.xdg_layout import resolve_lifecycle_paths


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(["git", "-C", str(root), *arguments], check=True, capture_output=True)


def _workspace(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "workspace"
    source = root / "source"
    source.mkdir(parents=True)
    _git(root, "init", "-q")
    (root / ".gitignore").write_text(
        ".venv/\nnode_modules/\n__pycache__/\n.generated/\n"
        "source/data/tasks.sqlite3*\nsource/data/logs/\n",
        encoding="utf-8",
    )
    _git(root, "add", ".gitignore")
    return root, source


def _paths(tmp_path: Path):
    return resolve_lifecycle_paths(
        {
            "HOME": str(tmp_path),
            "XDG_DATA_HOME": str(tmp_path / "xdg-data"),
            "XDG_STATE_HOME": str(tmp_path / "xdg-state"),
            "XDG_CONFIG_HOME": str(tmp_path / "xdg-config"),
            "XDG_CACHE_HOME": str(tmp_path / "xdg-cache"),
            "XDG_RUNTIME_DIR": str(tmp_path / "xdg-runtime"),
        }
    )


def test_workspace_plan_apply_and_repeat_empty(tmp_path: Path) -> None:
    root, source = _workspace(tmp_path)
    paths = _paths(tmp_path)
    protected_xdg = paths.data / ".venv/bin/python"
    protected_xdg.parent.mkdir(parents=True)
    protected_xdg.write_bytes(b"xdg-data")
    for path, content in (
        (source / ".venv/bin/python", b"python"),
        (source / "web/node_modules/pkg/index.js", b"javascript"),
        (source / "pkg/__pycache__/module.pyc", b"bytecode"),
        (source / ".generated/assembly.json", b"{}"),
        (source / "data/tasks.sqlite3-wal", b"sqlite"),
        (source / "data/logs/dev.log", b"log"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    plan = workspace_cleanup.workspace_clean_plan(root, source)

    assert plan["candidateCount"] == 6
    assert plan["files"] == 6
    receipt = workspace_cleanup.apply_workspace_clean(
        paths, plan, plan["confirmationDigest"]
    )
    assert receipt["status"] == "PASS"
    assert protected_xdg.read_bytes() == b"xdg-data"
    assert workspace_cleanup.workspace_clean_plan(root, source)["candidateCount"] == 0


def test_workspace_rejects_unignored_candidate(tmp_path: Path) -> None:
    root, source = _workspace(tmp_path)
    (source / "build").mkdir()
    (source / "build/output.bin").write_bytes(b"output")

    with pytest.raises(
        workspace_cleanup.WorkspaceCleanupRefusal,
        match="WORKSPACE_UNIGNORED_CONTENT_PROTECTED",
    ):
        workspace_cleanup.workspace_clean_plan(root, source)


def test_workspace_apply_rejects_changed_target(tmp_path: Path) -> None:
    root, source = _workspace(tmp_path)
    target = source / ".venv/bin/python"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"before")
    plan = workspace_cleanup.workspace_clean_plan(root, source)
    target.write_bytes(b"after")

    with pytest.raises(
        workspace_cleanup.WorkspaceCleanupRefusal,
        match="WORKSPACE_PLAN_CHANGED",
    ):
        workspace_cleanup.apply_workspace_clean(
            _paths(tmp_path), plan, plan["confirmationDigest"]
        )


def test_workspace_rejects_tracked_content_even_when_ignored(tmp_path: Path) -> None:
    root, source = _workspace(tmp_path)
    target = source / ".venv/bin/python"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"tracked")
    _git(root, "add", "-f", "source/.venv/bin/python")

    with pytest.raises(
        workspace_cleanup.WorkspaceCleanupRefusal,
        match="WORKSPACE_TRACKED_CONTENT_PROTECTED",
    ):
        workspace_cleanup.workspace_clean_plan(root, source)


def test_workspace_rejects_linked_worktree_candidate(
    tmp_path: Path, monkeypatch
) -> None:
    root, source = _workspace(tmp_path)
    linked = root / "linked"
    target = linked / ".venv/bin/python"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"linked")
    monkeypatch.setattr(
        workspace_cleanup,
        "_linked_worktrees",
        lambda _root: (root.absolute(), linked.absolute()),
    )

    with pytest.raises(
        workspace_cleanup.WorkspaceCleanupRefusal,
        match="WORKSPACE_LINKED_WORKTREE_PROTECTED",
    ):
        workspace_cleanup.workspace_clean_plan(root, source)


def test_workspace_apply_rejects_target_used_by_process(tmp_path: Path) -> None:
    root, source = _workspace(tmp_path)
    target = source / ".venv"
    target.mkdir()
    target.joinpath("content").write_bytes(b"in-use")
    plan = workspace_cleanup.workspace_clean_plan(root, source)
    process = subprocess.Popen(["sleep", "30"], cwd=target)
    try:
        with pytest.raises(
            workspace_cleanup.WorkspaceCleanupRefusal,
            match="WORKSPACE_TARGET_IN_USE",
        ):
            workspace_cleanup.apply_workspace_clean(
                _paths(tmp_path), plan, plan["confirmationDigest"]
            )
        assert target.is_dir()
    finally:
        process.terminate()
        process.wait(timeout=5)
