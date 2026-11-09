from __future__ import annotations

from io import BytesIO

import pytest

from platform_data_service.errors import DataProblemError
from platform_data_service.ingestion.json_validation import validate_json


def validate(content: bytes, *, max_bytes: int = 1024 * 1024) -> None:
    validate_json(BytesIO(content), size=len(content), max_bytes=max_bytes)


@pytest.mark.parametrize(
    "content",
    [
        b'{"outer":{"value":1},"items":[true,null,"text"]}',
        b'\xef\xbb\xbf{"value":1}',
        b'0',
    ],
)
def test_strict_json_validation_accepts_unambiguous_json(content: bytes) -> None:
    validate(content)


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (b'{"id":1,"id":2}', "JSON_DUPLICATE_KEY"),
        (b'{"outer":{"id":1,"id":2}}', "JSON_DUPLICATE_KEY"),
        (b'{"missing":', "JSON_INVALID"),
        (b'{"value":NaN}', "JSON_NONFINITE_NUMBER"),
        (b'{"value":1e400}', "JSON_NONFINITE_NUMBER"),
        (b'{"value":"\xff"}', "JSON_INVALID"),
        (b'{"value":"\\ud800"}', "JSON_INVALID_UNICODE"),
    ],
)
def test_strict_json_validation_rejects_ambiguous_or_invalid_content(
    content: bytes, code: str
) -> None:
    with pytest.raises(DataProblemError, match=code) as caught:
        validate(content)
    assert caught.value.code == code


def test_strict_json_validation_fails_closed_above_budget() -> None:
    with pytest.raises(DataProblemError, match="JSON_VALIDATION_BUDGET") as caught:
        validate(b'{"value":1}', max_bytes=2)
    assert caught.value.status == 413


def test_strict_json_validation_rejects_excessive_nesting() -> None:
    content = b"[" * 101 + b"0" + b"]" * 101
    with pytest.raises(DataProblemError, match="JSON_NESTING_TOO_DEEP"):
        validate(content)
