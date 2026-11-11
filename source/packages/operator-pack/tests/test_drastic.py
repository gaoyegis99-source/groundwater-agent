from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from moperators.operators import drastic

FACTORS = ("D", "R", "A", "S", "T", "I", "C")
WEIGHTS = {"D": 5, "R": 4, "A": 3, "S": 2, "T": 1, "I": 5, "C": 3}
TRANSFORM = from_origin(500_000, 4_100_000, 100, 100)


@dataclass(frozen=True)
class StagedInput:
    path: Path
    media_type: str
    format_id: str
    metadata: dict


def _source(source_id: str) -> dict[str, str]:
    return {
        "id": source_id,
        "title": f"Auditable source for {source_id}",
        "version": "2026-11-10",
        "locator": f"appendix:{source_id}",
        "verificationStatus": "VERIFIED",
    }


def _bound(
    minimum: float | None,
    maximum: float | None,
    *,
    minimum_inclusive: bool,
    maximum_inclusive: bool,
    **values: object,
) -> dict[str, object]:
    result: dict[str, object] = {
        "minimumInclusive": minimum_inclusive,
        "maximumInclusive": maximum_inclusive,
        **values,
    }
    if minimum is not None:
        result["minimum"] = minimum
    if maximum is not None:
        result["maximum"] = maximum
    return result


def _three_rating_rules() -> list[dict[str, object]]:
    return [
        _bound(
            0,
            3,
            minimum_inclusive=True,
            maximum_inclusive=True,
            ruleId="low",
            rating=1,
        ),
        _bound(
            3,
            6,
            minimum_inclusive=False,
            maximum_inclusive=True,
            ruleId="medium",
            rating=5,
        ),
        _bound(
            6,
            10,
            minimum_inclusive=False,
            maximum_inclusive=True,
            ruleId="high",
            rating=10,
        ),
    ]


def _config(
    *,
    ratings: dict[str, int] | None = None,
) -> dict[str, object]:
    factor_ratings = ratings or {}
    factor_rules = {
        factor: (
            [
                _bound(
                    -100,
                    100,
                    minimum_inclusive=True,
                    maximum_inclusive=True,
                    ruleId=f"{factor}-constant",
                    rating=factor_ratings[factor],
                )
            ]
            if factor in factor_ratings
            else _three_rating_rules()
        )
        for factor in FACTORS
    }
    return {
        "schemaVersion": "groundwater-drastic-config.v1",
        "configId": "drastic-auditable-demo",
        "title": "Auditable DRASTIC demonstration configuration",
        "version": "2026-11-10",
        "method": "weighted-sum",
        "weights": {
            "source": _source("weights"),
            "values": WEIGHTS,
        },
        "ratings": {
            factor: {
                "band": 1,
                "unit": "factor-specific source unit",
                "basis": f"DRASTIC {factor} input definition",
                "source": _source(f"rating-{factor}"),
                "rules": factor_rules[factor],
            }
            for factor in FACTORS
        },
        "classification": {
            "source": _source("index-classes"),
            "classes": [
                _bound(
                    0,
                    100,
                    minimum_inclusive=True,
                    maximum_inclusive=True,
                    code=1,
                    label="Lower relative vulnerability",
                    order=1,
                ),
                _bound(
                    100,
                    230,
                    minimum_inclusive=False,
                    maximum_inclusive=True,
                    code=2,
                    label="Higher relative vulnerability",
                    order=2,
                ),
            ],
        },
    }


def _write_raster(
    path: Path,
    values: np.ndarray,
    *,
    transform=TRANSFORM,
    crs: str = "EPSG:32650",
    nodata: float = -9999.0,
) -> StagedInput:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=values.shape[1],
        height=values.shape[0],
        count=1,
        dtype="float64",
        crs=crs,
        transform=transform,
        nodata=nodata,
    ) as dataset:
        dataset.write(np.asarray(values, dtype=np.float64), 1)
    return StagedInput(path, "image/tiff", "geotiff", {})


def _write_config(path: Path, config: dict[str, object]) -> StagedInput:
    path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return StagedInput(path, "application/json", "json", {})


def _run(
    tmp_path: Path,
    arrays: dict[str, np.ndarray],
    config: dict[str, object],
    *,
    transforms: dict[str, object] | None = None,
    name: str,
) -> dict[str, dict]:
    transforms = transforms or {}
    inputs = {
        factor: [
            _write_raster(
                tmp_path / f"{name}-{factor}.tif",
                arrays[factor],
                transform=transforms.get(factor, TRANSFORM),
            )
        ]
        for factor in FACTORS
    }
    inputs["config"] = [_write_config(tmp_path / f"{name}-config.json", config)]
    return drastic.run(inputs, {}, tmp_path / name)


def _artifact_path(output: dict) -> Path:
    assert output["kind"] == "artifact"
    return Path(output["path"])


def _value(output: dict) -> dict:
    assert output["kind"] == "value"
    assert isinstance(output["value"], dict)
    return output["value"]


def test_constant_ratings_match_manual_weighted_sum_and_classification(
    tmp_path: Path,
) -> None:
    ratings = {"D": 1, "R": 2, "A": 3, "S": 4, "T": 5, "I": 6, "C": 7}
    arrays = {factor: np.full((2, 3), index + 1.0) for index, factor in enumerate(FACTORS)}
    outputs = _run(tmp_path, arrays, _config(ratings=ratings), name="constant")

    expected = sum(WEIGHTS[factor] * ratings[factor] for factor in FACTORS)
    assert expected == 86
    with rasterio.open(_artifact_path(outputs["index"])) as dataset:
        np.testing.assert_array_equal(dataset.read(1), np.full((2, 3), expected))
        assert dataset.crs.to_epsg() == 32650
        assert dataset.transform == TRANSFORM
    with rasterio.open(_artifact_path(outputs["classification"])) as dataset:
        np.testing.assert_array_equal(dataset.read(1), np.ones((2, 3), dtype=np.int16))

    summary = _value(outputs["summary"])
    assert summary["schemaVersion"] == "groundwater-drastic-summary.v1"
    assert summary["method"] == "weighted-sum"
    assert summary["formula"] == "sum(weight[factor] * rating[factor])"
    assert summary["factorWeights"] == WEIGHTS
    assert summary["ruleVersions"] == {factor: "2026-11-10" for factor in FACTORS}
    assert summary["counts"] == {
        "totalCellCount": 6,
        "allInputsValidCellCount": 6,
        "inputNoDataCellCount": 0,
        "ratingUncoveredCellCount": 0,
        "indexValidCellCount": 6,
        "classificationUncoveredCellCount": 0,
        "classificationValidCellCount": 6,
        "outputNoDataCellCount": 0,
    }
    assert "relative" in summary["interpretation"].lower()
    assert "not a pollution probability" in summary["interpretation"].lower()


def test_random_rasters_match_independent_numpy_oracle(tmp_path: Path) -> None:
    generator = np.random.default_rng(20261110)
    arrays = {
        factor: generator.integers(0, 11, size=(5, 7)).astype(np.float64) for factor in FACTORS
    }

    outputs = _run(tmp_path, arrays, _config(), name="oracle")
    expected_ratings = {
        factor: np.where(
            values <= 3,
            1,
            np.where(values <= 6, 5, 10),
        )
        for factor, values in arrays.items()
    }
    expected_index = sum(WEIGHTS[factor] * expected_ratings[factor] for factor in FACTORS)
    expected_classification = np.where(expected_index <= 100, 1, 2)

    with rasterio.open(_artifact_path(outputs["index"])) as dataset:
        np.testing.assert_allclose(dataset.read(1), expected_index, rtol=0, atol=0)
    with rasterio.open(_artifact_path(outputs["classification"])) as dataset:
        np.testing.assert_array_equal(dataset.read(1), expected_classification)


def test_nodata_propagates_and_rule_gaps_remain_uncovered(tmp_path: Path) -> None:
    arrays = {factor: np.ones((2, 2), dtype=np.float64) for factor in FACTORS}
    arrays["D"][0, 0] = -9999.0
    arrays["R"][0, 1] = 50.0

    outputs = _run(tmp_path, arrays, _config(), name="nodata")
    with rasterio.open(_artifact_path(outputs["index"])) as dataset:
        values = dataset.read(1, masked=True)
        assert values.mask.tolist() == [[True, True], [False, False]]
    with rasterio.open(_artifact_path(outputs["classification"])) as dataset:
        values = dataset.read(1, masked=True)
        assert values.mask.tolist() == [[True, True], [False, False]]

    summary = _value(outputs["summary"])
    assert summary["counts"] == {
        "totalCellCount": 4,
        "allInputsValidCellCount": 3,
        "inputNoDataCellCount": 1,
        "ratingUncoveredCellCount": 1,
        "indexValidCellCount": 2,
        "classificationUncoveredCellCount": 0,
        "classificationValidCellCount": 2,
        "outputNoDataCellCount": 2,
    }
    assert summary["ratingUncoveredRatio"] == pytest.approx(1 / 3)
    assert summary["outputNoDataRatio"] == pytest.approx(0.5)
    uncovered = {item["factor"]: item["uncoveredCellCount"] for item in summary["factors"]}
    assert uncovered == {"D": 0, "R": 1, "A": 0, "S": 0, "T": 0, "I": 0, "C": 0}
    assert summary["noDataPolicy"] == (
        "propagate any source NoData/non-finite value or unmatched factor rating"
    )


def test_grid_mismatch_is_rejected_without_implicit_alignment(tmp_path: Path) -> None:
    arrays = {factor: np.ones((2, 2), dtype=np.float64) for factor in FACTORS}
    shifted = from_origin(500_050, 4_100_000, 100, 100)

    with pytest.raises(ValueError, match=r"^GRID_MISMATCH$"):
        _run(
            tmp_path,
            arrays,
            _config(),
            transforms={"C": shifted},
            name="mismatch",
        )


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        ("unverified", "SOURCE_NOT_VERIFIED"),
        ("overlap", "CONFIG_INVALID"),
    ],
)
def test_invalid_or_unverified_config_fails_stably(
    tmp_path: Path,
    mutation: str,
    error: str,
) -> None:
    config = copy.deepcopy(_config())
    if mutation == "unverified":
        config["weights"]["source"]["verificationStatus"] = "UNVERIFIED"
    else:
        config["ratings"]["D"]["rules"].append(
            _bound(
                2,
                4,
                minimum_inclusive=True,
                maximum_inclusive=True,
                ruleId="overlap",
                rating=9,
            )
        )
    arrays = {factor: np.ones((1, 1), dtype=np.float64) for factor in FACTORS}

    with pytest.raises(ValueError, match=f"^{error}$"):
        _run(tmp_path, arrays, config, name=mutation)
