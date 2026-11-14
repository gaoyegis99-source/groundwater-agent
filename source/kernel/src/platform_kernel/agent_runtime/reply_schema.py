from __future__ import annotations

from .models import SubmittedReplyV6


def reply_schema(*, drafting: bool = False, collections: bool = False) -> dict:
    schema = SubmittedReplyV6.model_json_schema()
    if not drafting:
        schema["properties"]["workflow_draft"] = {"type": "null"}
        used = {"InteractionOptionV1", "ReplyCitationV2", "ReplySegmentV1"}
        schema["$defs"] = {
            name: definition
            for name, definition in schema["$defs"].items()
            if name in used
        }
    elif not collections:
        definitions = schema["$defs"]
        bindings = definitions["WorkflowNodeDraft"]["properties"]["inputs"]["additionalProperties"]["anyOf"]
        collection_ref = "#/$defs/WorkflowCollectionBindingDraft"
        bindings[:] = [item for item in bindings if item.get("$ref") != collection_ref]
        del definitions["WorkflowCollectionBindingDraft"]
    return schema
