from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.maintenance import dependency_materializer


def test_locate_compatible_scope_reuses_verified_read_only_materialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    cache = tmp_path / "cache" / "groundwater-v6"
    dependencies = cache / "deps"
    current = dependencies / ("sha256-" + "1" * 64)
    compatible = dependencies / ("sha256-" + "2" * 64)
    incompatible = dependencies / ("sha256-" + "3" * 64)
    for candidate in (current, compatible, incompatible):
        (candidate / "python").mkdir(parents=True)
    paths = SimpleNamespace(cache=cache)

    monkeypatch.setattr(dependency_materializer, "root", lambda *_args: current)
    monkeypatch.setattr(
        dependency_materializer,
        "verify_scope",
        lambda *_args: (_ for _ in ()).throw(
            dependency_materializer.DependencyRefusal(
                "DEPENDENCY_SCOPE_NOT_MATERIALIZED"
            )
        ),
    )
    monkeypatch.setattr(
        dependency_materializer,
        "_candidate_scope",
        lambda _source, candidate, _scope: (
            ([{"path": "native/pkg.whl"}], "2" * 64)
            if candidate == compatible
            else None
        ),
    )

    result = dependency_materializer.locate_compatible_scope(source, paths, "python")

    assert result == {
        "status": "PASS",
        "scope": "python",
        "dependencyDigest": "2" * 64,
        "path": str(compatible / "python"),
        "files": 1,
        "reused": True,
    }
    assert not current.joinpath("python.receipt.json").exists()


def test_locate_compatible_scope_refuses_when_no_verified_candidate_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache" / "groundwater-v6"
    current = cache / "deps" / ("sha256-" + "1" * 64)
    current.mkdir(parents=True)
    paths = SimpleNamespace(cache=cache)
    monkeypatch.setattr(dependency_materializer, "root", lambda *_args: current)
    monkeypatch.setattr(
        dependency_materializer,
        "verify_scope",
        lambda *_args: (_ for _ in ()).throw(
            dependency_materializer.DependencyRefusal(
                "DEPENDENCY_SCOPE_NOT_MATERIALIZED"
            )
        ),
    )
    monkeypatch.setattr(
        dependency_materializer, "_candidate_scope", lambda *_args: None
    )

    with pytest.raises(
        dependency_materializer.DependencyRefusal,
        match="DEPENDENCY_SCOPE_NOT_MATERIALIZED",
    ):
        dependency_materializer.locate_compatible_scope(
            tmp_path / "source", paths, "python"
        )
