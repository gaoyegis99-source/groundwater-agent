from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DemoContext(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    water_source: str = Field(min_length=1, max_length=200)
    groundwater_type: Literal["孔隙型", "裂隙型", "岩溶型"] = "孔隙型"
    evaluation_time: str = Field(min_length=1, max_length=100)


class WeightConfiguration(BaseModel):
    """Optional user weights as fractions; each hierarchy level must sum to 1."""

    model_config = ConfigDict(extra="forbid")
    primary: dict[str, float] = Field(description="一级权重，键为 resources/quality/recovery，合计1")
    secondary: dict[str, dict[str, float]] = Field(description="各一级组内的二级权重，分别合计1")

    @field_validator("primary")
    @classmethod
    def finite_primary_weights(cls, value: dict[str, float]) -> dict[str, float]:
        if any(not math.isfinite(weight) or weight < 0 or weight > 1 for weight in value.values()):
            raise ValueError("权重必须是0到1之间的有限数")
        return value

    @field_validator("secondary")
    @classmethod
    def finite_secondary_weights(cls, value: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
        if any(
            not math.isfinite(weight) or weight < 0 or weight > 1
            for group in value.values()
            for weight in group.values()
        ):
            raise ValueError("权重必须是0到1之间的有限数")
        return value


class SustainabilityInput(BaseModel):
    """Guide-based source evaluation inputs. Values are observations, not invented scores."""
    model_config = ConfigDict(extra="forbid")
    context: DemoContext
    synthetic_example: bool = False
    weights: WeightConfiguration | None = Field(default=None, description="可选自定义权重；不传时使用指南推荐值；权重按0–1小数输入且各层分别合计1")
    well_yield_m3_d: float | None = Field(default=None, ge=0, description="孔隙型富水性：单井涌水量，m³/d")
    richness_measure: Literal["地下水径流模数", "泉水流量", "泉（地下河）流量"] | None = Field(default=None, description="裂隙型/岩溶型富水性评价指标")
    richness_value: float | None = Field(default=None, ge=0, description="裂隙型/岩溶型富水性评价指标数值")
    recharge_r_m3_a: float = Field(ge=0, description="补给量R，m³/a")
    outflow_qout_m3_a: float = Field(ge=0, description="流出量Qout，m³/a")
    pumping_qp_m3_a: float = Field(ge=0, description="开采量Qp，m³/a")
    supply_qs: float = Field(ge=0, description="供水量Qs")
    demand_qg: float = Field(gt=0, description="需水量Qg")
    annual_pumping_qa_m3_a: float = Field(ge=0, description="年均实际开采量Qa，m³/a")
    available_qe_m3_a: float = Field(gt=0, description="可开采量Qe，m³/a")
    class_iii_count_nd: int = Field(ge=0, description="Ⅲ类及以上水质点数Nd")
    total_water_points_nz: int = Field(gt=0, description="评价水质点总数Nz")
    pollutant_exceedance_count_nc: int = Field(ge=0, description="特征污染指标超标点数Nc")
    initial_water_level_s0_m: float = Field(gt=0, description="恢复试验初始水位降深s0，m")
    residual_drawdown_sr_m: float = Field(ge=0, description="恢复观测残余水位降深sr，m")
    recovery_hours: float = Field(default=24, gt=0, description="恢复观测时间间隔Δt，小时；指南默认24h")
    aquifer_media: str | None = Field(default=None, description="孔隙型/裂隙型含水层介质类型；指南表11")
    vadose_media: str | None = Field(default=None, description="孔隙型/裂隙型包气带介质类型；指南表12")
    hydraulic_conductivity_m_d: float | None = Field(default=None, ge=0, description="孔隙型/裂隙型含水层渗透系数K，m/d")
    karst_cover_class: str | None = Field(default=None, description="岩溶型保护性盖层类别；指南表14")
    karst_surface_class: str | None = Field(default=None, description="岩溶型表层岩溶带发育类别；指南表15")
    karst_network_class: str | None = Field(default=None, description="岩溶型岩溶网络发育类别；指南表16")
    prohibited_area: Literal["否", "是", "待核查"] = "待核查"
    extraction_cap_exceeded: Literal["否", "是", "待核查"] = "待核查"
    severe_damage_or_unusable_quality: Literal["否", "是", "待核查"] = "待核查"
    evidence_note: str = Field(default="", max_length=1000, description="限制性情形的核查说明")

    @field_validator("well_yield_m3_d", "richness_value", "recharge_r_m3_a", "outflow_qout_m3_a", "pumping_qp_m3_a", "supply_qs", "demand_qg", "annual_pumping_qa_m3_a", "available_qe_m3_a", "initial_water_level_s0_m", "residual_drawdown_sr_m", "recovery_hours", "hydraulic_conductivity_m_d")
    @classmethod
    def finite(cls, value: float) -> float:
        if value is not None and not math.isfinite(value):
            raise ValueError("必须为有限数")
        return value


class SustainabilityTaskInput(SustainabilityInput):
    """Full sustainability-evaluation payload with an optional idempotency key."""

    client_request_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="调用方幂等键；同一键和同一请求重复提交会返回原任务",
    )


class IndicatorScoreRange(BaseModel):
    model_config = ConfigDict(extra="allow")
    min: float
    max: float
    text: str
    midpoint: float | None = None


class SustainabilityIndicatorItem(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    name: str
    weight: float
    score: IndicatorScoreRange | None
    selected_score: float | None = None


class SustainabilityIndicatorGroup(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    name: str
    weight: float
    score: IndicatorScoreRange | None
    selected_score: float | None
    items: list[SustainabilityIndicatorItem]


class SustainabilityEvaluationResult(BaseModel):
    """Structured full comprehensive-evaluation result exposed by the API."""

    model_config = ConfigDict(extra="allow")
    schema_version: str
    context: DemoContext
    synthetic_example: bool
    overall_score: IndicatorScoreRange | None
    overall_score_selected: float | None
    score_grade: str | None
    possible_grades: list[str]
    grade_labels: list[str]
    final_grade: str | None
    final_grade_label: str | None
    conclusion: str
    primary_indicators: list[SustainabilityIndicatorGroup]
    restriction_review: dict[str, Any]
    management_recommendations: list[str]
    weight_settings: dict[str, Any]
    scope_notice: str
    calculated_at: str


def _range(lo: float, hi: float, label: str | None = None) -> dict:
    return {"min": round(lo, 4), "max": round(hi, 4), "text": label or f"{lo:g}–{hi:g}"}


def _band(value: float, bands: list[tuple[bool, float, bool, float, float, float, str]]) -> dict:
    for low_inc, low, high_inc, high, score_low, score_high, label in bands:
        if (value >= low if low_inc else value > low) and (value <= high if high_inc else value < high):
            return {**_range(score_low, score_high), "basis": label}
    raise ValueError(f"无匹配评分区间：{value}")


def _weighted(rows: list[dict], weight_key: str = "weight") -> dict:
    lo = sum(row["score"]["min"] * row[weight_key] for row in rows)
    hi = sum(row["score"]["max"] * row[weight_key] for row in rows)
    return _range(lo, hi)


def _grade_for_score(score: float) -> str:
    if score >= 90:
        return "I"
    if score >= 75:
        return "II"
    if score >= 60:
        return "III"
    return "IV"


def _resolve_weights(data: SustainabilityInput, recovery_ids: list[str]) -> tuple[dict, bool]:
    """Use type-specific guide weights unless the request explicitly supplies overrides."""
    source_type = data.context.groundwater_type
    recovery_defaults = {
        "孔隙型": {"waterlevel": .45, "aquifer": .20, "vadose": .20, "conductivity": .15},
        "裂隙型": {"waterlevel": .55, "aquifer": .25, "vadose": .12, "conductivity": .08},
        "岩溶型": {"waterlevel": .30, "karst_cover": .245, "karst_surface": .21, "karst_network": .245},
    }[source_type]
    recommended = {
        "primary": {"resources": .35, "quality": .35, "recovery": .30},
        "secondary": {
            "resources": {"richness": .20, "balance": .30, "supply": .25, "exploitation": .25},
            "quality": {"class3": .60, "pollutant": .40},
            "recovery": recovery_defaults,
        },
    }
    custom = data.weights is not None
    weights = data.weights.model_dump() if data.weights is not None else recommended
    if set(weights["primary"]) != set(recommended["primary"]):
        raise ValueError("一级自定义权重必须包含资源量、水质、补给恢复能力")
    if set(weights["secondary"]) != set(recommended["secondary"]):
        raise ValueError("二级自定义权重必须分别包含资源量、水质和补给恢复能力")
    if set(weights["secondary"]["resources"]) != set(recommended["secondary"]["resources"]):
        raise ValueError("资源量二级权重指标不完整或包含不适用指标")
    if set(weights["secondary"]["quality"]) != set(recommended["secondary"]["quality"]):
        raise ValueError("水质二级权重指标不完整或包含不适用指标")
    if set(weights["secondary"]["recovery"]) != set(recovery_ids):
        raise ValueError(f"{source_type}补给恢复能力二级权重必须与该类型适用指标一致")
    for label, weight_set in [
        ("一级", weights["primary"]),
        ("资源量二级", weights["secondary"]["resources"]),
        ("水质二级", weights["secondary"]["quality"]),
        ("补给恢复能力二级", weights["secondary"]["recovery"]),
    ]:
        if abs(sum(weight_set.values()) - 1) > 1e-6:
            raise ValueError(f"{label}权重合计必须为100%")
    return weights, custom


RESTRICTION_GUIDANCE = [
    {
        "id": "prohibited_area",
        "name": "依法公布的地下水禁止开采区内存在不属于法定允许情形的现有取水",
        "criterion": "位于依法公布的地下水禁止开采区，且现有取水行为不属于法定允许取水情形。",
        "not_triggered_by": "仅位于限制开采区不触发本项；还需核对现有取水是否属于法定允许情形。",
        "evidence_to_record": "禁止开采区公告/批复及空间边界、取水位置、取水许可或其他法定允许依据。",
    },
    {
        "id": "extraction_cap_exceeded",
        "name": "持续超过依法核定取水量/适用总量控制要求，或低于控制水位且现有调度无法满足要求",
        "criterion": "现有取水方案持续超过依法核定的取水量或适用的取水总量控制要求；或者地下水位低于适用控制水位，且经核查无法通过现有调度措施满足控制要求。",
        "not_triggered_by": "须核实持续超量或控制水位及现有调度能力；不能仅凭单次数据异常直接认定。",
        "evidence_to_record": "取水许可/总量控制指标、连续实际取水记录、适用控制水位与监测时段、调度措施及核查结果。",
    },
    {
        "id": "severe_damage_or_unusable_quality",
        "name": "开采造成严重损害，或水质问题使现有供水处理/保障不能满足用途且风险无法有效控制",
        "criterion": "开采已造成严重地面沉降、地裂缝、海（咸）水入侵或生态损害；或者经复核确认的水质问题已导致既有供水处理与保障措施不能满足供水用途要求，且现有措施无法有效控制风险。",
        "not_triggered_by": "单次监测异常或暂时性水位波动本身不直接触发；应核实开采因果关系、影响程度及现有措施效果。",
        "evidence_to_record": "专项调查/复核依据、证据时段、空间位置、影响对象、损害或水质问题与开采的关系、现有控制措施及其效果。",
    },
]

RESTRICTION_DECISION_RULE = "限制性情形判定独立于综合评分。经主管部门认定或专项调查论证，存在任一情形且评估时仍未消除，当前开发利用方案下最终等级判为Ⅳ级，并保留综合得分和触发依据。资料不足时应列为待核查；仅位于限制开采区、单次监测异常或暂时性水位波动不直接触发。"


def evaluate_sustainability(data: SustainabilityInput) -> dict:
    if data.class_iii_count_nd > data.total_water_points_nz:
        raise ValueError("Nd不能大于Nz")
    if data.pollutant_exceedance_count_nc > data.total_water_points_nz:
        raise ValueError("Nc不能大于Nz")
    if data.residual_drawdown_sr_m > data.initial_water_level_s0_m:
        raise ValueError("残余水位降深sr不能大于初始水位降深s0")
    if data.outflow_qout_m3_a + data.pumping_qp_m3_a <= 0:
        raise ValueError("水均衡指数分母Qout+Qp必须大于0，指南未定义分母为0时的评分")

    kb = data.recharge_r_m3_a / (data.outflow_qout_m3_a + data.pumping_qp_m3_a)
    ps = data.supply_qs / data.demand_qg * 100
    class3_rate = data.class_iii_count_nd / data.total_water_points_nz * 100
    pollutant_rate = data.pollutant_exceedance_count_nc / data.total_water_points_nz * 100
    ke = data.annual_pumping_qa_m3_a / data.available_qe_m3_a

    source_type = data.context.groundwater_type
    if source_type == "孔隙型":
        if data.well_yield_m3_d is None:
            raise ValueError("孔隙型富水性评价必须填写单井涌水量")
        richness = _band(data.well_yield_m3_d, [(False, 1000, False, math.inf, 90, 100, "单井涌水量>1000 m³/d"), (True, 100, True, 1000, 60, 90, "100≤单井涌水量≤1000 m³/d"), (True, 0, False, 100, 0, 60, "单井涌水量<100 m³/d")])
        richness_details = f"单井涌水量={data.well_yield_m3_d:g} m³/d"
    else:
        if data.richness_measure is None or data.richness_value is None:
            raise ValueError(f"{source_type}富水性评价必须选择评价指标并填写数值")
        value = data.richness_value
        if source_type == "裂隙型" and data.richness_measure == "地下水径流模数":
            richness = _band(value, [(True, 3, True, math.inf, 90, 100, "地下水径流模数≥3 L/(s·km²)"), (True, 1, False, 3, 60, 90, "1≤地下水径流模数<3 L/(s·km²)"), (True, 0, False, 1, 0, 60, "地下水径流模数<1 L/(s·km²)")])
            unit = "L/(s·km²)"
        elif source_type == "裂隙型" and data.richness_measure == "泉水流量":
            richness = _band(value, [(False, 1, True, math.inf, 90, 100, "泉水流量>1 L/s"), (True, .1, True, 1, 60, 90, "0.1≤泉水流量≤1 L/s"), (True, 0, False, .1, 0, 60, "泉水流量<0.1 L/s")])
            unit = "L/s"
        elif source_type == "岩溶型" and data.richness_measure == "地下水径流模数":
            richness = _band(value, [(False, 6, True, math.inf, 90, 100, "地下水径流模数>6 L/(s·km²)"), (True, 3, True, 6, 60, 90, "3≤地下水径流模数≤6 L/(s·km²)"), (True, 0, False, 3, 0, 60, "地下水径流模数<3 L/(s·km²)")])
            unit = "L/(s·km²)"
        elif source_type == "岩溶型" and data.richness_measure == "泉（地下河）流量":
            richness = _band(value, [(False, 100, True, math.inf, 90, 100, "泉（地下河）流量>100 L/s"), (True, 10, True, 100, 60, 90, "10≤泉（地下河）流量≤100 L/s"), (True, 0, False, 10, 0, 60, "泉（地下河）流量<10 L/s")])
            unit = "L/s"
        else:
            raise ValueError(f"{source_type}不适用所选富水性评价指标")
        richness_details = f"{data.richness_measure}={value:g} {unit}"
    resource = [
        {"id": "richness", "name": "富水性", "weight": .20, "score": richness, "details": richness_details},
        {"id": "balance", "name": "水均衡指数", "weight": .30, "score": _band(kb, [(True, 1, False, math.inf, 90, 100, "Kb≥1"), (True, .90, False, 1, 75, 90, "0.90≤Kb<1"), (True, .75, False, .90, 60, 75, "0.75≤Kb<0.90"), (True, 0, False, .75, 0, 60, "Kb<0.75")]), "details": f"Kb=R/(Qout+Qp)={data.recharge_r_m3_a:g}/({data.outflow_qout_m3_a:g}+{data.pumping_qp_m3_a:g})={kb:.4g}"},
        {"id": "supply", "name": "供水保证率", "weight": .25, "score": _band(ps, [(True, 95, True, math.inf, 90, 100, "Ps≥95%"), (True, 85, False, 95, 75, 90, "85%≤Ps<95%"), (True, 70, False, 85, 60, 75, "70%≤Ps<85%"), (True, 0, False, 70, 0, 60, "Ps<70%")]), "details": f"Ps=Qs/Qg×100%={data.supply_qs:g}/{data.demand_qg:g}×100%={ps:.2f}%"},
        {"id": "exploitation", "name": "地下水开采系数", "weight": .25, "score": _range(100,100) if ke<=.8 else _range(85,85) if ke<=1 else _range(65,65) if ke<=1.3 else _range(40,40), "basis": "0≤Ke≤0.80:100；0.80<Ke≤1.00:85；1.00<Ke≤1.30:65；Ke>1.30:40", "details": f"Ke=Qa/Qe={data.annual_pumping_qa_m3_a:g}/{data.available_qe_m3_a:g}={ke:.4g}"},
    ]
    quality = [
        {"id": "class3", "name": "Ⅲ类水达标率", "weight": .60, "score": _band(class3_rate, [(True,90,True,math.inf,90,100,"≥90%"),(True,80,False,90,75,90,"80%≤<90%"),(True,70,False,80,60,75,"70%≤<80%"),(True,0,False,70,0,60,"<70%")]), "details": f"R₃=Nd/Nz×100%={data.class_iii_count_nd}/{data.total_water_points_nz}×100%={class3_rate:.2f}%"},
        {"id": "pollutant", "name": "特征污染指标超标率", "weight": .40, "score": _band(pollutant_rate, [(True,0,True,0,100,100,"Pc=0%"),(False,0,True,10,90,100,"0<Pc≤10%"),(False,10,True,25,60,90,"10<Pc≤25%"),(False,25,False,math.inf,0,60,"Pc>25%")]), "details": f"Pc=Nc/Nz×100%={data.pollutant_exceedance_count_nc}/{data.total_water_points_nz}×100%={pollutant_rate:.2f}%"},
    ]
    recovery_pct = (data.initial_water_level_s0_m - data.residual_drawdown_sr_m)/data.initial_water_level_s0_m*100
    aquifer_bands = {"裂隙、岩溶发育岩体": (90,100), "中粗砂岩、砾岩": (75,90), "粉细砂岩": (60,75), "黏土、页岩": (0,60)}
    vadose_bands = {"砂砾石、裂隙岩体": (90,100), "砂土": (75,90), "轻壤土、粉土": (60,75), "黏土、重壤土": (0,60)}
    recovery = [{"id":"waterlevel", "name":"水位恢复率", "weight":0, "score":_range(recovery_pct,recovery_pct), "basis":"得分取Rh数值", "details":f"Rh=(s0−sr)/s0×100%={recovery_pct:.2f}%；观测间隔{data.recovery_hours:g}h"}]
    if source_type in {"孔隙型", "裂隙型"}:
        recovery.extend([
            {"id":"aquifer", "name":"含水层介质类型", "weight":0, "score":_range(*aquifer_bands[data.aquifer_media]) if data.aquifer_media in aquifer_bands else None, "basis":"指南表11介质分类", "details":data.aquifer_media or "待核查"},
            {"id":"vadose", "name":"包气带介质类型", "weight":0, "score":_range(*vadose_bands[data.vadose_media]) if data.vadose_media in vadose_bands else None, "basis":"指南表12介质分类", "details":data.vadose_media or "待核查"},
            {"id":"conductivity", "name":"含水层渗透系数", "weight":0, "score":_band(data.hydraulic_conductivity_m_d, [(False,60,False,math.inf,90,100,"K>60 m/d"),(False,30,True,60,75,90,"30<K≤60 m/d"),(False,12,True,30,60,75,"12<K≤30 m/d"),(True,0,True,12,0,60,"K≤12 m/d")]) if data.hydraulic_conductivity_m_d is not None else None, "basis":"指南表13评分", "details":f"{data.hydraulic_conductivity_m_d:g} m/d" if data.hydraulic_conductivity_m_d is not None else "待核查"},
        ])
    else:
        cover_bands = {"cover_thin_rock_gravel": (90,100), "cover_sand_residual_2_5": (75,90), "cover_loam_5_10": (60,75), "cover_clay_over_10": (0,60)}
        cover_labels = {"cover_thin_rock_gravel":"基岩、砾石，厚度<2m", "cover_sand_residual_2_5":"砂土/残积层，厚度2–5m", "cover_loam_5_10":"壤土盖层，厚度5–10m", "cover_clay_over_10":"黏土/壤土盖层，厚度>10m"}
        surface_bands = {"strong":(90,100), "moderate":(75,90), "light":(60,75), "not_observed":(0,60)}
        surface_labels = {"strong":"溶蚀间距≤0.5m且溶蚀深度≥1m", "moderate":"0.5m<溶蚀间距≤2m且0.5m≤溶蚀深度<1m", "light":"溶蚀间距>2m且溶蚀深度<0.5m", "not_observed":"观察不到表层岩溶溶蚀发育"}
        network_bands = {"well_developed":(90,100), "weakly_developed":(75,90), "moderately_developed":(60,75), "no_karst_network":(0,60)}
        network_labels = {"well_developed":"良好发育的岩溶网络：分米级至米级管道、连通性极好、很少阻塞", "weakly_developed":"微弱发育的岩溶网络：小型管道、连通性较差或被充填、分米级或更小空洞", "moderately_developed":"中等发育的岩溶网络：管道和裂隙连通性一般、局部充填或阻塞", "no_karst_network":"孔隙区出露泉水、无岩溶发育、仅存在裂隙含水层"}
        recovery.extend([
            {"id":"karst_cover", "name":"覆盖层", "weight":0, "score":_range(*cover_bands[data.karst_cover_class]) if data.karst_cover_class in cover_bands else None, "basis":"指南表14", "details":cover_labels.get(data.karst_cover_class, "待核查/未匹配指南分类")},
            {"id":"karst_surface", "name":"表层岩溶带发育强度", "weight":0, "score":_range(*surface_bands[data.karst_surface_class]) if data.karst_surface_class in surface_bands else None, "basis":"指南表15", "details":surface_labels.get(data.karst_surface_class, "待核查/未匹配指南分类")},
            {"id":"karst_network", "name":"岩溶网络发育程度", "weight":0, "score":_range(*network_bands[data.karst_network_class]) if data.karst_network_class in network_bands else None, "basis":"指南表16", "details":network_labels.get(data.karst_network_class, "待核查/未匹配指南分类")},
        ])
    weights, custom_weights = _resolve_weights(data, [item["id"] for item in recovery])
    for group_id, items in (("resources", resource), ("quality", quality), ("recovery", recovery)):
        for item in items:
            item["weight"] = weights["secondary"][group_id][item["id"]]
    unscored_indicators = [item["name"] for item in recovery if item["score"] is None]
    groups = [
        {"id":"resources", "name":"资源量", "weight":weights["primary"]["resources"], "items":resource},
        {"id":"quality", "name":"水质", "weight":weights["primary"]["quality"], "items":quality},
        {"id":"recovery", "name":"补给恢复能力", "weight":weights["primary"]["recovery"], "items":recovery},
    ]
    for group in groups:
        group["score"] = None if any(x["score"] is None for x in group["items"]) else _weighted(group["items"])
        group["selected_score"] = None if group["score"] is None else sum(
            ((item["score"]["min"] + item["score"]["max"]) / 2) * item["weight"]
            for item in group["items"]
        )
        for item in group["items"]:
            if item["score"] is not None:
                item["selected_score"] = (item["score"]["min"] + item["score"]["max"]) / 2
    score_range = None if unscored_indicators else _weighted(groups)
    selected_overall = None if unscored_indicators else sum(group["selected_score"] * group["weight"] for group in groups)
    restrictions = [data.prohibited_area, data.extraction_cap_exceeded, data.severe_damage_or_unusable_quality]
    override = "是" in restrictions
    pending = "待核查" in restrictions
    score_grade = _grade_for_score(selected_overall) if selected_overall is not None else None
    grades = [score_grade] if score_grade else []
    grade_names = {"I":"高可持续", "II":"较高可持续", "III":"基本可持续", "IV":"不可持续"}
    if override:
        final_grade, conclusion = "IV", "指南限制性情形已确认，按指南归为IV级；综合分数仍单独保留。"
    elif pending:
        final_grade, conclusion = None, "限制性情形尚待核查；分数分级仅供参考，不能形成最终等级。"
    elif unscored_indicators:
        final_grade, conclusion = None, "所选类型的适用指标仍有缺失或未匹配分类；综合分级待核实，不对该项赋分。"
    elif score_grade:
        final_grade, conclusion = score_grade, f"按各项得分区间中点计算，综合得分{selected_overall:.2f}分，判为{score_grade}级（{grade_names[score_grade]}）。"
    else:
        final_grade, conclusion = None, "有指标未能按指南匹配，综合分级待核实。"
    advice = {
        "IV": ["优先开展专项核查；按核查结论压减或限制开采。", "针对污染或水位/生态问题制定治理与应急备用水源措施。", "加密监测，并明确责任人、时限和跟踪指标。"],
        "III": ["控制新增开采，优先改善低分指标对应的问题。", "结合指南建议开展季度或年度跟踪评价。", "措施应落到具体问题、责任人、时限和跟踪指标。"],
        "II": ["保持总量和水位管控，优化局部薄弱指标的运行管理。", "按指南要求持续监测并开展年度复核。"],
        "I": ["维持水源地保护与常规巡查，按年度复核。", "管理建议为指南通用建议；本演示不据此推断具体风险成因。"],
        "pending": ["在限制性情形与介质分类核实前，不作最终可持续性结论。", "补齐取水许可/管控区、开采总量、水位控制及水质/生态核查证据。"],
    }
    selected_advice = advice["IV"] if override else advice["pending"] if pending or unscored_indicators else advice.get(final_grade, advice["pending"])
    for group in groups:
        for item in group["items"]:
            if item["score"] is not None:
                item["score"]["text"] = f"{item['score']['min']:g}–{item['score']['max']:g}分" if item["score"]["min"] != item["score"]["max"] else f"{item['score']['min']:g}分"
                item["score"]["midpoint"] = item["selected_score"]
    if score_range:
        score_range["text"] = f"{score_range['min']:.2f}–{score_range['max']:.2f}分"
    restriction_items = [
        {**guidance, "status": getattr(data, guidance["id"])}
        for guidance in RESTRICTION_GUIDANCE
    ]
    return {
        "schema_version":"1.1", "context":data.context.model_dump(), "synthetic_example":data.synthetic_example,
        "overall_score":score_range, "overall_score_selected":selected_overall,
        "score_grade":score_grade, "possible_grades":grades,
        "grade_labels":[grade_names[g] for g in grades], "final_grade":final_grade,
        "final_grade_label":grade_names.get(final_grade), "conclusion":conclusion,
        "primary_indicators":groups,
        "restriction_review":{"prohibited_area":data.prohibited_area,"extraction_cap_exceeded":data.extraction_cap_exceeded,"severe_damage_or_unusable_quality":data.severe_damage_or_unusable_quality,"items":restriction_items,"decision_rule":RESTRICTION_DECISION_RULE,"evidence_note":data.evidence_note,"override_applied":override,"pending":pending},
        "management_recommendations":selected_advice,
        "weight_settings":{"mode":"custom" if custom_weights else "guide_recommended","primary":weights["primary"],"secondary":weights["secondary"]},
        "scope_notice":"本演示按用户指定，对每项有区间的指标取区间中点参与加权；取中点不是指南明文规定。权重默认使用指南推荐值，也可按需自定义；一级权重和各一级组内二级权重均须合计100%。图中浅色范围保留指南得分区间，圆点表示采用的分值。三级仅展示测量数据/依据，不另计分。演示结果不替代正式评估、现场核验或审批。",
        "calculated_at":datetime.now(timezone.utc).isoformat(),
    }
