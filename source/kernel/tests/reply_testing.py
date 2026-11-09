"""Build explicit submit_reply tool calls for deterministic Agent tests."""
from agents.testing import function_call


def submit_reply(
    text="", *, citations=(), details=(), unresolved=(), refs=(), status=False,
    call_id="reply",
):
    return function_call("submit_reply", {
        "conclusion": {"text": text, "citations": list(citations)} if text else None,
        "details": [
            {"text": item, "citations": list(item_citations)}
            for item, item_citations in details
        ],
        "unresolved_items": [
            {"text": item, "citations": list(item_citations)}
            for item, item_citations in unresolved
        ],
        "referenced_workflow_runs": list(refs),
        "include_execution_status": status,
        "interaction_options": [], "workflow_draft": None,
    }, call_id=call_id)
