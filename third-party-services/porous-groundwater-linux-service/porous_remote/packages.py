"""Bounded ZIP contracts for submitted definitions and service-owned artifacts."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile

MAX_BYTES = 128 * 1024**2
MAX_DEFINITION = 2 * 1024**2
MAX_ENTRIES = 500
SIGNATURE = "_porous-package.json"


def encode(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode()


def checksum(path: Path) -> str:
    with path.open("rb") as stream:
        return "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_bytes(encode(value))


def read_definitions(path: Path, names: set[str]) -> dict[str, dict]:
    try:
        with ZipFile(path) as archive:
            members = archive.infolist()
            if (
                len(members) != len(names)
                or set(archive.namelist()) != names
                or any(member.file_size > MAX_DEFINITION or member.flag_bits & 1
                       or stat.S_ISLNK(member.external_attr >> 16) for member in members)
            ):
                raise ValueError("PROJECT_PACKAGE_INVALID")
            definitions = {name: json.loads(archive.read(name)) for name in names}
    except (BadZipFile, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("PROJECT_PACKAGE_INVALID") from error
    if any(not isinstance(value, dict) for value in definitions.values()):
        raise ValueError("MODEL_DEFINITION_INVALID")
    return definitions


def project_definition(path: Path) -> dict:
    name = "model_definition.json"
    return read_definitions(path, {name})[name]


def seal(output: Path, role: str, files: dict[str, Path], key: bytes) -> None:
    if len(files) >= MAX_ENTRIES or sum(path.stat().st_size for path in files.values()) > MAX_BYTES:
        raise ValueError("OUTPUT_PACKAGE_LIMIT_EXCEEDED")
    claims = {"role": role, "files": {name: checksum(path) for name, path in files.items()}}
    signature = hmac.digest(key, encode(claims), "sha256").hex()
    with ZipFile(output, "x", compression=ZIP_DEFLATED) as archive:
        for name, path in sorted(files.items()):
            archive.write(path, name)
        archive.writestr(SIGNATURE, encode({"claims": claims, "signature": signature}))


def extract_sealed(package: Path, target: Path, role: str, key: bytes) -> None:
    try:
        with ZipFile(package) as archive:
            members = archive.infolist()
            if len(members) > MAX_ENTRIES or sum(i.file_size for i in members) > MAX_BYTES:
                raise ValueError("PACKAGE_LIMIT_EXCEEDED")
            identity = archive.getinfo(SIGNATURE)
            if identity.file_size > 128 * 1024:
                raise ValueError("PACKAGE_SIGNATURE_INVALID")
            header = json.loads(archive.read(identity))
            claims = header["claims"]
            expected = hmac.digest(key, encode(claims), "sha256").hex()
            if not hmac.compare_digest(header["signature"], expected) or claims["role"] != role:
                raise ValueError("PACKAGE_SIGNATURE_INVALID")
            files = claims["files"]
            if (
                len(members) != len(files) + 1
                or set(archive.namelist()) != set(files) | {SIGNATURE}
            ):
                raise ValueError("PACKAGE_INVENTORY_INVALID")
            for member in members:
                name = member.filename
                if name == SIGNATURE:
                    continue
                relative = PurePosixPath(name)
                if (
                    relative.is_absolute()
                    or relative.as_posix() != name
                    or not relative.parts
                    or ".." in relative.parts
                    or "\\" in name
                    or stat.S_ISLNK(member.external_attr >> 16)
                    or member.is_dir()
                    or member.flag_bits & 1
                ):
                    raise ValueError("PACKAGE_PATH_INVALID")
                output = target.joinpath(*relative.parts)
                output.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, output.open("xb") as destination:
                    shutil.copyfileobj(source, destination)
                if checksum(output) != files[name]:
                    raise ValueError("PACKAGE_CONTENT_INVALID")
    except (BadZipFile, KeyError, TypeError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("SEALED_PACKAGE_INVALID") from error
