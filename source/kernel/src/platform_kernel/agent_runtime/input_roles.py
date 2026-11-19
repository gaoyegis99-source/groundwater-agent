"""Verify exact user-authored port assignments against a proposed graph."""

import re

from platform_workflow_compiler import WorkflowValidationError


_PORT = r"[A-Za-z][A-Za-z0-9_.-]*"
_DIRECTIVE = re.compile(rf"(?m)^\s*输入角色[ \t]+(?P<role>{_PORT})\s*[=：:]\s*(?P<value>[^\r\n]+)")
_LABELLED = re.compile(rf"[（(](?P<role>{_PORT})[）)]\s*=\s*(?P<value>[^\n；;。]+)")
_QUOTED = re.compile(rf"(?<![\w.-])(?P<role>{_PORT})\s*=\s*(?P<value>《[^》]+》|「[^」]+」)")


def verify_input_roles(message, graph, bindings, observed_versions, descriptions):
    """Match only explicit port assignments and exact observed names or versions."""
    ports = {
        (node.node_id, port): (node.operator_id, binding)
        for node in graph.nodes
        for port, binding in node.inputs.items()
    }
    for match, strict in (
        *((match, True) for match in _DIRECTIVE.finditer(message)),
        *((match, False) for match in _LABELLED.finditer(message)),
        *((match, False) for match in _QUOTED.finditer(message)),
    ):
        role = match["role"]
        qualifier, _, port = role.rpartition(".")
        candidates = [
            (node_id, binding)
            for (node_id, name), (operator_id, binding) in ports.items()
            if name == port and (not qualifier or qualifier in {node_id, operator_id})
        ]
        if not candidates and not strict:
            continue
        if len(candidates) != 1:
            raise WorkflowValidationError("WORKFLOW_INPUT_ROLE_AMBIGUOUS", f"/input_roles/{role}")
        node_id, binding = candidates[0]
        path = f"/graph/nodes/{node_id}/inputs/{port}"
        value = match["value"].strip().removesuffix("。").strip()
        if (value.startswith("《") and value.endswith("》")) or (value.startswith("「") and value.endswith("」")):
            value = value[1:-1]
        if value in observed_versions:
            expected = [value]
        else:
            expected = [version for version, entry in descriptions.items() if entry.get("title") == value]
        if len(expected) != 1:
            raise WorkflowValidationError("WORKFLOW_INPUT_ROLE_VERSION_REQUIRED", path)
        bound = getattr(binding, "workflow_input", None)
        if bindings.get(bound) != expected[0]:
            raise WorkflowValidationError("WORKFLOW_INPUT_ROLE_MISMATCH", path)
