from __future__ import annotations

import json

from scripts.maintenance import gw


def test_bootstrap_admin_defaults_to_research_dev(
    tmp_path, monkeypatch, capsys
) -> None:
    for name in ("CONFIG", "DATA", "STATE", "CACHE"):
        monkeypatch.setenv(f"XDG_{name}_HOME", str(tmp_path / name.lower()))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    answers = iter(("correct horse battery staple",) * 2)
    monkeypatch.setattr(gw.getpass, "getpass", lambda _prompt: next(answers))

    gw.main(
        [
            "auth",
            "bootstrap-admin",
            "--username",
            "research-admin",
            "--display-name",
            "Research Admin",
        ]
    )

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "PASS"
    database = (
        gw.lifecycle().data
        / "research-dev/groundwater/kernel/kernel.sqlite3"
    )
    assert database.is_file()
