from __future__ import annotations

import json

import pytest


def sustainability_payload(**changes):
    payload = {
        "context": {
            "water_source": "腰站堡水源地",
            "groundwater_type": "孔隙型",
            "evaluation_time": "2025-01-01",
        },
        "synthetic_example": True,
        "well_yield_m3_d": 1200,
        "recharge_r_m3_a": 1100,
        "outflow_qout_m3_a": 400,
        "pumping_qp_m3_a": 600,
        "supply_qs": 100,
        "demand_qg": 100,
        "annual_pumping_qa_m3_a": 800000,
        "available_qe_m3_a": 1000000,
        "class_iii_count_nd": 10,
        "total_water_points_nz": 10,
        "pollutant_exceedance_count_nc": 0,
        "initial_water_level_s0_m": 10,
        "residual_drawdown_sr_m": 0,
        "recovery_hours": 24,
        "aquifer_media": "中粗砂岩、砾岩",
        "vadose_media": "砂砾石、裂隙岩体",
        "hydraulic_conductivity_m_d": 65,
        "prohibited_area": "否",
        "extraction_cap_exceeded": "否",
        "severe_damage_or_unusable_quality": "否",
    }
    payload.update(changes)
    return payload


def test_comprehensive_sustainability_task_lifecycle_and_json_download(client):
    accepted_response = client.post(
        "/api/v1/sustainability/tasks", json=sustainability_payload()
    )
    assert accepted_response.status_code == 202
    accepted = accepted_response.json()
    assert accepted["task_id"]
    assert accepted["status"] == "pending"
    assert accepted["status_url"].endswith(f"/sustainability/tasks/{accepted['task_id']}")

    status = client.get(accepted["status_url"])
    assert status.status_code == 200
    assert status.json()["status"] == "succeeded"
    assert status.json()["error"] is None

    result_response = client.get(accepted["result_url"])
    assert result_response.status_code == 200
    result = result_response.json()
    assert result["context"]["water_source"] == "腰站堡水源地"
    assert result["final_grade"] == "I"
    assert result["weight_settings"]["mode"] == "guide_recommended"
    assert len(result["primary_indicators"]) == 3
    assert sum(len(group["items"]) for group in result["primary_indicators"]) == 10

    download = client.get(accepted["download_url"])
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("application/json")
    assert json.loads(download.content)["final_grade"] == "I"


def test_comprehensive_sustainability_task_validation_errors_are_structured(client):
    missing = sustainability_payload()
    del missing["available_qe_m3_a"]
    response = client.post("/api/v1/sustainability/tasks", json=missing)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REQUEST_VALIDATION_ERROR"


def test_comprehensive_sustainability_semantic_failure_is_queryable(client):
    payload = sustainability_payload(
        weights={
            "primary": {"resources": .35, "quality": .35, "recovery": .20},
            "secondary": {
                "resources": {"richness": .20, "balance": .30, "supply": .25, "exploitation": .25},
                "quality": {"class3": .60, "pollutant": .40},
                "recovery": {"waterlevel": .45, "aquifer": .20, "vadose": .20, "conductivity": .15},
            },
        }
    )
    accepted = client.post("/api/v1/sustainability/tasks", json=payload).json()
    status = client.get(accepted["status_url"]).json()
    assert status["status"] == "failed"
    assert status["error"]["code"] == "SUSTAINABILITY_INPUT_INVALID"
    assert client.get(accepted["result_url"]).status_code == 409
    assert client.get(accepted["download_url"]).status_code == 409


def test_comprehensive_task_applies_valid_custom_weights(client):
    payload = sustainability_payload(
        weights={
            "primary": {"resources": .50, "quality": .20, "recovery": .30},
            "secondary": {
                "resources": {"richness": .50, "balance": .20, "supply": .20, "exploitation": .10},
                "quality": {"class3": .60, "pollutant": .40},
                "recovery": {"waterlevel": .45, "aquifer": .20, "vadose": .20, "conductivity": .15},
            },
        }
    )
    accepted = client.post("/api/v1/sustainability/tasks", json=payload).json()
    result = client.get(accepted["result_url"]).json()
    assert result["weight_settings"]["mode"] == "custom"
    assert result["primary_indicators"][0]["weight"] == .50


def test_comprehensive_sustainability_task_idempotency(client):
    payload = sustainability_payload(client_request_id="agent-request-001")
    first = client.post("/api/v1/sustainability/tasks", json=payload).json()
    second = client.post("/api/v1/sustainability/tasks", json=payload).json()
    assert second["task_id"] == first["task_id"]
    assert second["idempotent_replay"] is True

    payload["context"]["evaluation_time"] = "2026-01-01"
    conflict = client.post("/api/v1/sustainability/tasks", json=payload)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


@pytest.mark.parametrize(
    "groundwater_type,type_fields,expected_recovery_ids",
    [
        (
            "孔隙型",
            {"well_yield_m3_d": 1200},
            ["waterlevel", "aquifer", "vadose", "conductivity"],
        ),
        (
            "裂隙型",
            {"richness_measure": "地下水径流模数", "richness_value": 3.5},
            ["waterlevel", "aquifer", "vadose", "conductivity"],
        ),
        (
            "岩溶型",
            {
                "richness_measure": "地下水径流模数",
                "richness_value": 6.5,
                "karst_cover_class": "cover_thin_rock_gravel",
                "karst_surface_class": "strong",
                "karst_network_class": "well_developed",
            },
            ["waterlevel", "karst_cover", "karst_surface", "karst_network"],
        ),
    ],
)
def test_comprehensive_task_routes_calculate_each_groundwater_type(
    client, groundwater_type, type_fields, expected_recovery_ids
):
    payload = sustainability_payload(
        context={
            "water_source": "示例水源地",
            "groundwater_type": groundwater_type,
            "evaluation_time": "2025-01-01",
        }
    )
    payload.pop("well_yield_m3_d")
    payload.update(type_fields)
    accepted = client.post("/api/v1/sustainability/tasks", json=payload).json()
    result = client.get(accepted["result_url"]).json()
    recovery_ids = [item["id"] for item in result["primary_indicators"][2]["items"]]
    assert result["context"]["groundwater_type"] == groundwater_type
    assert recovery_ids == expected_recovery_ids


def test_pending_sustainability_task_reports_result_not_ready(client):
    from app.sustainability import SustainabilityTaskInput

    service = client.app.state.sustainability_task_service
    task, replay = service.create_task(
        SustainabilityTaskInput.model_validate(sustainability_payload())
    )
    assert replay is False
    status_url = f"/api/v1/sustainability/tasks/{task['task_id']}"
    result_url = f"{status_url}/result"
    assert client.get(status_url).json()["status"] == "pending"
    pending_result = client.get(result_url)
    assert pending_result.status_code == 409
    assert pending_result.json()["error"]["code"] == "RESULT_NOT_READY"
    service.run_task(task["task_id"])
    assert client.get(status_url).json()["status"] == "succeeded"


def test_unfinished_sustainability_task_is_marked_failed_after_restart(client):
    from app.sustainability import SustainabilityTaskInput

    service = client.app.state.sustainability_task_service
    task, _ = service.create_task(
        SustainabilityTaskInput.model_validate(sustainability_payload())
    )
    repository = client.app.state.sustainability_task_repository
    assert repository.recover_unfinished() == 1
    failed = client.get(f"/api/v1/sustainability/tasks/{task['task_id']}").json()
    assert failed["status"] == "failed"
    assert failed["error"]["code"] == "SERVICE_RESTARTED"


def test_comprehensive_task_routes_are_documented_in_openapi(client):
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    assert "/api/v1/sustainability/tasks" in paths
    assert "/api/v1/sustainability/tasks/{task_id}" in paths
    assert "/api/v1/sustainability/tasks/{task_id}/files/result.json" in paths
