from __future__ import annotations

from pathlib import Path

import pytest

from scripts.development.agent_replay import AgentReplayRefusal, run_agent_replay


EXPECTED_COUNTS = {
    "dialogue-no-execution": (0, 0),
    "execution-proposal-confirmation": (1, 0),
    "fan-out-draft": (0, 0),
    "knowledge-read-citation": (0, 0),
    "minimax-noncase-terminal": (1, 1),
    "missing-field-correction": (0, 0),
}


def test_all_agent_cases_repeat_three_times_with_stable_invariants(
    tmp_path: Path,
) -> None:
    repository = Path(__file__).resolve().parents[2]
    result = run_agent_replay(
        repository_root=repository,
        case_file=repository / "scripts/development/agent-replay-cases.v1.json",
        run_directory=tmp_path / "agent-replay",
        case_ids=None,
        repetitions=3,
    )

    assert result["status"] == "PASS"
    assert result["repetitions"] == 3
    assert set(result["caseResults"]) == set(EXPECTED_COUNTS)
    for case_id, (confirmations, workflow_runs) in EXPECTED_COUNTS.items():
        case = result["caseResults"][case_id]
        assert case["status"] == "PASS"
        assert case["stable"] is True
        assert len(set(case["iterationSnapshotSha256"])) == 1
        assert case["snapshot"]["counts"]["confirmations"] == confirmations
        assert case["snapshot"]["counts"]["workflowRuns"] == workflow_runs
        assert set(case["snapshot"]["assertions"].values()) == {"PASS"}


def test_agent_replay_rejects_unknown_case_before_creating_output(
    tmp_path: Path,
) -> None:
    repository = Path(__file__).resolve().parents[2]
    output = tmp_path / "unknown"

    with pytest.raises(AgentReplayRefusal, match="AGENT_REPLAY_CASE_NOT_FOUND"):
        run_agent_replay(
            repository_root=repository,
            case_file=repository / "scripts/development/agent-replay-cases.v1.json",
            run_directory=output,
            case_ids=("not-a-case",),
            repetitions=3,
        )

    assert not output.exists()


@pytest.mark.parametrize("repetitions", [0, 11])
def test_agent_replay_bounds_repetition_count(
    tmp_path: Path,
    repetitions: int,
) -> None:
    repository = Path(__file__).resolve().parents[2]

    with pytest.raises(AgentReplayRefusal, match="AGENT_REPLAY_REPETITIONS_INVALID"):
        run_agent_replay(
            repository_root=repository,
            case_file=repository / "scripts/development/agent-replay-cases.v1.json",
            run_directory=tmp_path / f"repetitions-{repetitions}",
            case_ids=("dialogue-no-execution",),
            repetitions=repetitions,
        )
