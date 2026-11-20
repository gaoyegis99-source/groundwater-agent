import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest
from agents.testing import ScriptedModel

from platform_kernel.agent_runtime.mode import resolve_mode_capabilities
from platform_kernel.agent_runtime.persistence import load_request_context
from platform_kernel.agent_runtime.result_processing import prepare_interruption
from platform_kernel.business_capabilities import (
    continue_business_capability,
    parse_business_capabilities,
    require_installed_business_capability,
    select_business_capability,
)
from platform_kernel.contracts import DataVersionRefV2
from platform_kernel.composition import PlatformApplication
from platform_kernel.errors import AgentRunError
from platform_kernel.interaction.persistence import pending_agent_start
from platform_kernel.intent_routing.models import IntentDecisionV4
from platform_kernel.workflow_selection import bind_workflow_selection


def _ref(name, digest="a"):
    return {"id": name, "version": "1.0.0", "checksum": f"sha256:{digest * 64}"}


@pytest.fixture
def registry(kernel_settings):
    settings = kernel_settings()
    model = replace(
        settings.models[0], model_id="test.model", workflow_id="test.workflow",
        model_version="1.0.0", workflow_version="1.0.0",
        model_checksum=_ref("x")["checksum"], workflow_checksum=_ref("x", "b")["checksum"],
        data_input_parameters=("versionId",),
        input_schema={"type": "object", "properties": {
            "versionId": {"type": "string"}, "eventThreshold": {"type": "number"},
            "eventComparison": {"type": "string"}, "enabled": {"type": "boolean"},
        }},
        default_parameters={"enabled": False},
    )
    return (model,), [{
        "ref": _ref("test.capability", "c"), "title": "监测趋势", "description": "联系监测对象。",
        "scenario": "监测趋势", "tier": "CORE", "defaultVisible": True, "roles": ["admin", "researcher"],
        "requiredServices": ["data", model.execution_service], "validationStatus": "TECHNICALLY_VALIDATED",
        "matchTerms": [["监测趋势", "水位趋势"]], "excludeTerms": ["自定义工作流", "不做阈值"],
        "profiles": [{
            "profileRef": _ref("test.profile", "d"), "modelRef": _ref("test.model"),
            "workflowRef": _ref("test.workflow", "b"), "resultContractRef": _ref("test.results", "e"),
            "roles": ["admin", "researcher"], "limitations": ["方法有适用范围。"],
            "matchTerms": [["季节趋势"], ["阈值事件"]],
            "inputRoles": {"observations": {
                "modelParameter": "versionId",
                "formatPairs": [{"format": "parquet", "mediaType": "application/vnd.apache.parquet"}],
            }},
            "fixedParameters": {"enabled": True}, "modelDefaults": {},
            "requiredParameters": [{"name": "eventThreshold", "label": "阈值"}],
            "thresholdParameter": "eventThreshold", "metadataParameters": {}, "requiredChecks": ["FIELD_UNIT"],
            "nonOverlappingTimeRanges": [],
            "textParameters": [
                {
                    "name": "eventThreshold", "label": "阈值", "type": "number",
                    "patterns": [{"pattern": r"高于\s*(?P<value>[0-9]+)\s*(?P<unit>m)?"}],
                },
                {
                    "name": "eventComparison", "label": "比较", "type": "string",
                    "patterns": [{"pattern": r"高于\s*[0-9]+", "value": "above"}],
                },
            ],
        }],
    }]


def _input(**values):
    return {
        "kind": "data_version", "service": "data-primary", "dataset_id": "dataset",
        "version_id": "version", "semantic_id": "semantic", "object_id": "object",
        "media_type": "application/vnd.apache.parquet", "format": "parquet",
        "size_bytes": 100, "checksum": "sha256:" + "0" * 64, **values,
    }


def _attachment(**values):
    return DataVersionRefV2.from_storage_dict(_input(**values)).as_dict()


GOAL = "请分析监测趋势，检查季节趋势和阈值事件。"


def test_business_selection_preserves_source_role_and_exact_attachments(registry):
    models, bundle = registry
    configuration = parse_business_capabilities(bundle, models, {"data", models[0].execution_service})
    selection = select_business_capability(configuration, GOAL + "高于 10 m。", frozenset({"researcher"}), [_attachment()])
    assert selection["parameters"] == {"eventComparison": "above", "eventThreshold": 10.0, "enabled": True}
    assert selection["input_bindings"] == {"versionId": 0}
    saved = bind_workflow_selection(selection, models[0], (_attachment(),))
    assert saved["inputs"] == {"versionId": _input()}
    business = saved["business_capability"]
    assert business["thresholdUnit"] == "m"
    assert business["argumentSources"] == {
        "eventThreshold": "USER", "eventComparison": "USER", "enabled": "WORKFLOW_PROFILE",
    }
    assert business["profileRef"] == _ref("test.profile", "d")
    require_installed_business_capability(configuration, json.loads(json.dumps(saved)))
    for policy in ("EXECUTION_PROPOSAL", "READ_ONLY"):
        capabilities = resolve_mode_capabilities({
            "mode": "DIALOGUE", "dialogue_policy": policy, "workflow_selection": saved,
        }, models, composition_available=True, catalog_available=True)
        assert capabilities.models == models
        assert not capabilities.allow_operator_catalog
        assert capabilities.allow_plan is (policy == "EXECUTION_PROPOSAL")


@pytest.mark.parametrize("goal,role", [
    ("请分析水位趋势。", "researcher"),
    (GOAL + "要自定义工作流。", "researcher"),
    (GOAL + "本次不做阈值。", "researcher"),
    (GOAL, "viewer"),
])
def test_business_eligibility_does_not_grant_a_different_scope(registry, goal, role):
    _, configuration = registry
    assert select_business_capability(tuple(configuration), goal, frozenset({role}), [_attachment()]) is None


def test_incomplete_business_match_keeps_pins_and_omits_untrusted_values(registry):
    _, configuration = registry
    selection = select_business_capability(
        tuple(configuration), GOAL + "高于 10 m 或高于 11 m。", frozenset({"admin"}),
        [_attachment(), _attachment(version_id="other", object_id="other")],
    )
    assert selection["workflow_checksum"] == _ref("test.workflow", "b")["checksum"]
    assert selection["input_bindings"] == {}
    assert "eventThreshold" not in selection["parameters"]
    assert selection["business_capability"]["parameterConflicts"] == ["eventThreshold"]


def test_stale_profile_neither_falls_back_nor_changes_pending_selection(registry):
    models, bundle = registry
    selection = select_business_capability(tuple(bundle), GOAL, frozenset({"researcher"}), [])
    assert selection is not None
    candidate = deepcopy(bundle)
    candidate[0]["profiles"][0]["profileRef"] = _ref("test.profile", "f")
    with pytest.raises(ValueError, match="BUSINESS_CAPABILITY_SELECTION_STALE"):
        require_installed_business_capability(tuple(candidate), selection)
    candidate[0]["profiles"][0]["modelRef"] = _ref("test.model", "f")
    with pytest.raises(ValueError, match="KERNEL_BUSINESS_CAPABILITY_INVALID"):
        parse_business_capabilities(candidate, models, {"data", models[0].execution_service})


def test_follow_up_answers_only_the_current_blocked_preparation(registry):
    models, bundle = registry
    registry = tuple(bundle)
    roles = frozenset({"researcher"})
    initial = select_business_capability(registry, GOAL, roles, [_attachment()])
    prior = bind_workflow_selection(initial, models[0], (_attachment(),))
    missing = {"kind": "MISSING_PARAMETER", "parameter": "eventThreshold"}
    resumed, attachments = continue_business_capability(
        registry, prior, missing, "高于 10 m", roles, [],
    )
    assert attachments == [_attachment()]
    assert resumed["parameters"] == {"eventThreshold": 10.0, "eventComparison": "above", "enabled": True}
    assert resumed["business_capability"]["continuesPreparation"] is True
    assert resumed["business_capability"]["profileRef"] == prior["business_capability"]["profileRef"]
    assert continue_business_capability(registry, prior, missing, "继续", roles, []) is None
    assert continue_business_capability(registry, prior, missing, "高于 10 m", frozenset({"viewer"}), []) is None


def test_selected_business_model_cannot_skip_current_ready_preparation(registry):
    models, bundle = registry
    model, = models
    selection = select_business_capability(tuple(bundle), GOAL + "高于 10 m", frozenset({"researcher"}), [_attachment()])
    selection = bind_workflow_selection(selection, model, (_attachment(),))
    run = SimpleNamespace(plans=[SimpleNamespace(
        revision=1, steps=[SimpleNamespace(tool=model.tool_name)],
    )], actions=[])
    result = SimpleNamespace(
        interruptions=[SimpleNamespace(
            name=model.tool_name, call_id="model-call",
            arguments=json.dumps({**selection["parameters"], "versionId": "version"}),
        )],
        to_input_list=list,
    )
    with pytest.raises(AgentRunError, match="AGENT_MODEL_OUTPUT_INVALID") as error:
        prepare_interruption(run, result, {model.tool_name: model}, workflow_selection=selection)
    assert error.value.diagnostics["reason"] == "TASK_PREPARATION_REQUIRED"


@pytest.mark.parametrize("mode", ["DIALOGUE", "WORKFLOW_DRAFT", "NEEDS_CLARIFICATION", "COMPARE"])
def test_pending_business_selection_respects_final_intent_and_stale_installation(registry, kernel_settings, mode):
    models, bundle = registry
    settings = replace(kernel_settings(), models=models, business_capabilities=())
    app = PlatformApplication(settings=settings, model=ScriptedModel([]), intent_router_model=ScriptedModel([]))

    async def scenario():
        try:
            session = app.interaction.create_session().session
            receipt = app.interaction.message(
                session.session_id, "capability-test", GOAL, "AUTO",
                installed_models=models, business_capabilities=tuple(bundle),
                actor_roles=frozenset({"researcher"}), input_references=[_attachment()],
            )
            agent_run_id = receipt["agent_run_id"]
            assert app.interaction.claim_route(agent_run_id) is not None
            decision = (
                IntentDecisionV4.from_python(
                    response_kind="DIALOGUE", semantic_intent="COMPARE", task_action="NONE",
                    dialogue_policy="READ_ONLY", control_intent=None, clarification_reason=None,
                ) if mode == "COMPARE" else mode
            )
            app.interaction.resolve_route(
                agent_run_id, decision, "EXECUTION_PROPOSAL" if mode == "DIALOGUE" else None,
                provider="test", model="test", trace_id="route", provider_requests=0,
                input_tokens=0, output_tokens=0, total_tokens=0,
            )
            context = load_request_context(app.database, agent_run_id)
            if mode == "DIALOGUE":
                assert context["workflow_selection"]["business_capability"]["profileRef"] == _ref("test.profile", "d")
                await app.interaction.resume_pending_start(session.session_id)
                run = app.agent_runtime.get(agent_run_id, settings.project_id)
                assert run.status == "FAILED" and run.error == "BUSINESS_CAPABILITY_SELECTION_STALE"
                assert pending_agent_start(app.database, session.session_id) is None
            else:
                assert context["workflow_selection"] is None
                assert app.interaction.business_capability_refs(agent_run_id) is None
        finally:
            await app.close()

    asyncio.run(scenario())
