from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
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

    manifest_path, resolved = MODULE._load_case_layout(case_root, case_root)
    manifest, checksum, verified = MODULE._load_and_verify(manifest_path, resolved)

    assert checksum == _sha256(manifest_path)
    assert manifest["resources"].keys() == contents.keys()
    assert verified == resolved
    assert all(path.is_relative_to(case_root) for path in verified.values())


def test_local_case_rejects_tampered_resource(tmp_path: Path) -> None:
    case_root, _ = _case_root(tmp_path)
    (case_root / "payload" / "resources" / "resource-0.bin").write_bytes(b"changed")

    with pytest.raises(ValueError, match="CASE_RESOURCE_(SIZE|CHECKSUM)_CHANGED:resource-0"):
        MODULE._load_case_layout(case_root, case_root)


def test_local_case_rejects_path_escape(tmp_path: Path) -> None:
    case_root, _ = _case_root(tmp_path)
    relocation_path = case_root / "relocation.v1.json"
    relocation = json.loads(relocation_path.read_text(encoding="utf-8"))
    relocation["resources"]["resource-0"] = "../outside.bin"
    relocation_path.write_text(json.dumps(relocation), encoding="utf-8")

    with pytest.raises(ValueError, match="CASE_RELOCATION_PATH_ESCAPE"):
        MODULE._load_case_layout(case_root, case_root)


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
        MODULE._load_case_layout(case_root, case_root)


def test_relocation_plan_and_apply_are_verified(
    tmp_path: Path, monkeypatch
) -> None:
    source, _ = _case_root(tmp_path)
    data_home = tmp_path / "xdg-data"
    state_home = tmp_path / "xdg-state"
    paths = MODULE.lifecycle(
        {
            "HOME": str(tmp_path),
            "XDG_DATA_HOME": str(data_home),
            "XDG_STATE_HOME": str(state_home),
            "XDG_CONFIG_HOME": str(tmp_path / "xdg-config"),
            "XDG_CACHE_HOME": str(tmp_path / "xdg-cache"),
            "XDG_RUNTIME_DIR": str(tmp_path / "xdg-runtime"),
        }
    )
    target = paths.data / "case-assets/yaozhanbao-transfer-case"
    monkeypatch.setattr(MODULE, "DESCRIPTOR_ROOT", source)
    monkeypatch.setattr(MODULE, "lifecycle", lambda: paths)

    plan = MODULE.relocation_plan(source, target)
    assert plan["files"] == 21
    assert not target.exists()

    with pytest.raises(ValueError, match="CONFIRMATION_MISMATCH"):
        MODULE.apply_relocation(plan, "sha256:wrong")
    receipt = MODULE.apply_relocation(plan, plan["confirmationDigest"])

    assert receipt["status"] == "PASS"
    assert receipt["sourcePayloadRemoved"] is True
    assert target.joinpath("payload").is_dir()
    MODULE._load_case_layout(target, source)


def test_relocation_rejects_source_change(tmp_path: Path, monkeypatch) -> None:
    source, _ = _case_root(tmp_path)
    monkeypatch.setattr(MODULE, "DESCRIPTOR_ROOT", source)
    target = tmp_path / "target"
    plan = MODULE.relocation_plan(source, target)
    (source / "payload/resources/resource-0.bin").write_bytes(b"changed")

    with pytest.raises(ValueError, match="CASE_RESOURCE_(SIZE|CHECKSUM)_CHANGED"):
        MODULE.apply_relocation(plan, plan["confirmationDigest"])


def test_relocation_rejects_conflicting_existing_target(
    tmp_path: Path, monkeypatch
) -> None:
    source, _ = _case_root(tmp_path / "source")
    target = tmp_path / "xdg-data/groundwater-v6/case-assets/yaozhanbao-transfer-case"
    target.parent.mkdir(parents=True)
    shutil.copytree(source / "payload", target / "payload")
    (target / "payload/resources/resource-0.bin").write_bytes(b"conflict")
    paths = MODULE.lifecycle(
        {
            "HOME": str(tmp_path),
            "XDG_DATA_HOME": str(tmp_path / "xdg-data"),
            "XDG_STATE_HOME": str(tmp_path / "xdg-state"),
            "XDG_CONFIG_HOME": str(tmp_path / "xdg-config"),
            "XDG_CACHE_HOME": str(tmp_path / "xdg-cache"),
            "XDG_RUNTIME_DIR": str(tmp_path / "xdg-runtime"),
        }
    )
    monkeypatch.setattr(MODULE, "DESCRIPTOR_ROOT", source)
    monkeypatch.setattr(MODULE, "lifecycle", lambda: paths)
    plan = MODULE.relocation_plan(source, target)

    with pytest.raises(ValueError, match="CASE_RESOURCE_(SIZE|CHECKSUM)_CHANGED"):
        MODULE.apply_relocation(plan, plan["confirmationDigest"])
    assert source.joinpath("payload").is_dir()


def test_relocation_closes_identical_target_and_reuses_stable_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    source, _ = _case_root(tmp_path / "source")
    target = tmp_path / "xdg-data/groundwater-v6/case-assets/yaozhanbao-transfer-case"
    target.parent.mkdir(parents=True)
    shutil.copytree(source / "payload", target / "payload")
    paths = MODULE.lifecycle(
        {
            "HOME": str(tmp_path),
            "XDG_DATA_HOME": str(tmp_path / "xdg-data"),
            "XDG_STATE_HOME": str(tmp_path / "xdg-state"),
            "XDG_CONFIG_HOME": str(tmp_path / "xdg-config"),
            "XDG_CACHE_HOME": str(tmp_path / "xdg-cache"),
            "XDG_RUNTIME_DIR": str(tmp_path / "xdg-runtime"),
        }
    )
    monkeypatch.setattr(MODULE, "DESCRIPTOR_ROOT", source)
    monkeypatch.setattr(MODULE, "lifecycle", lambda: paths)
    plan = MODULE.relocation_plan(source, target)
    receipt = {
        "schemaVersion": "yaozhanbao-case-relocation-receipt.v1",
        "status": "PASS",
        "confirmationDigest": plan["confirmationDigest"],
        "sourceCaseRoot": str(source.absolute()),
        "targetCaseRoot": str(target.absolute()),
        "files": plan["files"],
        "bytes": plan["bytes"],
        "inventorySha256": plan["inventorySha256"],
        "published": True,
        "verifiedAt": "2026-11-12T00:00:00+00:00",
    }
    receipt_path, persisted = MODULE._write_relocation_receipt(paths, receipt)
    assert persisted == receipt

    result = MODULE.apply_relocation(plan, plan["confirmationDigest"])

    assert result["published"] is True
    assert result["verifiedAt"] == receipt["verifiedAt"]
    assert result["receipt"] == str(receipt_path)
    assert not source.joinpath("payload").exists()
    MODULE._load_case_layout(target, source)


def test_verify_only_has_no_network_side_effect(tmp_path: Path, monkeypatch, capsys) -> None:
    case_root, _ = _case_root(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [str(SCRIPT), "--case-root", str(case_root), "--verify-only"],
    )

    monkeypatch.setattr(MODULE, "DESCRIPTOR_ROOT", case_root)
    MODULE.main()

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "verified"
    assert result["resources"] == 19
