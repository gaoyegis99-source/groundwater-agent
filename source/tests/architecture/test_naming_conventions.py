from pathlib import Path

from scripts.checks.check_naming import naming_violations


ROOT = Path(__file__).resolve().parents[2]


def test_current_source_follows_repository_naming_conventions() -> None:
    assert naming_violations(ROOT) == []


def test_checker_reports_mixed_python_and_frontend_file_names(tmp_path: Path) -> None:
    python_file = tmp_path / "kernel" / "badModule.py"
    frontend_file = tmp_path / "web" / "src" / "useMixedName.ts"
    python_file.parent.mkdir(parents=True)
    frontend_file.parent.mkdir(parents=True)
    python_file.touch()
    frontend_file.touch()

    assert naming_violations(tmp_path) == [
        "Frontend file must use PascalCase for components or kebab-case for modules: "
        "web/src/useMixedName.ts",
        "Python module must use snake_case: kernel/badModule.py",
    ]


def test_checker_ignores_generated_sources(tmp_path: Path) -> None:
    for relative in ("web/src/generated/badModule.ts",):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

    assert naming_violations(tmp_path) == []


def test_private_snake_case_python_modules_are_valid(tmp_path: Path) -> None:
    private = tmp_path / "packages/lightweight-agent-core/src/platform_lightweight_agent/_driver_common.py"
    invalid = tmp_path / "packages/lightweight-agent-core/src/platform_lightweight_agent/__MixedName.py"
    private.parent.mkdir(parents=True)
    private.touch()
    invalid.touch()

    assert naming_violations(tmp_path) == [
        "Python module must use snake_case: "
        "packages/lightweight-agent-core/src/platform_lightweight_agent/__MixedName.py"
    ]
