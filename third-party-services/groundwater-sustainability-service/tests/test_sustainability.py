from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.sustainability import SustainabilityInput, evaluate_sustainability


def make_input(**changes):
    values = {
        "context": {"water_source": "腰站堡水源地", "groundwater_type": "孔隙型", "evaluation_time": "2025年"},
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
    values.update(changes)
    return SustainabilityInput.model_validate(values)


@pytest.mark.parametrize(
    "ke,expected",
    [(0.8, 100), (0.800001, 85), (1.0, 85), (1.000001, 65), (1.3, 65), (1.300001, 40)],
)
def test_exploitation_coefficient_boundaries(ke, expected):
    result = evaluate_sustainability(make_input(annual_pumping_qa_m3_a=ke * 1000, available_qe_m3_a=1000))
    exploitation = result["primary_indicators"][0]["items"][3]
    assert exploitation["score"]["min"] == expected
    assert exploitation["score"]["max"] == expected


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({"well_yield_m3_d": 1000}, (60, 90)),
        ({"well_yield_m3_d": 1000.01}, (90, 100)),
        ({"recharge_r_m3_a": 900}, (75, 90)),
        ({"recharge_r_m3_a": 750}, (60, 75)),
        ({"supply_qs": 95}, (90, 100)),
        ({"supply_qs": 85}, (75, 90)),
        ({"class_iii_count_nd": 8}, (75, 90)),
        ({"pollutant_exceedance_count_nc": 1}, (90, 100)),
        ({"hydraulic_conductivity_m_d": 60}, (75, 90)),
        ({"hydraulic_conductivity_m_d": 30}, (60, 75)),
    ],
)
def test_indicator_score_ranges(changes, expected):
    result = evaluate_sustainability(make_input(**changes))
    group = result["primary_indicators"][0] if "well_yield_m3_d" in changes or "recharge_r_m3_a" in changes or "supply_qs" in changes else result["primary_indicators"][1] if "class_iii_count_nd" in changes or "pollutant_exceedance_count_nc" in changes else result["primary_indicators"][2]
    item = next(x for x in group["items"] if x["score"]["min"] == expected[0] and x["score"]["max"] == expected[1])
    assert item["score"]["min"] == expected[0]


def test_midpoint_scores_are_weighted_and_guide_ranges_remain_visible():
    result = evaluate_sustainability(make_input())
    assert result["weight_settings"]["mode"] == "guide_recommended"
    assert result["weight_settings"]["primary"] == {"resources": .35, "quality": .35, "recovery": .30}
    assert result["overall_score"]["min"] == pytest.approx(92.725)
    assert result["overall_score"]["max"] == pytest.approx(99.4)
    assert result["overall_score_selected"] == pytest.approx(96.0625)
    assert result["score_grade"] == "I"
    assert result["final_grade"] == "I"
    assert result["final_grade_label"] == "高可持续"
    assert result["primary_indicators"][0]["score"]["min"] == pytest.approx(92.5)
    assert result["primary_indicators"][0]["selected_score"] == pytest.approx(96.25)
    assert [x["weight"] for x in result["primary_indicators"][2]["items"]] == [.45,.20,.20,.15]
    for group in result["primary_indicators"]:
        for item in group["items"]:
            assert item["selected_score"] == pytest.approx((item["score"]["min"] + item["score"]["max"]) / 2)


def test_user_custom_weights_are_applied_to_both_hierarchy_levels():
    weights = {
        "primary": {"resources": .50, "quality": .20, "recovery": .30},
        "secondary": {
            "resources": {"richness": .50, "balance": .20, "supply": .20, "exploitation": .10},
            "quality": {"class3": .60, "pollutant": .40},
            "recovery": {"waterlevel": .45, "aquifer": .20, "vadose": .20, "conductivity": .15},
        },
    }
    result = evaluate_sustainability(make_input(weights=weights))
    assert result["weight_settings"]["mode"] == "custom"
    assert [group["weight"] for group in result["primary_indicators"]] == [.50, .20, .30]
    resources = result["primary_indicators"][0]
    assert [item["weight"] for item in resources["items"]] == [.50, .20, .20, .10]
    expected_resources = sum(item["selected_score"] * item["weight"] for item in resources["items"])
    assert resources["selected_score"] == pytest.approx(expected_resources)
    expected_overall = sum(group["selected_score"] * group["weight"] for group in result["primary_indicators"])
    assert result["overall_score_selected"] == pytest.approx(expected_overall)
    assert result["overall_score_selected"] != pytest.approx(96.0625)


def test_custom_weight_sums_must_equal_100_percent():
    weights = {
        "primary": {"resources": .50, "quality": .20, "recovery": .20},
        "secondary": {
            "resources": {"richness": .20, "balance": .30, "supply": .25, "exploitation": .25},
            "quality": {"class3": .60, "pollutant": .40},
            "recovery": {"waterlevel": .45, "aquifer": .20, "vadose": .20, "conductivity": .15},
        },
    }
    with pytest.raises(ValueError, match="一级权重合计必须为100%"):
        evaluate_sustainability(make_input(weights=weights))


def test_custom_weights_are_supported_by_http_api_and_invalid_totals_are_structured_errors():
    payload = make_input().model_dump(mode="json", exclude_none=True)
    payload["weights"] = {
        "primary": {"resources": .35, "quality": .35, "recovery": .30},
        "secondary": {
            "resources": {"richness": .20, "balance": .30, "supply": .25, "exploitation": .25},
            "quality": {"class3": .60, "pollutant": .40},
            "recovery": {"waterlevel": .45, "aquifer": .20, "vadose": .20, "conductivity": .15},
        },
    }
    with TestClient(
        app,
        headers={"Authorization": "Bearer development-only-key-change-me-000000000000"},
    ) as client:
        response = client.post("/api/v1/sustainability/evaluate", json=payload)
        payload["weights"]["primary"]["recovery"] = .20
        invalid = client.post("/api/v1/sustainability/evaluate", json=payload)
    assert response.status_code == 200
    assert response.json()["weight_settings"]["mode"] == "custom"
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "SUSTAINABILITY_INPUT_INVALID"


def test_http_api_omitting_weights_uses_guide_recommendations():
    payload = make_input().model_dump(mode="json", exclude_none=True)
    with TestClient(
        app,
        headers={"Authorization": "Bearer development-only-key-change-me-000000000000"},
    ) as client:
        response = client.post("/api/v1/sustainability/evaluate", json=payload)
    assert response.status_code == 200
    result = response.json()
    assert "weights" not in payload
    assert result["weight_settings"]["mode"] == "guide_recommended"
    assert [group["weight"] for group in result["primary_indicators"]] == [.35, .35, .30]


def test_midpoint_selection_produces_single_grade_when_range_crossed():
    result = evaluate_sustainability(make_input(well_yield_m3_d=50, recharge_r_m3_a=700, supply_qs=60, class_iii_count_nd=6, pollutant_exceedance_count_nc=4, initial_water_level_s0_m=10, residual_drawdown_sr_m=5, hydraulic_conductivity_m_d=8))
    assert result["overall_score"]["min"] < 60 < result["overall_score"]["max"]
    assert result["overall_score_selected"] == pytest.approx(45.875)
    assert result["score_grade"] == "IV"
    assert result["final_grade"] == "IV"
    assert "区间中点" in result["conclusion"]


def test_grade_iii_uses_basic_sustainability_label():
    result=evaluate_sustainability(make_input(well_yield_m3_d=50,class_iii_count_nd=0,residual_drawdown_sr_m=4))
    assert result["overall_score_selected"] == pytest.approx(72.4625)
    assert result["final_grade"] == "III"
    assert result["final_grade_label"] == "基本可持续"


def test_restriction_override_and_pending_review():
    assert evaluate_sustainability(make_input(prohibited_area="是"))["final_grade"] == "IV"
    pending = evaluate_sustainability(make_input(prohibited_area="待核查"))
    assert pending["final_grade"] is None
    assert "尚待核查" in pending["conclusion"]


def test_restriction_guidance_is_returned_with_evaluation():
    result = evaluate_sustainability(make_input(
        prohibited_area="待核查",
        extraction_cap_exceeded="否",
        severe_damage_or_unusable_quality="否",
    ))
    review = result["restriction_review"]
    assert len(review["items"]) == 3
    assert [item["status"] for item in review["items"]] == ["待核查", "否", "否"]
    assert "依法公布" in review["items"][0]["criterion"]
    assert "海（咸）水入侵" in review["items"][2]["criterion"]
    assert "单次监测异常" in review["items"][2]["not_triggered_by"]
    assert "主管部门认定或专项调查论证" in review["decision_rule"]


def test_restriction_guidance_endpoint_and_page_explain_review():
    with TestClient(
        app,
        headers={"Authorization": "Bearer development-only-key-change-me-000000000000"},
    ) as client:
        response = client.get("/api/v1/sustainability/restrictions")
        page = client.get("/sustainability")
    assert response.status_code == 200
    metadata = response.json()
    assert len(metadata["items"]) == 3
    assert "依法公布" in metadata["items"][0]["criterion"]
    assert "海（咸）水入侵" in metadata["items"][2]["criterion"]
    assert "限制性情形核查" in page.text
    assert "/api/v1/sustainability/restrictions" in page.text


@pytest.mark.parametrize(
    "changes",
    [
        {"total_water_points_nz": 0},
        {"class_iii_count_nd": 11},
        {"pollutant_exceedance_count_nc": 11},
        {"residual_drawdown_sr_m": 11},
        {"outflow_qout_m3_a": 0, "pumping_qp_m3_a": 0},
    ],
)
def test_invalid_inputs_do_not_calculate(changes):
    with pytest.raises((ValueError, Exception)):
        evaluate_sustainability(make_input(**changes))


def test_unknown_media_is_not_scored():
    result = evaluate_sustainability(make_input(aquifer_media="待核查/不匹配指南分类"))
    aquifer = result["primary_indicators"][2]["items"][1]
    assert aquifer["score"] is None
    assert result["final_grade"] is None


def test_endpoint_returns_structured_validation_error():
    values = make_input().model_dump(mode="json")
    values["outflow_qout_m3_a"] = 0
    values["pumping_qp_m3_a"] = 0
    with TestClient(
        app,
        headers={"Authorization": "Bearer development-only-key-change-me-000000000000"},
    ) as client:
        response = client.post("/api/v1/sustainability/evaluate", json=values)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "SUSTAINABILITY_INPUT_INVALID"


def test_endpoint_returns_midpoint_score_and_original_interval():
    values = make_input(synthetic_example=True).model_dump(mode="json")
    with TestClient(
        app,
        headers={"Authorization": "Bearer development-only-key-change-me-000000000000"},
    ) as client:
        response = client.post("/api/v1/sustainability/evaluate", json=values)
    assert response.status_code == 200
    result = response.json()
    assert result["overall_score_selected"] == pytest.approx(96.0625)
    assert result["overall_score"]["min"] == pytest.approx(92.725)
    assert result["overall_score"]["max"] == pytest.approx(99.4)
    assert result["synthetic_example"] is True


def test_page_is_served_in_chinese():
    with TestClient(
        app,
        headers={"Authorization": "Bearer development-only-key-change-me-000000000000"},
    ) as client:
        response = client.get("/sustainability")
    assert response.status_code == 200
    assert "裂隙型" in response.text and "岩溶型" in response.text
    assert "表20权重" in response.text
    assert "radarChart" in response.text
    assert "radar-card" in response.text
    assert "radar-legend" in response.text
    assert "#148575" in response.text
    assert "viewBox=\"0 0 520 320\"" in response.text
    assert "<h1>地下水水源地可持续利用评估</h1>" in response.text
    assert "textContent='地下水水源地可持续利用评估'" in response.text
    assert "function hasCustomWeights()" in response.text
    assert "if(customWeights)raw.weights=collectWeights()" in response.text
    assert "当前使用所选类型的指南推荐权重" in response.text
    assert response.text.count('class="restore-weight-button"') == 1
    assert "data-restore-scope=\"${scope}\"" in response.text
    assert "recommendedWeightGroups()[scope]" in response.text


def test_fracture_type_uses_its_own_indicators_and_weights():
    data = make_input(
        context={"water_source":"腰站堡水源地","groundwater_type":"裂隙型","evaluation_time":"2025年"},
        well_yield_m3_d=None,
        richness_measure="地下水径流模数", richness_value=3,
    )
    result = evaluate_sustainability(data)
    recovery = result["primary_indicators"][2]["items"]
    assert [x["weight"] for x in recovery] == [.55,.25,.12,.08]
    assert [x["name"] for x in recovery] == ["水位恢复率","含水层介质类型","包气带介质类型","含水层渗透系数"]
    assert recovery[0]["weight"] != .45


def test_karst_type_uses_karst_specific_indicators_and_table_20_weights():
    data = make_input(
        context={"water_source":"腰站堡水源地","groundwater_type":"岩溶型","evaluation_time":"2025年"},
        well_yield_m3_d=None,
        richness_measure="地下水径流模数", richness_value=6.1,
        aquifer_media=None, vadose_media=None, hydraulic_conductivity_m_d=None,
        karst_cover_class="cover_thin_rock_gravel",
        karst_surface_class="strong",
        karst_network_class="well_developed",
    )
    result = evaluate_sustainability(data)
    recovery = result["primary_indicators"][2]["items"]
    assert [x["name"] for x in recovery] == ["水位恢复率","覆盖层","表层岩溶带发育强度","岩溶网络发育程度"]
    assert [x["weight"] for x in recovery] == [.30,.245,.21,.245]
    assert all(x["score"] is not None for x in recovery)
    assert result["overall_score_selected"] is not None


def test_primary_indicator_names_match_requested_labels():
    result=evaluate_sustainability(make_input())
    assert [group["name"] for group in result["primary_indicators"]] == ["资源量","水质","补给恢复能力"]


@pytest.mark.parametrize(
    "groundwater_type,measure,value,expected",
    [
        ("裂隙型","地下水径流模数",3,(90,100)),
        ("裂隙型","地下水径流模数",1,(60,90)),
        ("裂隙型","泉水流量",1,(60,90)),
        ("岩溶型","地下水径流模数",6,(60,90)),
        ("岩溶型","泉（地下河）流量",100,(60,90)),
    ],
)
def test_type_specific_richness_boundaries(groundwater_type,measure,value,expected):
    data=make_input(context={"water_source":"青山","groundwater_type":groundwater_type,"evaluation_time":"2025"},well_yield_m3_d=None,richness_measure=measure,richness_value=value)
    item=evaluate_sustainability(data)["primary_indicators"][0]["items"][0]
    assert (item["score"]["min"],item["score"]["max"]) == expected


def test_type_specific_form_advertises_dynamic_indicators():
    with TestClient(
        app,
        headers={"Authorization": "Bearer development-only-key-change-me-000000000000"},
    ) as client:
        html=client.get("/sustainability").text
    assert 'name="groundwater_type"' in html
    assert "karst_cover_class" in html
    assert "地下水径流模数" in html
    assert "表20权重" in html
    assert "timeInput.type='date'" in html
    assert ".grade.grade-III" in html
    assert '<div class="section-title">资源量</div>' in html
    assert ".scorehead.grade-III .bigscore" in html
    assert "style=\"left:60%\">60 · III" in html
    assert "style=\"left:75%\">75 · II" in html
    assert "style=\"left:90%\">90 · I" in html
    assert ".score-track.overall.grade-III .score-band" in html
    assert "III级（中等）" in html
    assert "水质指标" in html
    assert "补给恢复能力指标" in html
    assert "无法连接本地评估服务" in html
    assert "权重设置（可调整）" in html
    assert 'name="weight_${scope}_${id}"' in html
    assert "collectWeights" in html
    assert "updateWeightTotals" in html
    assert "本次使用自定义权重" in html
