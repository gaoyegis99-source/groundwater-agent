"""Safe user-visible projections of SDK and tool activity."""

from __future__ import annotations

from collections.abc import Callable
import json
from typing import Any
from uuid import uuid4


_ACTIVITY_TEMPLATES: dict[tuple[str, str], tuple[str, str]] = {
    ("SEGMENT", "INITIAL"): (
        "处理当前请求",
        "正在装载当前会话、任务状态和可用能力。",
    ),
    ("SEGMENT", "RESUME_EXECUTION"): (
        "整理执行结果",
        "正在读取已确认任务的执行状态和发布结果。",
    ),
    ("SEGMENT", "RESUME_REJECTION"): (
        "调整处理方案",
        "正在依据拒绝原因重新检查目标和方案。",
    ),
    ("MODEL_REQUEST", "PROCESSING"): (
        "生成下一步处理决策",
        "Agent 正在基于当前上下文选择下一项明确动作。",
    ),
    ("MODEL_REQUEST", "COMPACTION"): (
        "整理会话上下文",
        "正在压缩较早对话，同时保留目标、约束、决定和未决事项。",
    ),
    ("DIAGNOSTIC", "TOKEN_SOFT_TARGET"): (
        "上下文用量提醒",
        "上下文已超过软目标，处理仍将继续。",
    ),
}

def _minimax_final_reply_json(text: str, control: dict[str, Any]) -> str | None:
    """Build a terminal reply using MiniMax prose and Kernel-read evidence."""
    text = text.strip()
    while text.startswith("<think>"):
        end = text.find("</think>")
        if end < 0:
            return None
        text = text[end + len("</think>"):].strip()
    if not text:
        return None
    # MiniMax sometimes emits the advertised submit_reply arguments as a plain
    # assistant JSON object instead of a tool call.  Preserve that object so the
    # normal submit_reply schema and fact checks validate it; wrapping the JSON
    # as prose would expose protocol syntax in the user-facing answer.
    try:
        structured = json.loads(text)
    except json.JSONDecodeError:
        structured = None
    if isinstance(structured, dict):
        return json.dumps(
            structured, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )
    citations = [
        {"version_id": item[0], "unit_id": item[1], "checksum": item[2]}
        for item in control["knowledge_evidence"]
    ]
    return json.dumps({
        "conclusion": {"text": text, "citations": citations},
        "details": [], "unresolved_items": [],
        "referenced_workflow_runs": [], "include_execution_status": False,
        "interaction_options": [], "workflow_draft": None,
    }, ensure_ascii=False, separators=(",", ":"))


def _minimax_final_text_json(
    response: object, control: dict[str, Any],
) -> str | None:
    """Return validated terminal JSON for one MiniMax assistant text response."""
    output = getattr(response, "output", None)
    if not isinstance(output, list):
        return None
    texts = [
        part.text.strip()
        for item in output
        if getattr(item, "type", None) == "message"
        for part in getattr(item, "content", [])
        if getattr(part, "type", None) == "output_text"
        and isinstance(getattr(part, "text", None), str)
        and part.text.strip()
    ]
    if len(texts) != 1:
        return None
    return _minimax_final_reply_json(texts[0], control)


def _activity(segment: dict, kind: str, purpose: str, *, status: str = "RUNNING",
              tool_name: str | None = None, tool_call_id: str | None = None) -> dict:
    title, summary = _ACTIVITY_TEMPLATES.get(
        (kind, purpose), ("处理系统步骤", "正在执行已声明的系统步骤。")
    )
    return {
        "agent_run_id": segment["agent_run_id"], "goal_revision": segment["goal_revision"],
        "segment_id": segment["segment_id"], "activity_id": str(uuid4()),
        "kind": kind, "purpose": purpose, "status": status,
        "tool_name": tool_name, "tool_call_id": tool_call_id, "error_code": None,
        "title": title, "summary": summary,
    }


def _activity_status(record: Callable[[dict], None], activity: dict, status: str) -> None:
    record({**activity, "status": status})
