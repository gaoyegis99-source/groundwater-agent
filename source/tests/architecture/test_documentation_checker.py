from __future__ import annotations

from pathlib import Path

import pytest

from scripts.checks import check_documentation as module_under_test


@pytest.mark.parametrize(
    "fragment,missing",
    [
        ("current-checks", False),
        ("current-checks-1", False),
        ("%E5%BD%93%E5%89%8D%E6%A3%80%E6%9F%A5", False),
        ("development-checks", True),
        ("code-example", True),
    ],
)
def test_heading_links_include_local_unicode_and_duplicate_headings(
    tmp_path, monkeypatch, fragment, missing
):
    document = tmp_path / "checks.md"
    document.write_text(
        "# Current checks\n## Current checks\n## 当前检查\n"
        "```bash\n# Code example\n```\n"
        f"[check](#{fragment})\n[check](checks.md#{fragment})\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module_under_test, "ROOT", tmp_path)
    monkeypatch.setattr(module_under_test, "markdown_files", lambda: [document])
    errors = []
    module_under_test.check_links(errors)
    assert len(errors) == (2 if missing else 0)
    assert all(error.startswith("missing heading anchor:") for error in errors)


@pytest.mark.parametrize(
    "destination",
    [
        "/home/user/result.json",
        "/tmp/run/result.json",
        "/var/lib/result.json",
        "file:///tmp/result.json",
        "C:/Users/user/result.json",
        "D:\\work\\result.json",
    ],
)
def test_active_document_rejects_clickable_host_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, destination: str
) -> None:
    document = tmp_path / "active.md"
    document.write_text(f"[evidence](<{destination}>)\n", encoding="utf-8")
    monkeypatch.setattr(module_under_test, "ROOT", tmp_path)
    monkeypatch.setattr(module_under_test, "markdown_files", lambda: [document])
    errors: list[str] = []

    module_under_test.check_links(errors)

    assert errors == [
        f"active documentation has clickable host path: active.md -> {destination}"
    ]


def test_site_path_and_plain_host_path_text_are_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = tmp_path / "active.md"
    document.write_text(
        "[API route](/v1)\nEvidence: `/tmp/run/result.json`\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module_under_test, "ROOT", tmp_path)
    monkeypatch.setattr(module_under_test, "markdown_files", lambda: [document])
    errors: list[str] = []

    module_under_test.check_links(errors)

    assert errors == []


def test_exported_source_without_git_metadata_scans_all_markdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = tmp_path / "docs/current.md"
    current.parent.mkdir()
    current.write_text("current\n", encoding="utf-8")
    historical = tmp_path / "docs/history/old.md"
    historical.parent.mkdir()
    historical.write_text("old\n", encoding="utf-8")
    monkeypatch.setattr(module_under_test, "ROOT", tmp_path)

    assert module_under_test.markdown_files() == [current, historical]


def test_missing_history_link_is_not_masked_by_an_export_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = tmp_path / "docs/current.md"
    document.parent.mkdir()
    document.write_text("[history](history/old.md)\n", encoding="utf-8")
    monkeypatch.setattr(module_under_test, "ROOT", tmp_path)
    monkeypatch.setattr(module_under_test, "markdown_files", lambda: [document])
    errors: list[str] = []

    module_under_test.check_links(errors)

    assert errors == ["missing source link: docs/current.md -> history/old.md"]


def test_current_product_documentation_matches_authoritative_manifests() -> None:
    errors: list[str] = []

    module_under_test.check_current_product_docs(errors)

    assert errors == []
