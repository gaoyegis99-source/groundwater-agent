from __future__ import annotations

import subprocess
import sys

import platform_lightweight_agent as legacy
from platform_lightweight_agent import api


def test_small_typed_api_reuses_legacy_symbols_without_narrowing_compatibility() -> None:
    assert len(api.__all__) == 41
    assert "workflow" in api.__all__
    assert set(api.__all__) <= set(legacy.__all__)
    for name in api.__all__:
        assert getattr(api, name) is getattr(legacy, name)
    for name in legacy.__all__:
        assert getattr(legacy, name) is not None
    assert set(legacy.__all__) <= set(dir(legacy))


def test_root_import_is_lazy_and_keeps_optional_dependencies_unloaded() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import platform_lightweight_agent as package; "
            "assert len(package.__all__) == 219; "
            "assert {name for name in sys.modules "
            "if name.startswith('platform_lightweight_agent.')} "
            "== {'platform_lightweight_agent._legacy_exports'}; "
            "import platform_lightweight_agent.api as api; "
            "assert len(api.__all__) == 41; "
            "assert {name for name in sys.modules "
            "if name.startswith('platform_lightweight_agent.')} "
            "== {'platform_lightweight_agent._legacy_exports', "
            "'platform_lightweight_agent.api'}; "
            "assert not [name for name in sys.modules "
            "if name.startswith(('anyio', 'jsonschema', 'pydantic', 'agents', "
            "'copier', 'dbos', 'sqlalchemy'))]",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
