from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

from jsonschema import validate

from ..contracts import DataVersionRefV2
from ..errors import AgentRunError, UpstreamServiceError
from ..settings import (
    ExecutableModelDefinition,
    ExecutionCaseDefinition,
    KernelSettings,
)
from .observation_client import ObservationClient
from .persistence.run_queries import load_request_message
from .result_processing import PreparedAction


_EXECUTION_VERBS = ("运行", "执行", "启动", "开始运行", "run", "execute")
_NEGATED_EXECUTION = ("停止", "取消", "拒绝", "不要执行", "不要运行", "不执行", "不运行")
_NEGATED_TOOL_USE = ("不要使用", "不要调用", "do not use", "don't use")


@dataclass(frozen=True)
class ResolvedExecutionCase:
    case: ExecutionCaseDefinition
    model: ExecutableModelDefinition
    profile: str
    action: PreparedAction
    goal: str
    reason: str


@dataclass(frozen=True)
class ExplicitModelToolRequest:
    tool: str
    arguments: dict[str, object]


def matches_execution_case_request(settings: KernelSettings, message: str) -> bool:
    """Recognize configured case entry points without granting execution authority."""
    lowered = message.casefold()
    if any(term.casefold() in lowered for term in _NEGATED_EXECUTION):
        return False
    has_action = any(verb.casefold() in lowered for verb in _EXECUTION_VERBS)
    return has_action and any(
        alias.casefold() in lowered
        for case in settings.execution_cases
        for alias in case.aliases
    )


def matches_explicit_model_tool_request(
    settings: KernelSettings, message: str,
) -> bool:
    """Recognize an explicit request to invoke one installed model tool.

    This is an intent rule only.  The normal preparation and confirmation
    boundaries still authorize the eventual action.  Requiring the command
    phrase plus an exact installed tool name avoids treating documentation or
    general model discussion as execution authority.
    """
    lowered = message.casefold()
    if any(term.casefold() in lowered for term in (*_NEGATED_EXECUTION, *_NEGATED_TOOL_USE)):
        return False
    for model in settings.models:
        escaped = re.escape(model.tool_name.casefold())
        if re.search(
            rf"(?:使用|调用)\s*工具\s*[`'\"]?{escaped}(?![a-z0-9_])",
            lowered,
        ) or re.search(
            rf"(?:use|invoke|call)\s+(?:the\s+)?tool\s+[`'\"]?{escaped}(?![a-z0-9_])",
            lowered,
        ):
            return True
    return False


def parse_explicit_model_tool_request(
    settings: KernelSettings, message: str,
) -> ExplicitModelToolRequest | None:
    """Parse the closed structured payload attached to an explicit tool command."""
    if not matches_explicit_model_tool_request(settings, message):
        return None
    marker = re.search(r"\[Model execution\]\s*", message, re.IGNORECASE)
    if marker is None:
        return None
    try:
        payload, end = json.JSONDecoder().raw_decode(message[marker.end():])
    except (json.JSONDecodeError, TypeError):
        return None
    if message[marker.end() + end:].strip() or not isinstance(payload, dict):
        return None
    if set(payload) != {"tool", "arguments"}:
        return None
    tool = payload.get("tool")
    arguments = payload.get("arguments")
    installed = {model.tool_name for model in settings.models}
    if (
        not isinstance(tool, str)
        or tool not in installed
        or not isinstance(arguments, dict)
        or not all(isinstance(name, str) for name in arguments)
    ):
        return None
    return ExplicitModelToolRequest(tool, dict(arguments))


def load_explicit_model_tool_request(
    database: Path, settings: KernelSettings, agent_run_id: str,
) -> dict[str, object] | None:
    """Reconstruct the same user binding for proposal persistence and replay."""
    message = load_request_message(database, agent_run_id)
    request = None if message is None else parse_explicit_model_tool_request(settings, message)
    return None if request is None else {"tool": request.tool, "arguments": request.arguments}


_COMPARISON = re.compile(
    r"(?:比较|对比)\s*(?P<left>[^：:，,；;。!?！？]{2,48}?)\s*"
    r"(?:和|与|、|(?:\s+vs\.?\s+))\s*"
    r"(?P<right>[^：:，,；;。!?！？]{2,48})(?=[：:，,；;。!?！？]|$)",
    re.IGNORECASE,
)


def parse_comparison_terms(message: str) -> tuple[str, str] | None:
    """Extract two user-named business methods from an explicit comparison."""
    match = _COMPARISON.search(message)
    if match is None:
        return None
    terms = tuple(match.group(name).strip() for name in ("left", "right"))
    if any(not term for term in terms) or terms[0] == terms[1]:
        return None
    return terms


_CHINESE_UNIT_QUESTION = re.compile(
    r"(?:说明|列出)\s*(?P<terms>[^；;。!?！？]{2,160}?)\s*"
    r"(?:分别\s*)?(?:使用|采用)\s*(?:什么|何种)?\s*单位",
)
_ENGLISH_UNIT_QUESTION = re.compile(
    r"(?:what|which)\s+units?\s+(?:do|does)\s+"
    r"(?P<terms>[^?.!;]{2,160}?)\s+use\b",
    re.IGNORECASE,
)
_ANSWER_TERM_SEPARATOR = re.compile(
    r"\s*(?:、|，|,|和|与|及|\band\b)\s*",
    re.IGNORECASE,
)


def parse_required_response_terms(message: str) -> tuple[str, ...]:
    """Extract explicit list items that a direct unit answer must preserve.

    This intentionally recognizes only closed, enumerated unit questions.  It
    does not try to infer topics from arbitrary prose, so ordinary answers may
    still use natural synonyms while a request for several named measurements
    is checked item by item at the Kernel reply boundary.
    """
    match = _CHINESE_UNIT_QUESTION.search(message)
    if match is None:
        match = _ENGLISH_UNIT_QUESTION.search(message)
    if match is None:
        return ()
    terms = tuple(
        item.strip(" \t\r\n`'\"“”‘’「」")
        for item in _ANSWER_TERM_SEPARATOR.split(match.group("terms"))
    )
    if (
        not 2 <= len(terms) <= 8
        or any(not 1 <= len(term) <= 48 for term in terms)
        or len(set(term.casefold() for term in terms)) != len(terms)
    ):
        return ()
    return terms


_PROJECT_CONVENTION_REQUEST = re.compile(
    r"(?:按|根据|依照|依据)(?:当前)?项目(?:约定|规范|规则)|"
    r"\b(?:according\s+to|using)\s+(?:the\s+)?project\s+"
    r"(?:conventions?|rules?|specification)\b",
    re.IGNORECASE,
)


def requires_project_evidence(message: str) -> bool:
    """Return whether execution parameters explicitly depend on project rules."""
    return _PROJECT_CONVENTION_REQUEST.search(message) is not None


async def resolve_execution_case(
    settings: KernelSettings,
    client: ObservationClient,
    *,
    message: str,
    project_id: str,
    agent_run_id: str,
    trace_id: str,
    actor_subject: str | None = None,
) -> ResolvedExecutionCase | None:
    """Resolve an exact configured case; return None for the generic Agent path."""
    case = next(
        (
            item for item in settings.execution_cases
            if any(alias.casefold() in message.casefold() for alias in item.aliases)
        ),
        None,
    )
    if case is None:
        return None
    model = next(
        (
            item for item in settings.models
            if (item.model_id, item.model_version)
            == (case.model_id, case.model_version)
        ),
        None,
    )
    search = next(
        (item for item in settings.observation_tools if item.name == "discover_datasets"),
        None,
    )
    inspect = next(
        (item for item in settings.observation_tools if item.name == "inspect_data_version"),
        None,
    )
    services = settings.services_by_name
    if model is None or search is None or inspect is None:
        return None
    search_service = services.get(search.service)
    inspect_service = services.get(inspect.service)
    if search_service is None or inspect_service is None:
        return None
    try:
        result = await client.invoke(
            search,
            search_service,
            call_id=f"case-search:{case.case_id}",
            project_id=project_id,
            trace_id=trace_id,
            arguments={"query": "", "tags": [case.dataset_tag], "limit": 100},
            actor_subject=actor_subject,
        )
        candidates = _select_batch(case, result["data"])
        if candidates is None:
            return None
        details = await asyncio.gather(*(
            client.invoke(
                inspect,
                inspect_service,
                call_id=f"case-inspect:{case.case_id}:{role.role}",
                project_id=project_id,
                trace_id=trace_id,
                arguments={"version_id": candidates[role.role]["currentVersion"]},
                actor_subject=actor_subject,
            )
            for role in case.input_roles
        ))
        references: dict[str, DataVersionRefV2] = {}
        arguments = dict(model.default_parameters)
        for role, response in zip(case.input_roles, details, strict=True):
            reference = _exact_reference(candidates[role.role], response["data"])
            if reference is None:
                return None
            references[role.parameter] = reference
            arguments[role.parameter] = reference.version_id
        profile = _selected_profile(case, message)
        arguments.update(case.profiles[profile])
        validate(arguments, model.input_schema)
    except (AgentRunError, UpstreamServiceError):
        # This is an acceleration path. Any incomplete or unavailable case falls
        # back to the ordinary Agent, which retains its normal diagnostics.
        return None
    canonical = json.dumps(
        arguments, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    )
    fingerprint = hashlib.sha256(
        f"{agent_run_id}:{model.tool_name}:{canonical}".encode()
    ).hexdigest()
    action = PreparedAction(
        tool_call_id=f"case:{case.case_id}",
        position=0,
        tool=model.tool_name,
        model_id=model.model_id,
        model_version=model.model_version,
        model_checksum=model.model_checksum,
        workflow_id=model.workflow_id,
        workflow_version=model.workflow_version,
        workflow_checksum=model.workflow_checksum,
        plan_revision=1,
        arguments_json=canonical,
        input_references_json=json.dumps(
            {
                name: reference.as_storage_dict()
                for name, reference in references.items()
            },
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ),
        schema_checksum=model.input_schema_checksum,
        fingerprint=fingerprint,
    )
    return ResolvedExecutionCase(
        case=case,
        model=model,
        profile=profile,
        action=action,
        goal=f"执行{case.aliases[0]}并发布可核验成果",
        reason=(
            f"Kernel 已按案例 {case.case_id} 核验 {len(references)} 项同批输入；"
            "输入身份与 checksum 均匹配。具体参数见本次确认卡，等待用户确认。"
        ),
    )


def _selected_profile(case: ExecutionCaseDefinition, message: str) -> str:
    lowered = message.casefold()
    if "development_fast" in lowered or "快速档" in message or "快速模式" in message:
        if "development_fast" in case.profiles:
            return "development_fast"
    return case.default_profile


def _select_batch(
    case: ExecutionCaseDefinition, data: object,
) -> dict[str, dict[str, object]] | None:
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None
    required = {item.role for item in case.input_roles}
    batches: dict[str, dict[str, dict[str, object]]] = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("tags"), list):
            continue
        tags = item["tags"]
        batch_tags = [tag for tag in tags if isinstance(tag, str) and tag.startswith("case-batch:")]
        role_tags = [tag for tag in tags if isinstance(tag, str) and tag.startswith("input:")]
        if len(batch_tags) != 1 or len(role_tags) != 1:
            continue
        role = role_tags[0][len("input:"):]
        if role not in required or not isinstance(item.get("currentVersion"), str):
            continue
        batch = batches.setdefault(batch_tags[0], {})
        if role in batch:
            batch[role] = {}
        else:
            batch[role] = item
    valid = [batch for batch in batches.values() if set(batch) == required and all(batch.values())]
    return valid[0] if len(valid) == 1 else None


def _exact_reference(
    candidate: dict[str, object], detail: object,
) -> DataVersionRefV2 | None:
    if not isinstance(detail, dict) or detail.get("status") != "OK":
        return None
    if (
        detail.get("versionId") != candidate.get("currentVersion")
        or detail.get("datasetId") != candidate.get("datasetId")
        or not isinstance(detail.get("semanticId"), str)
        or not isinstance(detail.get("objects"), list)
    ):
        return None
    raw = [item for item in detail["objects"] if isinstance(item, dict) and item.get("role") == "raw"]
    if len(raw) != 1:
        return None
    item = raw[0]
    try:
        return DataVersionRefV2.from_dict({
            "kind": "data_version", "service": "data-primary",
            "datasetId": detail["datasetId"], "versionId": detail["versionId"],
            "semanticId": detail["semanticId"], "objectId": item["objectId"],
            "mediaType": item["mediaType"], "format": item["format"],
            "sizeBytes": item["size"], "checksum": item["checksum"],
        })
    except (KeyError, TypeError, ValueError):
        return None
