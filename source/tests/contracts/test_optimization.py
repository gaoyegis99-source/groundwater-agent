from __future__ import annotations

import asyncio
from dataclasses import replace

from scripts.evaluation import measure_agent_context as measure


def test_current_common_domain_tool_surface_fits_hard_input_budget(kernel_settings):
    settings = kernel_settings("groundwater")
    result = asyncio.run(measure.measure(settings))
    assert len(settings.models) in {10, 18}
    assert result["available_input_tokens"] == 131072 - 4096
    composition = settings.workflow_composition is not None
    catalog_discovery = composition or bool(settings.operator_uses)
    assert {mode: value["tool_count"] for mode, value in result["modes"].items()} == {
        "DIALOGUE_READ_ONLY": 9 + (2 if catalog_discovery else 0),
        "DIALOGUE_EXECUTION_PROPOSAL": 11 + len(settings.models) + (3 if composition else 0),
        "WORKFLOW_DRAFT": 11 + (1 if composition else 0),
    }
    execution_ceiling = 22_600 if len(settings.models) == 18 else 20_750
    initial_token_ceiling = {
        "DIALOGUE_READ_ONLY": 15_000,
        # Composition adds a graph and validation schema to the preset tools.
        "DIALOGUE_EXECUTION_PROPOSAL": execution_ceiling + (4_400 if composition else 0),
        "WORKFLOW_DRAFT": 15_500 if composition else 15_000,
    }
    for name, mode in result["modes"].items():
        assert mode["fits_input_budget"]
        assert mode["initial_total_tokens"] <= initial_token_ceiling[name]
        assert (
            mode["initial_total_tokens"] + mode["remaining_input_tokens"]
            == result["available_input_tokens"]
        )
        assert mode["general_instructions_tokens"] > 0
        assert mode["domain_instructions_tokens"] > 0
        assert mode["tools_tokens"] > 0


def test_context_measurement_reports_unusable_budget_without_truncation(
    kernel_settings, monkeypatch
):
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:1")
    settings = replace(
        kernel_settings("groundwater"),
        agent_context_window_tokens=1000,
        agent_max_output_tokens=100,
    )
    result = asyncio.run(measure.measure(settings))
    assert all(not mode["fits_input_budget"] for mode in result["modes"].values())
    assert all(mode["remaining_input_tokens"] < 0 for mode in result["modes"].values())
