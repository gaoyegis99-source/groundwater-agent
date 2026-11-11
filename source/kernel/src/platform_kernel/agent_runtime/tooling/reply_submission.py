"""Terminal reply boundary and its bounded protocol repair policy."""
from __future__ import annotations

import json
from inspect import cleandoc
from pathlib import Path
import re

from agents import FunctionTool
from platform_workflow_compiler import compile_draft, WorkflowValidationError
from pydantic import ValidationError as PydanticValidationError

from ...errors import AgentRunError
from ..governance import governance
from ..mode import AgentModeCapabilities
from ..models import SubmittedReplyV6, reply_validation_diagnostics
from ..persistence import execution_reply_facts
from ..reply_schema import reply_schema
from ..protocol_repair import stage_protocol_retry
from .replies import (
    _clarification_reply_schema, _normalize_provider_reply_json,
    _validate_reply_evidence, validate_terminal_reply_context,
)


_SEQUENCE_PATTERNS = (
    re.compile(
        r"先\s*(?P<upstream>.+?)\s*(?:，|,|；|;)?\s*"
        r"(?:再|然后)\s*(?P<downstream>.+)",
        re.I | re.S,
    ),
    re.compile(
        r"\bfirst\b\s+(?P<upstream>.+?)\s*(?:,\s*)?"
        r"(?:and\s+)?\bthen\b\s+(?P<downstream>.+)",
        re.I | re.S,
    ),
    re.compile(
        r"(?P<upstream>[^,;，；]+?)\s*(?:->|→)\s*"
        r"(?P<downstream>[^,;，；]+)",
        re.I | re.S,
    ),
)
_SEQUENCE_STOP_WORDS = frozenset({
    "a", "an", "and", "analyses", "analysis", "analyze", "calculate",
    "data", "do", "first", "for", "next", "observations", "operation",
    "operations", "or", "perform", "run", "step", "steps", "the", "then",
    "to", "use", "using",
})


def _explicit_sequence_relation(message: str) -> tuple[str, str] | None:
    for pattern in _SEQUENCE_PATTERNS:
        match = pattern.search(message)
        if match is not None:
            return match.group("upstream"), match.group("downstream")
    return None


def _sequence_terms(value: str) -> set[str]:
    return {
        term
        for term in re.findall(r"[a-z0-9]+", value.casefold())
        if term not in _SEQUENCE_STOP_WORDS
    }


class ReplySubmission:
    """Bind reply validation to one run's database, request policy and provider."""

    def __init__(
        self,
        database: Path,
        request_context: dict | None,
        provider: str,
        *,
        operator_catalog: dict | None = None,
        operator_catalog_digest: str | None = None,
    ):
        self.database = database
        self.request_context = request_context
        self.provider = provider
        self.operator_catalog = operator_catalog
        self.operator_catalog_digest = operator_catalog_digest

    async def submit_reply(
        self, context, arguments_json: str,
    ) -> SubmittedReplyV6 | dict[str, object]:
        """Submit the final answer in this tool call. Put the direct answer in one
        concise conclusion and only necessary evidence or limitations in details.
        conclusion is an OBJECT {"text": "answer", "citations": []}, never a string.
        details and unresolved_items contain the same text/citations objects.
        Use null only for conclusion in clarification or pure execution-status replies.
        After any full Knowledge read in this Turn, cite at least one conclusion or
        detail. Other details and unresolved questions may be uncited. Supply only
        current unresolved questions and actual WorkflowRun IDs in this Session.
        Keep technical citation identities in citations fields, not in prose.
        Set include_execution_status to let Kernel summarize authoritative execution
        outcomes; the interface shows exact tasks and results. For a completed execution,
        details may contain up to three concise method, result-scope or limitation
        explanations, but must not replace or contradict the Kernel-owned status. Call
        alone after tool work; never execute here.
        """
        facts = self._execution_facts(context)
        execution_result = (
            facts["has_action"]
            or context is not None
            and governance(context.context)["finalization_reason"]
            == "EXECUTION_RESULT"
        )
        try:
            reply = SubmittedReplyV6.model_validate_json(
                _normalize_provider_reply_json(
                    arguments_json,
                    self.provider,
                    drafting=(self.request_context or {}).get("mode")
                    == "WORKFLOW_DRAFT",
                    execution_result=execution_result,
                )
            )
            reply = self._with_execution_facts(reply, context, facts)
            control = (
                {"knowledge_evidence": []}
                if context is None
                else governance(context.context)
            )
            reply = self._without_completed_evidence_options(reply, control)
            reply = self._without_untrusted_draft_reply_citations(
                reply, control,
            )
            json.dumps(
                reply.model_dump(), ensure_ascii=False, allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
            validate_terminal_reply_context(reply, self.request_context)
            _validate_reply_evidence(reply, control)
            # Preserve the renderer's established terminal errors for an
            # intrinsically contradictory reply shape. Draft preflight is only
            # meaningful once the reply can be rendered as either ordinary
            # prose or authoritative execution status.
            render_shape_valid = not (
                reply.include_execution_status
                and (
                    reply.conclusion is not None
                    or reply.unresolved_items
                    or reply.workflow_draft is not None
                )
                or not reply.include_execution_status
                and reply.conclusion is None
            )
            if render_shape_valid:
                preflight = self._preflight_workflow_draft(context, reply)
                if preflight is not None:
                    return preflight
            return reply
        except PydanticValidationError as error:
            diagnostics = reply_validation_diagnostics(error)
            instruction = "Call submit_reply once more with a value matching its schema."
            if any(
                issue.get("code") == "WORKFLOW_DRAFT_PARAMETER_SOURCES_INVALID"
                for issue in diagnostics.get("issues", [])
            ):
                instruction = (
                    "Call submit_reply once more. In workflow_draft, make "
                    "parameter_sources names unique and exactly equal to the "
                    "parameter_values keys; every parameter_values key must also "
                    "appear in graph.parameters. Preserve each known value and its "
                    "actual source, and do not invent a value for a missing input."
                )
            return self._retry_or_raise(
                context, diagnostics, instruction, error,
            )
        except (TypeError, ValueError, UnicodeEncodeError) as error:
            reason = str(error)
            diagnostics = {"reason": (
                reason
                if reason in {
                    "AGENT_WORKFLOW_DRAFT_INVALID",
                    "CLARIFICATION_REPLY_INVALID",
                    "EXECUTION_PROPOSAL_NOT_CREATED",
                    "INTERACTION_OPTIONS_WITHOUT_UNRESOLVED_ITEM",
                    "REPLY_CONCLUSION_TOO_LONG",
                    "REPLY_COMPARISON_INCOMPLETE",
                    "REPLY_COMPARISON_META_LANGUAGE",
                    "REPLY_REQUESTED_TERMS_INCOMPLETE",
                    "REPLY_CITATION_INVALID", "REPLY_CITATION_REQUIRED",
                    "REPLY_TECHNICAL_IDENTIFIER_VISIBLE",
                }
                else "REPLY_VALUE_INVALID"
            )}
            return self._retry_or_raise(
                context, diagnostics,
                "Call submit_reply once more with valid JSON values. Preserve the "
                "direct answer, explicitly repeat every answer item or business "
                "method named by the user, and include a direct recommendation when methods are "
                "being compared. Do not add platform, catalogue, or capability-"
                "boundary wording.", error,
            )

    def _without_completed_evidence_options(
        self, reply: SubmittedReplyV6, control: dict,
    ) -> SubmittedReplyV6:
        """Keep a completed evidence answer free of optional action shortcuts.

        Once a trusted read has closed the evidence phase, a non-clarification
        answer is already actionable as prose. Provider-authored navigation is
        not part of the evidence and can accidentally imply execution, so the
        Kernel omits it without changing conclusions or unresolved facts.
        """
        if (
            control.get("finalization_reason") == "EVIDENCE"
            and reply.conclusion is not None
            and reply.interaction_options
        ):
            return reply.model_copy(update={"interaction_options": []})
        return reply

    def _execution_facts(self, context) -> dict[str, object]:
        if (
            context is None
            or (self.request_context or {}).get("dialogue_policy")
            != "EXECUTION_PROPOSAL"
        ):
            return {"has_action": False, "workflow_run_ids": []}
        return execution_reply_facts(
            self.database, context.context["agent_run_id"],
        )

    def _with_execution_facts(
        self, reply: SubmittedReplyV6, context, facts: dict[str, object],
    ) -> SubmittedReplyV6:
        if (self.request_context or {}).get("dialogue_policy") == "EXECUTION_PROPOSAL":
            has_action = (
                facts["has_action"]
                or governance(context.context)["finalization_reason"]
                == "EXECUTION_RESULT"
            )
            authoritative = {
                "include_execution_status": has_action,
                "referenced_workflow_runs": facts["workflow_run_ids"],
            }
            if has_action:
                authoritative.update({
                    "conclusion": None,
                    "unresolved_items": [],
                    "interaction_options": [],
                    "workflow_draft": None,
                })
            reply = reply.model_copy(update=authoritative)
        return reply

    def _without_untrusted_draft_reply_citations(
        self, reply: SubmittedReplyV6, control: dict,
    ) -> SubmittedReplyV6:
        """Remove DeepSeek draft prose citations when no trusted read exists.

        Retrieval and lookup results are candidates, not citable evidence.  Some
        DeepSeek draft responses place those candidate identities in the prose
        reply even after being told that the allowed set is empty.  Removing the
        unsupported presentation links is fail-closed: it never creates trusted
        evidence and deliberately leaves workflow_draft.knowledge_refs untouched
        for the stricter draft-evidence validator.
        """
        if (
            self.provider != "deepseek"
            or (self.request_context or {}).get("mode") != "WORKFLOW_DRAFT"
            or control["knowledge_evidence"]
        ):
            return reply

        def without_citations(segment):
            return (
                None
                if segment is None
                else segment.model_copy(update={"citations": []})
            )

        return reply.model_copy(update={
            "conclusion": without_citations(reply.conclusion),
            "details": [without_citations(item) for item in reply.details],
            "unresolved_items": [
                without_citations(item) for item in reply.unresolved_items
            ],
        })

    def _preflight_workflow_draft(
        self, context, reply: SubmittedReplyV6,
    ) -> dict[str, object] | None:
        """Give one bounded repair for a structurally valid but invalid graph.

        Finalization still performs the authoritative evidence check.  This
        preflight is deliberately limited to the pure compiler so an invalid
        port, format, parameter, or graph can be corrected before the Agent
        loop terminates, without executing or publishing anything.
        """
        draft = reply.workflow_draft
        if (
            draft is None
            or self.operator_catalog is None
            or self.operator_catalog_digest is None
        ):
            return None
        try:
            control = (
                {"knowledge_evidence": []}
                if context is None
                else governance(context.context)
            )
            for index, ref in enumerate(draft.knowledge_refs):
                if (
                    ref.version_id,
                    ref.unit_id,
                    ref.checksum,
                ) not in {
                    tuple(item) for item in control["knowledge_evidence"]
                }:
                    raise WorkflowValidationError(
                        "WORKFLOW_DRAFT_EVIDENCE_INVALID",
                        f"/knowledge_refs/{index}",
                    )
            compile_draft(
                draft.graph.model_dump(by_alias=True),
                self.operator_catalog,
                workflow_id="draft.preflight",
                version="0.1.0",
                operator_catalog_digest=self.operator_catalog_digest,
                parameter_values=draft.parameter_values,
                parameter_sources=[
                    source.model_dump() for source in draft.parameter_sources
                ],
                # Rehydrate observed bindings here so an incompatible workflow
                # input declaration is repairable before persistence. Fall back
                # only for old resumable runs that predate data_evidence. The
                # finalizer repeats the same authoritative rehydration.
                data_bindings=[
                    {
                        **binding.model_dump(exclude_none=True),
                        "data_ref": control.get("data_evidence", {}).get(
                            binding.data_ref.version_id,
                            binding.data_ref.model_dump(exclude_none=True),
                        ),
                    }
                    for binding in draft.data_bindings
                ],
            )
            sequence_issue = self._sequential_draft_issue(draft)
            if sequence_issue is not None:
                code, path = sequence_issue
                raise WorkflowValidationError(code, path)
        except WorkflowValidationError as error:
            issue = {"code": error.code, "path": error.path}
            # The compiler correctly rejects a disconnected downstream node as
            # unreachable before the higher-level sequence check can run.  For
            # an explicit "first ... then ..." request, preserve that safety
            # result but classify it with the actionable sequence contract so
            # the Provider repairs the edge and terminal output together.
            sequence_issue = self._sequential_draft_issue(draft)
            if error.code == "WORKFLOW_NODE_UNREACHABLE" and sequence_issue:
                code, path = sequence_issue
                issue = {"code": code, "path": path}
            if stage_protocol_retry(self.database, context):
                instruction = (
                    "Call submit_reply once more after correcting workflow_draft "
                    "to satisfy the installed operator contracts. Preserve every "
                    "requested operation; if no valid full graph exists, submit "
                    "workflow_draft=null and explain the limitation."
                )
                if (
                    issue["code"] == "WORKFLOW_PORT_INCOMPATIBLE"
                    and issue["path"].endswith("/workflowInput")
                ):
                    instruction = (
                        "Call submit_reply once more. The workflowInput name at the "
                        "reported path has no matching graph.inputs declaration. Add "
                        "that external input with the inspected target operator port's "
                        "cardinality, mediaTypes and formats, and keep the node binding. "
                        "If its Data is missing, omit only its data_bindings entry and "
                        "describe the gap in workflow_draft.unresolved_items. Preserve "
                        "every requested operation; if no valid full graph exists, "
                        "submit workflow_draft=null and explain the limitation."
                    )
                elif issue["code"] == "WORKFLOW_DRAFT_SEQUENCE_INVALID":
                    instruction = (
                        "Call submit_reply once more. The user requested a sequence: "
                        "connect each explicitly downstream operation to the requested "
                        "upstream nodeOutput instead of reusing the external "
                        "workflowInput. Expose every requested terminal operation as a "
                        "workflow output; fan-out and multiple terminal outputs are "
                        "valid. Preserve the requested operation order and all explicit "
                        "missing inputs."
                    )
                elif issue["code"] == "DRAFT_DATA_FORMAT_INCOMPATIBLE":
                    instruction = (
                        "Call submit_reply once more. For each data binding, copy the "
                        "inspected Data media type and format, then bind them to its graph input "
                        "declaration while also satisfying the inspected target "
                        "operator port. Bind the source Data only to the source input; "
                        "leave the separate missing rules/configuration input unbound."
                    )
                return {
                    "status": "RETRY_REQUIRED",
                    "reason": "AGENT_WORKFLOW_DRAFT_INVALID",
                    "issues": [issue],
                    "instruction": instruction,
                }
            raise AgentRunError(
                "AGENT_MODEL_OUTPUT_INVALID",
                diagnostics={
                    "kind": "WORKFLOW_VALIDATION",
                    **issue,
                },
            ) from error
        return None

    def _sequential_draft_issue(
        self, draft,
    ) -> tuple[str, str] | None:
        message = (self.request_context or {}).get("current_message")
        if not isinstance(message, str) or len(draft.graph.nodes) < 2:
            return None
        relation = _explicit_sequence_relation(message)
        if relation is None:
            return None

        node_ids = {node.node_id for node in draft.graph.nodes}
        downstream_by_node = {node_id: set() for node_id in node_ids}
        upstream_by_node = {node_id: set() for node_id in node_ids}
        for node in draft.graph.nodes:
            for binding in node.inputs.values():
                node_output = getattr(binding, "node_output", None)
                if node_output is None or node_output.node_id not in node_ids:
                    continue
                downstream_by_node[node_output.node_id].add(node.node_id)
                upstream_by_node[node.node_id].add(node_output.node_id)

        requested_upstream, requested_downstream = relation
        upstream_matches = self._matching_sequence_nodes(
            requested_upstream, draft.graph.nodes,
        )
        downstream_matches = self._matching_sequence_nodes(
            requested_downstream, draft.graph.nodes,
        )
        if upstream_matches and downstream_matches:
            for downstream_id in downstream_matches:
                ancestors = set()
                pending = list(upstream_by_node[downstream_id])
                while pending:
                    upstream_id = pending.pop()
                    if upstream_id in ancestors:
                        continue
                    ancestors.add(upstream_id)
                    pending.extend(upstream_by_node[upstream_id])
                if ancestors.isdisjoint(upstream_matches):
                    return "WORKFLOW_DRAFT_SEQUENCE_INVALID", "/graph/nodes"
        else:
            connected = set()
            pending = [next(iter(node_ids))]
            while pending:
                node_id = pending.pop()
                if node_id in connected:
                    continue
                connected.add(node_id)
                pending.extend(
                    downstream_by_node[node_id] | upstream_by_node[node_id]
                )
            if connected != node_ids:
                return "WORKFLOW_DRAFT_SEQUENCE_INVALID", "/graph/nodes"

        sinks = {
            node_id
            for node_id, downstream_ids in downstream_by_node.items()
            if not downstream_ids
        }
        if any(
            output.node_output.node_id not in sinks
            for output in draft.graph.outputs
        ):
            return "WORKFLOW_DRAFT_SEQUENCE_INVALID", "/graph/outputs"
        return None

    def _matching_sequence_nodes(self, text: str, nodes) -> set[str]:
        requested_terms = _sequence_terms(text)
        if not requested_terms:
            return set()
        scores = {}
        for node in nodes:
            aliases = [node.node_id, node.operator_id]
            if self.operator_catalog is not None:
                manifest = self.operator_catalog.get(
                    (node.operator_id, node.operator_version),
                )
                if manifest is not None:
                    aliases.extend([
                        manifest.get("title", ""),
                        *manifest.get("tags", []),
                    ])
            node_terms = set().union(*map(_sequence_terms, aliases))
            scores[node.node_id] = len(requested_terms & node_terms)
        best_score = max(scores.values(), default=0)
        if best_score == 0:
            return set()
        return {
            node_id for node_id, score in scores.items() if score == best_score
        }

    def _retry_or_raise(self, context, diagnostics: dict, instruction: str, error: Exception) -> dict:
        if stage_protocol_retry(self.database, context):
            repair = {
                "status": "RETRY_REQUIRED",
                "reason": diagnostics["reason"],
                "issues": diagnostics.get("issues", []),
                "instruction": instruction,
            }
            if diagnostics["reason"] in {
                "REPLY_CITATION_INVALID",
                "REPLY_CITATION_REQUIRED",
            }:
                evidence = governance(context.context)["knowledge_evidence"]
                repair["allowed_citations"] = [
                    {
                        "version_id": version_id,
                        "unit_id": unit_id,
                        "checksum": checksum,
                    }
                    for version_id, unit_id, checksum in evidence
                ]
                repair["instruction"] = (
                    "Call submit_reply once more. In conclusion/details citations, "
                    "use only exact objects listed in allowed_citations; copy all three "
                    "fields without alteration. Cite at least one allowed object when "
                    "the answer uses the read evidence. If allowed_citations is empty, "
                    "submit no citations and do not claim evidence support."
                )
            return repair
        raise AgentRunError(
            "AGENT_MODEL_OUTPUT_INVALID", diagnostics=diagnostics,
        ) from error

    def tool(self, capabilities: AgentModeCapabilities) -> FunctionTool:
        mode, provider = capabilities.mode, self.provider
        reply_description = cleandoc(self.submit_reply.__doc__)
        if mode == "WORKFLOW_DRAFT":
            reply_description += (
                " Before submitting a workflow draft, check every required graph "
                "input: bind each compatible inspected source, leave every explicitly "
                "missing rules/configuration artifact unbound, and describe that gap "
                "in workflow_draft.unresolved_items. Never bind a source Data version "
                "to a different rules/configuration input. "
                "when one source exists and one separate rules/configuration artifact "
                "is explicitly missing, data_bindings must contain the source binding "
                "and no rules binding, so only the rules input remains missing. "
                "Use only the documented workflow_draft fields; never add explanatory "
                "note fields beside parameter_values or other draft fields. "
                "For sequential requests, "
                "each graph output must reference a requested terminal operation, "
                "never an intermediate node; fan-out may have multiple terminal "
                "outputs. Workflow-draft replies never include "
                "interaction_options; the saved draft carries its own gaps. Keep "
                "workflow_draft.summary to one sentence within 120 characters."
            )
        if capabilities.clarification_only:
            reply_description = (
                "Ask exactly one concise natural-language clarification question. "
                "Supply conclusion=null, details=[], one unresolved item ending in ? or ？ with no "
                "citations, referenced_workflow_runs=[], include_execution_status=false, "
                "workflow_draft=null, and 2–3 mutually exclusive interaction_options."
            )
        return FunctionTool(
            name="submit_reply", description=reply_description,
            params_json_schema={**(
                _clarification_reply_schema()
                if capabilities.clarification_only
                else reply_schema(drafting=mode == "WORKFLOW_DRAFT")
            ), "title": "submit_reply_args"},
            on_invoke_tool=self.submit_reply,
            _use_default_failure_error_function=False,
            strict_json_schema=(
                capabilities.clarification_only
                or provider != "deepseek" and not capabilities.read_only
            ),
        )
