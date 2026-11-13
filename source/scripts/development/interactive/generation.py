"""Observe which mounted Python source a development API process started with."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import uuid

from .profile import API_SOURCE_ROOTS, API_TARGETS

PROBE_PATH = "/internal/dev-source-generation"


def source_digest(root: Path, service: str) -> str:
    digest = hashlib.sha256()
    for directory in API_SOURCE_ROOTS[service]:
        for path in sorted((root / directory).rglob("*.py")):
            if path.is_symlink() or not path.is_file():
                continue
            name = path.relative_to(root).as_posix().encode()
            digest.update(name + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    return f"sha256:{digest.hexdigest()}"


def create_app():
    service = os.environ["PLATFORM_DEV_API_SERVICE"]
    root = Path(__file__).resolve().parents[3]
    started_with = source_digest(root, service)
    module, attribute = API_TARGETS[service]
    app = getattr(importlib.import_module(module), attribute)
    if attribute == "create_app":
        app = app()
    if source_digest(root, service) != started_with:
        raise RuntimeError("DEV_API_SOURCE_CHANGED_DURING_IMPORT")
    proof = json.dumps(
        {"sourceDigest": started_with, "generation": uuid.uuid4().hex}
    ).encode()

    async def observed_app(scope, receive, send):
        client = scope.get("client")
        if (
            scope["type"] == "http"
            and scope["method"] == "GET"
            and scope["path"] == PROBE_PATH
            and client
            and client[0] == "127.0.0.1"
        ):
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            })
            await send({"type": "http.response.body", "body": proof})
        else:
            await app(scope, receive, send)

    return observed_app
