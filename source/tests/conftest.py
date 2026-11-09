from __future__ import annotations

from pathlib import Path
import json
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "platform" / "assembler"))

from assembler import compile_product  # noqa: E402
from platform_kernel.composition import install_trace_processors  # noqa: E402
from platform_kernel.settings import IntentRouterSettings, KernelSettings  # noqa: E402
from scripts.lib.assembly_paths import compiled_bundle  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_trace_processors():
    """Keep direct Kernel contract tests off the SDK's network exporter."""

    install_trace_processors([])
    yield
    install_trace_processors([])


@pytest.fixture
def kernel_settings(tmp_path):
    def build(product: str = "groundwater") -> KernelSettings:
        manifest = ROOT / "products" / product / "product-manifest.json"
        compile_product(ROOT, manifest)
        return KernelSettings.from_config(
            compiled_bundle(ROOT, product) / "kernel-config.json",
            data_root=tmp_path / product,
            project_id=json.loads(manifest.read_text())["projectId"],
            agent_provider="deepseek",
            agent_protocol="responses",
            agent_base_url="http://agent.test/v1",
            agent_model="test-model",
            agent_api_key="test-key",
            agent_context_window_tokens=131072,
            agent_max_output_tokens=4096,
            agent_request_timeout_seconds=120,
            agent_segment_timeout_seconds=300,
            intent_router=IntentRouterSettings.from_values(
                provider="deepseek",
                protocol="responses",
                base_url="http://router.test/v1",
                model="test-router-model",
                api_key="test-router-key",
                context_window_tokens=131072,
                max_output_tokens=256,
                request_timeout_seconds=120,
                segment_timeout_seconds=300,
            ),
        )

    return build
