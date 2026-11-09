from __future__ import annotations

from scripts.checks.check_configuration_boundaries import configuration_violations


def test_current_configuration_ownership_is_clean():
    assert configuration_violations() == []


def test_configuration_gate_rejects_hardcoded_identity_and_compose_capacity(tmp_path):
    source = tmp_path / "platform" / "assembler"
    source.mkdir(parents=True)
    (source / "bad.py").write_text(
        'subject = "local-researcher"\nvalue = "${EXECUTOR_NATIVE_SLOTS:-2}"\n',
        encoding="utf-8",
    )

    findings = configuration_violations(tmp_path)

    assert findings == [
        {"code": "COMPOSE_EXECUTOR_CAPACITY_SUBSTITUTION", "path": "platform/assembler/bad.py"},
        {"code": "DUPLICATE_EXECUTOR_CAPACITY_PARSER", "path": "platform/assembler/bad.py"},
        {"code": "HARDCODED_LOCAL_IDENTITY", "path": "platform/assembler/bad.py"},
    ]
