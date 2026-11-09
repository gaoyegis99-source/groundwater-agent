from __future__ import annotations

import pytest

from platform_knowledge_next.config import Settings


def _environment(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(
        "KNOWLEDGE_DATABASE_URL",
        "postgresql://knowledge@knowledge-next-db:5432/knowledge",
    )
    monkeypatch.setenv("KNOWLEDGE_DATABASE_PASSWORD_FILE", str(tmp_path / "password"))
    (tmp_path / "password").write_text("p@ss:word\n")


def test_database_password_is_read_from_a_secret_file(monkeypatch, tmp_path) -> None:
    _environment(monkeypatch, tmp_path)

    assert Settings.from_env().database_url == (
        "postgresql://knowledge:p%40ss%3Aword@knowledge-next-db:5432/knowledge"
    )


def test_secret_file_mode_rejects_an_inline_database_password(
    monkeypatch, tmp_path
) -> None:
    _environment(monkeypatch, tmp_path)
    monkeypatch.setenv(
        "KNOWLEDGE_DATABASE_URL",
        "postgresql://knowledge:inline@knowledge-next-db:5432/knowledge",
    )

    with pytest.raises(ValueError, match="URL_WITH_SECRET_FILE_INVALID"):
        Settings.from_env()
