from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_interactive_development_code_is_not_imported_by_runtime_or_assembler() -> None:
    runtime_roots = (
        ROOT / "kernel/src",
        ROOT / "services/data/src",
        ROOT / "services/knowledge-next/src",
        ROOT / "services/executor/src",
        ROOT / "platform/assembler",
        ROOT / "packages",
    )
    offenders = []
    for runtime_root in runtime_roots:
        for path in runtime_root.rglob("*.py"):
            if "scripts.development.interactive" in path.read_text(encoding="utf-8"):
                offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


def test_interactive_development_assets_live_only_in_development_tooling() -> None:
    development = ROOT / "scripts/development/interactive"
    assert development.is_dir()
    assert (development / "vite-main.config.ts").is_file()
    assert (development / "vite-data.config.ts").is_file()
    assert (development / "vite-knowledge.config.ts").is_file()
    assert "127.0.0.1:28186" in (development / "vite-data.config.ts").read_text(
        encoding="utf-8"
    )
    knowledge_config = (development / "vite-knowledge.config.ts").read_text(
        encoding="utf-8"
    )
    assert "127.0.0.1:28186" in knowledge_config
    assert not any(port in knowledge_config for port in ("28188", "28189"))
    assert not (ROOT / "web/src/development").exists()
    assert not (ROOT / "services/data/src/platform_data_service/development").exists()
    assert not (
        ROOT / "services/knowledge-next/src/platform_knowledge_next/development"
    ).exists()
    assert not (
        ROOT / "services/executor/src/platform_executor_service/development"
    ).exists()
