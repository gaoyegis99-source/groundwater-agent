from scripts.evaluation.benchmark_v6_state_layout import benchmark


def test_v6_state_layout_experiment_proves_equivalence_and_failure_boundaries() -> None:
    report = benchmark((3,), 1)
    assert report["schema_version"] == "v6-state-layout-experiment-v1"
    assert all(report["safety_checks"].values())
    full, normalized = report["results"]
    assert full["layout"] == "full_snapshot"
    assert normalized["layout"] == "normalized_cells"
    assert full["transitions"] == normalized["transitions"] == 15
    assert normalized["state_payload_bytes"] < full["state_payload_bytes"]
