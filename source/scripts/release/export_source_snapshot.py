from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess


ROOT = Path(__file__).resolve().parents[2]
SECRET_PATTERNS = {
    "private-key": re.compile(rb"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
    "openai-compatible-key": re.compile(rb"\bsk-(?:cp-)?[A-Za-z0-9_-]{20,}\b"),
    "github-token": re.compile(rb"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"),
    "aws-access-key": re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
}


def _git(*args: str, text: bool = True) -> str | bytes:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=text
    )
    return result.stdout


def _load_manifest(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schemaVersion") != "source-export-manifest.v1":
        raise ValueError("EXPORT_MANIFEST_VERSION_UNSUPPORTED")
    return value


def _selected(path: str, manifest: dict[str, object]) -> bool:
    included = path in manifest["includeExact"] or any(
        path.startswith(prefix) for prefix in manifest["includePrefixes"]
    )
    if not included:
        return False
    if path in manifest["excludeExact"]:
        return False
    if any(path.startswith(prefix) for prefix in manifest["excludePrefixes"]):
        return False
    parts = PurePosixPath(path).parts
    if any(segment in manifest["excludePathSegments"] for segment in parts):
        return False
    if any(path.endswith(suffix) for suffix in manifest["excludeSuffixes"]):
        return False
    if any(part.endswith(".egg-info") for part in parts):
        return False
    return True


def _tracked_entries(revision: str) -> list[tuple[str, str, str]]:
    raw = _git("ls-tree", "-r", "-z", revision, text=False)
    entries: list[tuple[str, str, str]] = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        metadata, encoded_path = record.split(b"\t", 1)
        mode, kind, _object_id = metadata.decode("ascii").split()
        entries.append((mode, kind, encoded_path.decode("utf-8")))
    return entries


def _blob(revision: str, path: str) -> bytes:
    return _git("show", f"{revision}:{path}", text=False)


def _scan_secret(path: str, content: bytes) -> None:
    for label, pattern in SECRET_PATTERNS.items():
        if pattern.search(content):
            raise ValueError(f"EXPORT_SECRET_DETECTED:{label}:{path}")


def _prepare_output(output: Path) -> None:
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"EXPORT_DESTINATION_NOT_EMPTY:{output}")
    output.mkdir(parents=True, exist_ok=True)


def export(output: Path, manifest_path: Path) -> dict[str, object]:
    manifest = _load_manifest(manifest_path)
    requested_revision = str(manifest.get("sourceRevision", "HEAD"))
    revision = str(_git("rev-parse", requested_revision)).strip()
    if requested_revision == "HEAD":
        dirty = subprocess.run(
            ["git", "diff-index", "--quiet", "HEAD", "--"], cwd=ROOT
        ).returncode
        if dirty:
            raise ValueError("EXPORT_SOURCE_DIRTY")
    _prepare_output(output)
    selected: list[str] = []
    checksums: list[tuple[str, str]] = []
    for mode, kind, path in _tracked_entries(revision):
        if not _selected(path, manifest):
            continue
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ValueError(f"EXPORT_GIT_MODE_UNSUPPORTED:{mode}:{kind}:{path}")
        content = _blob(revision, path)
        _scan_secret(path, content)
        destination = output / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        destination.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
        if mode == "100755":
            destination.chmod(
                destination.stat().st_mode
                | stat.S_IXUSR
                | stat.S_IXGRP
                | stat.S_IXOTH
            )
        selected.append(path)
        checksums.append((hashlib.sha256(content).hexdigest(), path))

    missing = sorted(set(manifest["includeExact"]) - set(selected))
    if missing:
        raise ValueError("EXPORT_REQUIRED_FILE_MISSING:" + ",".join(missing))

    metadata = {
        "schemaVersion": "clean-source-snapshot.v1",
        "releaseLabel": manifest["releaseLabel"],
        "publicApiVersion": manifest["publicApiVersion"],
        "sourceCommit": revision,
        "sourceFileCount": len(selected),
        "historyIncluded": False,
        "runtimeDataIncluded": False,
        "credentialsIncluded": False,
    }
    metadata_bytes = (json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode()
    _scan_secret("RELEASE-METADATA.json", metadata_bytes)
    (output / "RELEASE-METADATA.json").write_bytes(metadata_bytes)
    checksums.append(
        (hashlib.sha256(metadata_bytes).hexdigest(), "RELEASE-METADATA.json")
    )
    checksum_text = "".join(
        f"{digest}  {path}\n" for digest, path in sorted(checksums, key=lambda row: row[1])
    )
    (output / "SOURCE-MANIFEST.sha256").write_text(checksum_text, encoding="utf-8")
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export a clean product source snapshot from Git HEAD"
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    try:
        metadata = export(args.output.resolve(), args.manifest.resolve())
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(metadata, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
