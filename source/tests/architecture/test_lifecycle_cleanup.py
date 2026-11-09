from __future__ import annotations

from pathlib import Path

import pytest

from scripts.maintenance.lifecycle_cleanup import (
    CleanupRefusal,
    apply_plan,
    cache_clean_plan,
    engine_reset_plan,
)
from scripts.maintenance.xdg_layout import resolve_lifecycle_paths


def _paths(tmp_path: Path):
    return resolve_lifecycle_paths(
        {
            "HOME": str(tmp_path / "home"),
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "XDG_DATA_HOME": str(tmp_path / "data"),
            "XDG_STATE_HOME": str(tmp_path / "state"),
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
            "XDG_RUNTIME_DIR": str(tmp_path / "run"),
        },
        uid=1234,
    )


def test_cache_clean_never_includes_engine_or_authoritative_data(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    (paths.build / "output").mkdir(parents=True)
    (paths.build / "output/file").write_text("generated")
    (paths.docker / "keep").mkdir(parents=True)
    paths.instances.mkdir(parents=True)
    (paths.instances / "keep").write_text("authoritative")

    plan = cache_clean_plan(paths)
    targets = {Path(item["path"]).name for item in plan["targets"]}
    assert targets == {"build", "uv", "npm", "deps", "toolchains"}
    result = apply_plan(paths, plan, plan["confirmationDigest"])
    assert result["status"] == "PASS"
    assert not paths.build.exists()
    assert (paths.docker / "keep").is_dir()
    assert (paths.instances / "keep").read_text() == "authoritative"


def test_plan_is_invalidated_when_target_changes(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.build.mkdir(parents=True)
    plan = cache_clean_plan(paths)
    (paths.build / "late").write_text("changed")
    with pytest.raises(CleanupRefusal, match="CLEANUP_TARGET_CHANGED"):
        apply_plan(paths, plan, plan["confirmationDigest"])


def test_engine_reset_targets_only_disposable_docker_root(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    (paths.docker / "buildkit").mkdir(parents=True)
    plan = engine_reset_plan(paths)
    assert [item["path"] for item in plan["targets"]] == [str(paths.docker)]
    apply_plan(paths, plan, plan["confirmationDigest"])
    assert not paths.docker.exists()


def test_cleanup_rejects_symlink_target(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    paths.cache.mkdir(parents=True)
    paths.build.symlink_to(outside, target_is_directory=True)
    with pytest.raises(Exception, match="SYMLINK"):
        cache_clean_plan(paths)
