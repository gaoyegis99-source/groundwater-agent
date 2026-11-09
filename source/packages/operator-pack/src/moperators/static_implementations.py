from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from moperators.operators import cartography, gru_attention, transfer_data, transfer_spatial
from moperators.operators.native import StagedInput, run_native_operator
from moperators.operators.qgis import run_qgis_operator

Runner = Callable[
    [str, dict[str, list[StagedInput]], dict[str, Any], Path],
    dict[str, dict[str, Any]],
]


def _native(operator_id: str, inputs, parameters, output_dir):
    return run_native_operator(operator_id, inputs, parameters, output_dir)


def _qgis(operator_id: str, inputs, parameters, output_dir):
    return run_qgis_operator(operator_id, inputs, parameters, output_dir)


def _remote_http(operator_id: str, inputs, parameters, output_dir):
    raise ValueError("REMOTE_HTTP_REQUIRES_DEDICATED_WORKER")


IMPLEMENTATIONS: dict[tuple[str, str], Runner] = {
    ("groundwater.porous.agri_invert", "1.0.0"): _remote_http,
    ("groundwater.porous.flow_transport_simulate", "1.0.0"): _remote_http,
    ("groundwater.porous.inversion_sample", "1.0.0"): _remote_http,
    ("groundwater.porous.inversion_train", "1.0.0"): _remote_http,
    ("groundwater.porous.map_prepare", "1.0.0"): _remote_http,
    ("groundwater.porous.model_compile", "1.0.0"): _remote_http,
    ("groundwater.porous.pest_calibrate", "1.0.0"): _remote_http,
    ("groundwater.porous.result_analyze", "1.0.0"): _remote_http,
    ("groundwater.porous.result_render", "1.0.0"): _remote_http,
    ("groundwater.porous.scenario_compare", "1.0.0"): _remote_http,
    ("groundwater.source_sustainability.evaluate", "1.0.0"): _remote_http,
    ("cartography.render-png", "1.0.0"): cartography.render_png,
    ("table.timeseries_unpivot", "1.0.0"): transfer_data.unpivot,
    ("timeseries.chronological_split", "1.0.0"): transfer_data.split,
    ("preprocessing.standard_scaler_fit", "1.0.0"): transfer_data.scaler_fit,
    ("preprocessing.standard_scaler_transform", "1.0.0"): transfer_data.scaler_transform,
    ("preprocessing.standard_scaler_inverse", "1.0.0"): transfer_data.scaler_inverse,
    ("timeseries.supervised_windows", "1.0.0"): transfer_data.windows,
    ("statistics.regression_metrics", "1.0.0"): transfer_data.regression_metrics,
    ("ml.gru_attention_pretrain", "1.0.0"): gru_attention.pretrain,
    ("ml.gru_attention_finetune", "1.0.0"): gru_attention.finetune,
    ("ml.gru_attention_predict", "1.0.0"): gru_attention.predict,
    ("vector.from_xy", "1.0.0"): transfer_spatial.from_xy,
    ("vector.tag_membership", "1.0.0"): transfer_spatial.tag_membership,
    ("vector.envelope", "1.0.0"): transfer_spatial.envelope,
    ("raster.window", "1.0.0"): transfer_spatial.raster_window,
    ("crs.reproject", "1.0.0"): _native,
    ("data.convert", "1.0.0"): _native,
    ("data.inspect", "1.0.0"): _native,
    ("document.chunk", "1.0.0"): _native,
    ("document.text_extract", "1.0.0"): _native,
    ("geometry.repair", "1.0.0"): _qgis,
    ("geometry.validate", "1.0.0"): _qgis,
    ("groundwater.darcy_velocity", "1.0.0"): _native,
    ("groundwater.drawdown.calculate", "1.0.0"): _native,
    ("groundwater.head.calculate", "1.0.0"): _native,
    ("groundwater.hydraulic_gradient", "1.0.0"): _native,
    ("hydrology.basins", "1.0.0"): _native,
    ("hydrology.fill_sinks", "1.0.0"): _qgis,
    ("hydrology.flow_accumulation", "1.0.0"): _native,
    ("hydrology.flow_direction", "1.0.0"): _native,
    ("hydrology.snap_pour_points", "1.0.0"): _native,
    ("hydrology.stream_extract", "1.0.0"): _native,
    ("hydrology.stream_order", "1.0.0"): _native,
    ("hydrology.watershed", "1.0.0"): _native,
    ("points.interpolate.idw", "1.0.0"): _native,
    ("raster.algebra", "1.0.0"): _native,
    ("raster.aspect", "1.0.0"): _native,
    ("raster.cell_statistics", "1.0.0"): _native,
    ("raster.clip", "1.0.0"): _native,
    ("raster.contour", "1.0.0"): _native,
    ("raster.fill_nodata", "1.0.0"): _native,
    ("raster.hillshade", "1.0.0"): _native,
    ("raster.mosaic", "1.0.0"): _native,
    ("raster.polygonize", "1.0.0"): _native,
    ("raster.proximity", "1.0.0"): _native,
    ("raster.rasterize", "1.0.0"): _native,
    ("raster.reclassify", "1.0.0"): _native,
    ("raster.reproject", "1.0.0"): _native,
    ("raster.resample", "1.0.0"): _native,
    ("raster.sample_points", "1.0.0"): _native,
    ("raster.slope", "1.0.0"): _native,
    ("raster.statistics", "1.0.0"): _native,
    ("raster.zonal_statistics", "1.0.0"): _native,
    ("spreadsheet.extract_table", "1.0.0"): _native,
    ("statistics.describe", "1.0.0"): _native,
    ("statistics.linear_trend", "1.0.0"): _native,
    ("statistics.mad_outliers", "1.0.0"): _native,
    ("statistics.mann_kendall", "1.0.0"): _native,
    ("statistics.pettitt_change_point", "1.0.0"): _native,
    ("statistics.seasonal_mann_kendall", "1.0.0"): _native,
    ("table.deduplicate", "1.0.0"): _native,
    ("table.filter", "1.0.0"): _native,
    ("table.join", "1.0.0"): _native,
    ("table.profile", "1.0.0"): _native,
    ("table.project", "1.0.0"): _native,
    ("table.sort", "1.0.0"): _native,
    ("table.validate", "1.0.0"): _native,
    ("timeseries.aggregate", "1.0.0"): _native,
    ("timeseries.autocorrelation", "1.0.0"): _native,
    ("timeseries.baseline_anomaly", "1.0.0"): _native,
    ("timeseries.completeness", "1.0.0"): _native,
    ("timeseries.cross_correlation", "1.0.0"): _native,
    ("timeseries.empirical_percentile", "1.0.0"): _native,
    ("timeseries.gap_runs", "1.0.0"): _native,
    ("timeseries.lag_change", "1.0.0"): _native,
    ("timeseries.period_change", "1.0.0"): _native,
    ("timeseries.rate_of_change", "1.0.0"): _native,
    ("timeseries.regularize", "1.0.0"): _native,
    ("timeseries.rolling_statistics", "1.0.0"): _native,
    ("timeseries.seasonal_anomaly", "1.0.0"): _native,
    ("timeseries.snapshot", "1.0.0"): _native,
    ("timeseries.threshold_events", "1.0.0"): _native,
    ("vector.buffer", "1.0.0"): _native,
    ("vector.centroid", "1.0.0"): _qgis,
    ("vector.clip", "1.0.0"): _qgis,
    ("vector.convex_hull", "1.0.0"): _qgis,
    ("vector.dissolve", "1.0.0"): _qgis,
    ("vector.extract_by_extent", "1.0.0"): _qgis,
    ("vector.merge", "1.0.0"): _qgis,
    ("vector.multipart_to_singleparts", "1.0.0"): _qgis,
    ("vector.nearest_join", "1.0.0"): _qgis,
    ("vector.overlay", "1.0.0"): _qgis,
    ("vector.point_on_surface", "1.0.0"): _qgis,
    ("vector.simplify", "1.0.0"): _qgis,
    ("vector.spatial_join", "1.0.0"): _qgis,
}


def execute(operator_id: str, version: str, inputs, parameters, output_dir: Path):
    try:
        runner = IMPLEMENTATIONS[(operator_id, version)]
    except KeyError as cause:
        raise ValueError("OPERATOR_NOT_IMPLEMENTED") from cause
    return runner(operator_id, inputs, parameters, output_dir)
