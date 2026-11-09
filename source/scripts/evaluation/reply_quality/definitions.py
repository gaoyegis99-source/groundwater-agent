"""Frozen constants and digest primitives for reply-quality evaluation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "tests/fixtures/reply-quality-evaluation.v1.json"
PROJECT = "groundwater-demo"
REPETITIONS = 3
SUPPORTED_PROVIDERS = {"minimax", "deepseek"}
REQUIRED_ENVIRONMENT = (
    "PLATFORM_AGENT_PROVIDER",
    "PLATFORM_AGENT_PROTOCOL",
    "PLATFORM_AGENT_BASE_URL",
    "PLATFORM_AGENT_MODEL",
    "PLATFORM_AGENT_API_KEY_FILE",
    "PLATFORM_AGENT_CONTEXT_WINDOW_TOKENS",
    "PLATFORM_AGENT_MAX_OUTPUT_TOKENS",
    "PLATFORM_AGENT_REQUEST_TIMEOUT_SECONDS",
    "PLATFORM_AGENT_SEGMENT_TIMEOUT_SECONDS",
)
REVIEW_DIMENSIONS = (
    "directness",
    "concision",
    "readability",
    "evidence_fidelity",
    "next_step_clarity",
    "option_relevance",
    "option_distinction",
    "natural_language_actionability",
    "confirmation_boundary_safety",
)
AUTOMATIC_GATES = {
    "run_count_24",
    "case_iteration_matrix_exact",
    "session_and_agent_runs_unique_24",
    "valid_final_reply_24_of_24",
    "provider_fallbacks_zero",
    "provider_retry_policy_24",
    "all_automatic_checks_pass",
    "technical_identifier_leaks_zero",
    "duplicate_sources_zero",
    "read_only_side_effects_zero",
    "known_fact_errors_zero",
    "generic_continuation_zero",
    "dialogue_policy_exact_24",
    "interaction_option_contracts_24",
    "option_auto_followups_exact",
    "scenario_oracles_24_of_24",
    "response_bindings_24",
}
TECHNICAL_LEAK = re.compile(
    r"sha256:[0-9a-f]{64}|"
    r"(?:knowledge|workflow|resource|model|session|draft|agent[_-]?run|"
    r"tool[_-]?call)[ _-]?id|workflow[ _-]?run|chunk[ _-]?index|checksum|"
    r"\b(?:submit_reply|run_[a-z0-9_]+)\b|"
    r"\b(?:minimax|deepseek)\b|"
    r"/api/v\d+/knowledge|https?://[^\s)]+|"
    r"\b(?:groundwater|knowledge|workflow)\.[a-z0-9._-]+@\d+(?:\.\d+)+\b|"
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
GENERIC_CONTINUATION = re.compile(
    r"是否继续|需要我继续|要不要继续|如需[^。！？!?]{0,24}(?:我可以|请告诉我)",
)
OPTION_FORBIDDEN = re.compile(
    r"跳过确认|绕过确认|无需确认|自动批准|直接批准|"
    r"requestedMode|工作流运行|调用.{0,12}模型|切换.{0,8}模式|"
    r"(?:请|立即|直接)?(?:执行|运行|计算).{0,24}(?:分析|模型|工作流)",
    re.IGNORECASE,
)
EXPECTED_DOMAIN_PACKAGE = {
    "domainId": "groundwater",
    "version": "6.0.0",
    "checksum": "sha256:577ce573b7e67fad7367323bce87967c713c44c63d243467aaf47137fc7c53e6",
}
EXPECTED_DATA_FIXTURE_CHECKSUM = (
    "sha256:86c257568bc21eda70434b523f13670da614a8444e01a968058bab96d3f4bf6a"
)
EXPECTED_MODEL_IDS = {
    "groundwater.summary",
    "groundwater.monitoring-trend",
    "groundwater.monitoring-coverage",
    "groundwater.flow-field",
    "groundwater.transfer-learning-analysis",
    "groundwater.render-map",
    "groundwater.source-sustainability",
}


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()
