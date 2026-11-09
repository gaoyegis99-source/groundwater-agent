from __future__ import annotations

from pathlib import Path

from assembler_publication import generated_root


def test_generated_root_defaults_to_xdg_cache(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("PLATFORM_GENERATED_ROOT", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    result = generated_root(tmp_path / "source")
    assert result.is_relative_to(
        tmp_path / "cache/platform-assembler/build/assemblies/worktrees"
    )
    assert not result.is_relative_to(tmp_path / "source")


def test_generated_root_accepts_absolute_external_path(
    monkeypatch, tmp_path: Path
) -> None:
    target = tmp_path / "runtime" / "build"
    monkeypatch.setenv("PLATFORM_GENERATED_ROOT", str(target))
    assert generated_root(tmp_path / "source") == target


def test_generated_root_rejects_relative_path(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PLATFORM_GENERATED_ROOT", "../runtime/build")
    try:
        generated_root(tmp_path / "source")
    except ValueError as error:
        assert str(error) == "GENERATED_ROOT_NOT_ABSOLUTE"
    else:  # pragma: no cover - explicit refusal is the contract
        raise AssertionError("relative generated root was accepted")
