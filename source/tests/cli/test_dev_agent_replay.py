from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.development.dev as dev


def test_dev_agent_replay_uses_xdg_cache_and_three_repetitions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cache = tmp_path / "cache" / "groundwater-v6"
    paths = SimpleNamespace(cache=cache)
    calls: list[dict[str, object]] = []

    monkeypatch.setattr(dev, "activate", lambda **_kwargs: None)
    monkeypatch.setattr(dev, "lifecycle", lambda: paths)
    monkeypatch.setattr(dev, "ensure_private_roots", lambda *_args: None)

    def run_agent_replay(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {
            "schemaVersion": "agent-replay-run.v1",
            "status": "PASS",
            "repetitions": kwargs["repetitions"],
            "runDirectory": str(kwargs["run_directory"]),
        }

    monkeypatch.setattr(dev, "run_agent_replay", run_agent_replay)

    dev.main(["agent-replay", "--case", "all"])

    assert len(calls) == 1
    call = calls[0]
    assert call["repository_root"] == dev.ROOT
    assert call["case_file"] == (
        dev.ROOT / "scripts/development/agent-replay-cases.v1.json"
    )
    assert call["case_ids"] is None
    assert call["repetitions"] == 3
    run_directory = Path(str(call["run_directory"]))
    assert run_directory.parent == cache / "agent-replay-runs"
    assert json.loads(capsys.readouterr().out)["status"] == "PASS"


def test_dev_agent_replay_returns_stable_json_for_unknown_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts.development.agent_replay import AgentReplayRefusal

    paths = SimpleNamespace(cache=tmp_path / "cache" / "groundwater-v6")
    monkeypatch.setattr(dev, "activate", lambda **_kwargs: None)
    monkeypatch.setattr(dev, "lifecycle", lambda: paths)
    monkeypatch.setattr(dev, "ensure_private_roots", lambda *_args: None)
    monkeypatch.setattr(
        dev,
        "run_agent_replay",
        lambda **_kwargs: (_ for _ in ()).throw(
            AgentReplayRefusal("AGENT_REPLAY_CASE_NOT_FOUND")
        ),
    )

    with pytest.raises(SystemExit, match="2"):
        dev.main(["agent-replay", "--case", "missing", "--repetitions", "3"])

    assert json.loads(capsys.readouterr().err) == {
        "code": "AGENT_REPLAY_CASE_NOT_FOUND",
        "status": "FAILED",
    }
