"""Static registrations whose source does not alter the original registry lock."""

from moperators.operators.statistics_describe_columns import run

IMPLEMENTATIONS: dict = {
    ("statistics.describe_columns", "1.0.0"): run,
}
