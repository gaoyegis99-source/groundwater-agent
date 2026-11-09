from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from moperators.operators import (
    document_chunk,
    document_text_extract,
    groundwater_drawdown,
    head_calculate,
    linear_trend,
    mad_outliers,
    mann_kendall,
    period_change,
    raster_cell_statistics,
    raster_sample_points,
    seasonal_mann_kendall,
    spreadsheet_extract_table,
    statistics_pettitt,
    table_deduplicate,
    table_filter,
    table_join,
    table_profile,
    table_project,
    table_sort,
    table_validate,
    timeseries_aggregate,
    timeseries_autocorrelation,
    timeseries_baseline_anomaly,
    timeseries_completeness,
    timeseries_cross_correlation,
    timeseries_empirical_percentile,
    timeseries_gap_runs,
    timeseries_lag_change,
    timeseries_rate_of_change,
    timeseries_regularize,
    timeseries_rolling_statistics,
    timeseries_seasonal_anomaly,
    timeseries_snapshot,
    timeseries_threshold_events,
)
from moperators.operators.analysis_common import TableInput

HANDLERS = {
    "groundwater.head.calculate": head_calculate.run,
    "timeseries.aggregate": timeseries_aggregate.run,
    "timeseries.completeness": timeseries_completeness.run,
    "timeseries.snapshot": timeseries_snapshot.run,
    "statistics.linear_trend": linear_trend.run,
    "statistics.mann_kendall": mann_kendall.run,
    "statistics.mad_outliers": mad_outliers.run,
    "timeseries.period_change": period_change.run,
}

DIRECT_HANDLERS = {
    "document.chunk": document_chunk.run,
    "document.text_extract": document_text_extract.run,
    "groundwater.drawdown.calculate": groundwater_drawdown.run,
    "raster.cell_statistics": raster_cell_statistics.run,
    "raster.sample_points": raster_sample_points.run,
    "spreadsheet.extract_table": spreadsheet_extract_table.run,
    "statistics.pettitt_change_point": statistics_pettitt.run,
    "statistics.seasonal_mann_kendall": seasonal_mann_kendall.run,
    "table.deduplicate": table_deduplicate.run,
    "table.filter": table_filter.run,
    "table.join": table_join.run,
    "table.profile": table_profile.run,
    "table.project": table_project.run,
    "table.sort": table_sort.run,
    "table.validate": table_validate.run,
    "timeseries.autocorrelation": timeseries_autocorrelation.run,
    "timeseries.baseline_anomaly": timeseries_baseline_anomaly.run,
    "timeseries.cross_correlation": timeseries_cross_correlation.run,
    "timeseries.empirical_percentile": timeseries_empirical_percentile.run,
    "timeseries.gap_runs": timeseries_gap_runs.run,
    "timeseries.lag_change": timeseries_lag_change.run,
    "timeseries.rate_of_change": timeseries_rate_of_change.run,
    "timeseries.regularize": timeseries_regularize.run,
    "timeseries.rolling_statistics": timeseries_rolling_statistics.run,
    "timeseries.seasonal_anomaly": timeseries_seasonal_anomaly.run,
    "timeseries.threshold_events": timeseries_threshold_events.run,
}


def run_analysis_operator(
    operator_id: str,
    inputs: Mapping[str, Sequence[TableInput]],
    parameters: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any] | None:
    direct_handler = DIRECT_HANDLERS.get(operator_id)
    if direct_handler is not None:
        return direct_handler(inputs, parameters, output_dir)
    handler = HANDLERS.get(operator_id)
    if handler is None:
        return None
    return handler(inputs["observations"][0], parameters, output_dir)
