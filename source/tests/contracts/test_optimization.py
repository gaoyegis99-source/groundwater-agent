from __future__ import annotations

import asyncio
from dataclasses import replace

from scripts.evaluation import measure_agent_context as measure


def test_current_common_domain_tool_surface_fits_hard_input_budget(kernel_settings):
    settings = kernel_settings("groundwater")
    result = asyncio.run(measure.measure(settings))
    assert len(settings.models) == 10
    assert result["available_input_tokens"] == 131072 - 4096
    assert {mode: value["tool_count"] for mode, value in result["modes"].items()} == {
        "DIALOGUE_READ_ONLY": 9,
        "DIALOGUE_EXECUTION_PROPOSAL": 21,
        "WORKFLOW_DRAFT": 11,
    }
    initial_token_ceiling = {
        "DIALOGUE_READ_ONLY": 15_000,
        # Ten models consume 20,491 tokens with the DRASTIC model schema;
        # retain a 259-token regression margin.
        "DIALOGUE_EXECUTION_PROPOSAL": 20_750,
        "WORKFLOW_DRAFT": 15_000,
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
