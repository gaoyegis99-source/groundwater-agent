from pathlib import Path

from scripts.checks import check_native_tests


def test_native_suite_uses_exact_locked_cpython_instead_of_host_interpreter() -> None:
    version = check_native_tests._locked_python_version()
    command = check_native_tests._venv_command(
        "/tools/uv", Path("/cache/native/venv"), version
    )

    assert version == "3.12.8"
    assert command == [
        "/tools/uv",
        "venv",
        "--python",
        "3.12.8",
        "--offline",
        "--no-python-downloads",
        "/cache/native/venv",
    ]


def test_native_suite_owns_its_pytest_temp_root() -> None:
    command = check_native_tests._pytest_command(
        "/cache/native/venv/bin/python", Path("/cache/native/run-1")
    )

    assert command[command.index("--basetemp") + 1] == "/cache/native/run-1/pytest"
    assert "/tmp/pytest-of-" not in " ".join(command)
