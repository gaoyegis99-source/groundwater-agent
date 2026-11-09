"""Pure recognition of the short natural-language control vocabulary."""

from __future__ import annotations


CONTROL_PHRASES = {
    "批准": "APPROVE_CONFIRMATION",
    "同意": "APPROVE_CONFIRMATION",
    "确认执行": "APPROVE_CONFIRMATION",
    "批准执行": "APPROVE_CONFIRMATION",
    "拒绝": "REJECT_CONFIRMATION",
    "不同意": "REJECT_CONFIRMATION",
    "拒绝执行": "REJECT_CONFIRMATION",
    "停止": "STOP_ANALYSIS",
    "停下": "STOP_ANALYSIS",
    "停止分析": "STOP_ANALYSIS",
    "停止本次分析": "STOP_ANALYSIS",
    "取消": "CANCEL_EXECUTION",
    "取消执行": "CANCEL_EXECUTION",
    "取消计算": "CANCEL_EXECUTION",
}

STATUS_PHRASES = frozenset(
    {
        "状态",
        "查看状态",
        "当前状态",
        "查询状态",
        "进度怎么样",
        "进度怎么样？",
        "现在怎么样了",
        "现在怎么样了？",
    }
)


def rule_intent(message: str) -> str | None:
    """Match only a complete short command; substrings never trigger controls."""

    normalized = message.strip()
    return CONTROL_PHRASES.get(normalized) or (
        "STATUS_QUERY" if normalized in STATUS_PHRASES else None
    )
