"""Check current-source file naming; code symbols are enforced by Ruff N rules."""
from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
PYTHON_ROOTS = (
    "kernel",
    "services",
    "platform",
    "packages",
    "scripts",
    "tests",
)
FRONTEND_ROOTS = (
    "web/src",
    "web/e2e",
    "services/data/web/src",
    "services/knowledge-next/ui/src",
)
IGNORED_PARTS = frozenset({
    ".generated",
    ".venv",
    "build",
    "dist",
    "generated",
    "node_modules",
    "site-packages",
})
# Python's private implementation modules conventionally use one leading
# underscore; they remain snake_case and should not fail the file-name gate.
_PYTHON_MODULE = re.compile(r"_?[a-z][a-z0-9_]*")
_FRONTEND_PASCAL = re.compile(r"[A-Z][A-Za-z0-9]*")
_FRONTEND_KEBAB = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
_FRONTEND_MODIFIERS = frozenset({"spec", "test", "worker"})


def _ignored(path: Path) -> bool:
    return any(part in IGNORED_PARTS for part in path.parts)


def _python_name_valid(path: Path) -> bool:
    return path.stem in {"__init__", "__main__"} or bool(
        _PYTHON_MODULE.fullmatch(path.stem)
    )


def _frontend_name_valid(path: Path) -> bool:
    parts = path.name.removesuffix(path.suffix).split(".")
    base, modifiers = parts[0], parts[1:]
    return (
        all(modifier in _FRONTEND_MODIFIERS for modifier in modifiers)
        and bool(
            _FRONTEND_PASCAL.fullmatch(base)
            or _FRONTEND_KEBAB.fullmatch(base)
        )
    )


def naming_violations(root: Path = ROOT) -> list[str]:
    violations: list[str] = []
    for relative in PYTHON_ROOTS:
        source_root = root / relative
        if not source_root.exists():
            continue
        for path in source_root.rglob("*.py"):
            if not _ignored(path.relative_to(root)) and not _python_name_valid(path):
                violations.append(
                    f"Python module must use snake_case: {path.relative_to(root)}"
                )
    for relative in FRONTEND_ROOTS:
        source_root = root / relative
        if not source_root.exists():
            continue
        for path in source_root.rglob("*"):
            if (
                path.suffix in {".ts", ".tsx"}
                and not _ignored(path.relative_to(root))
                and not _frontend_name_valid(path)
            ):
                violations.append(
                    "Frontend file must use PascalCase for components or "
                    f"kebab-case for modules: {path.relative_to(root)}"
                )
    return sorted(violations)


def main() -> int:
    violations = naming_violations()
    if violations:
        print("\n".join(violations))
        return 1
    print("NAMING_CHECK_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
