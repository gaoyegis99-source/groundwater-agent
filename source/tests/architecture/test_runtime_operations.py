from __future__ import annotations

from pathlib import Path

from scripts.maintenance.config_runtime import apply_migration, migration_plan
from scripts.maintenance.xdg_layout import resolve_lifecycle_paths


ROOT = Path(__file__).resolve().parents[2]


def _paths(tmp_path: Path):
    return resolve_lifecycle_paths(
        {
            "HOME": str(tmp_path / "home"),
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "XDG_DATA_HOME": str(tmp_path / "data"),
            "XDG_STATE_HOME": str(tmp_path / "state"),
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
            "XDG_RUNTIME_DIR": str(tmp_path / "run"),
        }
    )


def test_source_env_migrates_to_protected_read_only_research_configuration(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / ".env").write_text(
        "PLATFORM_AGENT_MODEL=model-a\n"
        "PLATFORM_GATEWAY_PORT=28086\n"
        "PLATFORM_AGENT_API_KEY=do-not-log-this\n"
        "PLATFORM_SECRETS_DIR=/old/path\n"
    )
    paths = _paths(tmp_path / "lifecycle")
    plan = migration_plan(source, paths)
    assert "do-not-log-this" not in str(plan)

    receipt = apply_migration(source, paths, plan, plan["confirmationDigest"])

    assert receipt["status"] == "PASS"
    assert not (source / ".env").exists()
    profile = paths.config / "profiles/research.env"
    assert profile.stat().st_mode & 0o077 == 0
    assert "PLATFORM_GATEWAY_PORT=28186" in profile.read_text()
    assert "PLATFORM_OPERATOR_PORT" not in profile.read_text()
    secret = paths.config / "secrets/research/agent-api-key"
    assert secret.read_text() == "do-not-log-this\n"
    assert secret.parent.stat().st_mode & 0o077 == 0
    assert secret.stat().st_mode & 0o777 == 0o444
