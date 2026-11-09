from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "migration" / "import_yaozhanbao_case.py"
SPEC = importlib.util.spec_from_file_location("import_yaozhanbao_case", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inventory(root: Path) -> tuple[int, str]:
    rows = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rows.append(
                f"{path.relative_to(root).as_posix()}\t{path.stat().st_size}\t"
                f"{_sha256(path)}\n"
            )
    return len(rows), hashlib.sha256("".join(rows).encode("utf-8")).hexdigest()


def _case_root(tmp_path: Path) -> tuple[Path, dict[str, bytes]]:
    case_root = tmp_path / "case"
    source_package = case_root / "payload" / "source-package"
    prepared = source_package / "original" / "prepared"
    resources_root = case_root / "payload" / "resources"
    prepared.mkdir(parents=True)
    resources_root.mkdir(parents=True)
    (source_package / "README.md").write_text("source evidence\n", encoding="utf-8")

    contents = {f"resource-{index}": f"value-{index}".encode() for index in range(19)}
    resources = {}
    relocation_resources = {}
    integrity_resources = {}
    for name, content in contents.items():
        path = resources_root / f"{name}.bin"
        path.write_bytes(content)
        resources[name] = {
            "path": f"/retired/workspace/{name}.bin",
            "format": "bin",
            "mediaType": "application/octet-stream",
            "sizeBytes": len(content),
            "checksum": "sha256:" + _sha256(path),
        }
        relocation_resources[name] = f"payload/resources/{name}.bin"
        integrity_resources[name] = {
            "size_bytes": len(content),
            "sha256": _sha256(path),
        }
    manifest = {
        "schemaVersion": "transfer-case-inputs.v1",
        "resources": resources,
        "expected": {},
    }
    manifest_path = prepared / "inputs.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    file_count, inventory_sha256 = _inventory(source_package)
    manifest_sha256 = _sha256(manifest_path)
    (case_root / "relocation.v1.json").write_text(
        json.dumps(
            {
                "schema_version": "yaozhanbao-case-relocation.v1",
                "source_manifest_relative_path": "payload/source-package/original/prepared/inputs.json",
                "source_manifest_sha256": manifest_sha256,
                "resources": relocation_resources,
            }
        ),
        encoding="utf-8",
    )
    (case_root / "integrity.v1.json").write_text(
        json.dumps(
            {
                "schema_version": "yaozhanbao-case-integrity.v1",
                "source_manifest_sha256": manifest_sha256,
                "source_package": {
                    "sentinel_relative_path": "payload/source-package/README.md",
                    "file_count": file_count,
                    "inventory_sha256": inventory_sha256,
                },
                "resources": integrity_resources,
            }
        ),
        encoding="utf-8",
    )
    return case_root, contents


def test_local_case_preserves_manifest_bytes_and_resolves_all_resources(
    tmp_path: Path,
) -> None:
    case_root, contents = _case_root(tmp_path)

    manifest_path, resolved = MODULE._load_case_layout(case_root)
    manifest, checksum, verified = MODULE._load_and_verify(manifest_path, resolved)

    assert checksum == _sha256(manifest_path)
    assert manifest["resources"].keys() == contents.keys()
    assert verified == resolved
    assert all(path.is_relative_to(case_root) for path in verified.values())


def test_local_case_rejects_tampered_resource(tmp_path: Path) -> None:
    case_root, _ = _case_root(tmp_path)
    (case_root / "payload" / "resources" / "resource-0.bin").write_bytes(b"changed")

    with pytest.raises(ValueError, match="CASE_RESOURCE_(SIZE|CHECKSUM)_CHANGED:resource-0"):
        MODULE._load_case_layout(case_root)


def test_local_case_rejects_path_escape(tmp_path: Path) -> None:
    case_root, _ = _case_root(tmp_path)
    relocation_path = case_root / "relocation.v1.json"
    relocation = json.loads(relocation_path.read_text(encoding="utf-8"))
    relocation["resources"]["resource-0"] = "../outside.bin"
    relocation_path.write_text(json.dumps(relocation), encoding="utf-8")

    with pytest.raises(ValueError, match="CASE_RELOCATION_PATH_ESCAPE"):
        MODULE._load_case_layout(case_root)


def test_local_case_rejects_payload_symlink(tmp_path: Path) -> None:
    case_root, _ = _case_root(tmp_path)
    target = case_root / "payload" / "resources" / "resource-0.bin"
    linked = case_root / "payload" / "resources" / "linked.bin"
    try:
        linked.symlink_to(target)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")
    relocation_path = case_root / "relocation.v1.json"
    relocation = json.loads(relocation_path.read_text(encoding="utf-8"))
    relocation["resources"]["resource-0"] = "payload/resources/linked.bin"
    relocation_path.write_text(json.dumps(relocation), encoding="utf-8")

    with pytest.raises(ValueError, match="CASE_PAYLOAD_SYMLINK_INVALID"):
        MODULE._load_case_layout(case_root)


def test_explicit_legacy_manifest_remains_supported(tmp_path: Path) -> None:
    case_root, contents = _case_root(tmp_path)
    manifest_path, _ = MODULE._load_case_layout(case_root)
    legacy_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    relocation = json.loads(
        (case_root / "relocation.v1.json").read_text(encoding="utf-8")
    )
    for name, item in legacy_manifest["resources"].items():
        item["path"] = str(case_root / relocation["resources"][name])
    legacy_path = tmp_path / "legacy-inputs.json"
    legacy_path.write_text(json.dumps(legacy_manifest), encoding="utf-8")

    _, _, verified = MODULE._load_and_verify(legacy_path)

    assert verified.keys() == contents.keys()


def test_verify_only_has_no_network_side_effect(tmp_path: Path, monkeypatch, capsys) -> None:
    case_root, _ = _case_root(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [str(SCRIPT), "--case-root", str(case_root), "--verify-only"],
    )

    MODULE.main()

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "verified"
    assert result["resources"] == 19
