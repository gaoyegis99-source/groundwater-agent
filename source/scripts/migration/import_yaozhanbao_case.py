"""Import the pinned Yaozhanbao transfer case into v11 Data and Knowledge Next.

The source manifest is an existing, independently retained transfer-case receipt.
This command never scans the surrounding archive: it verifies and publishes only
the 19 paths and SHA-256 values named by that manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
import sys

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if __package__ in {None, ""}:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.lib.wire_json import from_wire_json, to_wire_json  # noqa: E402


PROJECT = "groundwater-demo"
DEFAULT_CASE_ROOT = (
    PROJECT_ROOT / "migration-assets" / "yaozhanbao-transfer-case"
)
DEFAULT_MANIFEST = (
    DEFAULT_CASE_ROOT
    / "payload/source-package/original-transfer-20261009-029d28edd3"
    / "prepared/inputs.json"
)
RELOCATION_FILE = "relocation.v1.json"
INTEGRITY_FILE = "integrity.v1.json"
TITLE_PREFIX = "腰站堡标准化迁移输入 "
TAGS = [
    "标准化资料", "腰站堡", "迁移学习案例",
    "yaozhanbao-transfer-case", "case:yaozhanbao-transfer-case",
]
KNOWLEDGE_ACCESSED_AT = "2026-10-13"
KNOWLEDGE_REVIEW_AFTER = "2027-10-13"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_case_path(case_root: Path, relative_path: object) -> Path:
    """Resolve one payload path without permitting links or directory escape."""
    if not isinstance(relative_path, str) or not relative_path:
        raise ValueError("CASE_RELOCATION_PATH_INVALID")
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("CASE_RELOCATION_PATH_ESCAPE")
    if case_root.is_symlink():
        raise ValueError("CASE_ROOT_SYMLINK_INVALID")

    candidate = case_root
    for part in relative.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise ValueError("CASE_PAYLOAD_SYMLINK_INVALID")
    if not candidate.is_file():
        raise FileNotFoundError(candidate)
    try:
        candidate.resolve().relative_to(case_root.resolve())
    except ValueError as error:
        raise ValueError("CASE_RELOCATION_PATH_ESCAPE") from error
    return candidate


def _tree_inventory_sha256(root: Path) -> tuple[int, str]:
    """Return a stable hash of every regular payload file beneath ``root``."""
    if root.is_symlink() or not root.is_dir():
        raise ValueError("CASE_SOURCE_PACKAGE_INVALID")
    rows: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("CASE_PAYLOAD_SYMLINK_INVALID")
        if path.is_file():
            rows.append(
                f"{path.relative_to(root).as_posix()}\t{path.stat().st_size}\t"
                f"{_sha256(path)}\n"
            )
    digest = hashlib.sha256("".join(rows).encode("utf-8")).hexdigest()
    return len(rows), digest


def _load_case_layout(case_root: Path) -> tuple[Path, dict[str, Path]]:
    """Load a tracked relocation map without changing source evidence bytes."""
    if not case_root.is_dir() or case_root.is_symlink():
        raise ValueError("CASE_ROOT_INVALID")
    relocation_path = case_root / RELOCATION_FILE
    integrity_path = case_root / INTEGRITY_FILE
    relocation = json.loads(relocation_path.read_text(encoding="utf-8"))
    integrity = json.loads(integrity_path.read_text(encoding="utf-8"))
    if relocation.get("schema_version") != "yaozhanbao-case-relocation.v1":
        raise ValueError("CASE_RELOCATION_SCHEMA_INVALID")
    if integrity.get("schema_version") != "yaozhanbao-case-integrity.v1":
        raise ValueError("CASE_INTEGRITY_SCHEMA_INVALID")

    manifest_path = _safe_case_path(
        case_root, relocation.get("source_manifest_relative_path")
    )
    manifest_checksum = _sha256(manifest_path)
    expected_manifest_checksum = relocation.get("source_manifest_sha256")
    if manifest_checksum != expected_manifest_checksum:
        raise ValueError("CASE_SOURCE_MANIFEST_CHANGED")
    if integrity.get("source_manifest_sha256") != manifest_checksum:
        raise ValueError("CASE_INTEGRITY_MANIFEST_MISMATCH")

    source_package = integrity.get("source_package")
    if not isinstance(source_package, dict):
        raise ValueError("CASE_SOURCE_PACKAGE_INVALID")
    source_package_path = _safe_case_path(
        case_root, source_package.get("sentinel_relative_path")
    ).parent
    file_count, inventory_checksum = _tree_inventory_sha256(source_package_path)
    if (
        file_count != source_package.get("file_count")
        or inventory_checksum != source_package.get("inventory_sha256")
    ):
        raise ValueError("CASE_SOURCE_PACKAGE_CHANGED")

    resources = relocation.get("resources")
    integrity_resources = integrity.get("resources")
    if not isinstance(resources, dict) or not isinstance(integrity_resources, dict):
        raise ValueError("CASE_RELOCATION_RESOURCES_INVALID")
    if set(resources) != set(integrity_resources):
        raise ValueError("CASE_INTEGRITY_RESOURCES_MISMATCH")

    resolved: dict[str, Path] = {}
    for name, relative_path in resources.items():
        path = _safe_case_path(case_root, relative_path)
        expected = integrity_resources[name]
        if not isinstance(expected, dict):
            raise ValueError("CASE_INTEGRITY_RESOURCES_INVALID")
        if path.stat().st_size != expected.get("size_bytes"):
            raise ValueError(f"CASE_RESOURCE_SIZE_CHANGED:{name}")
        if _sha256(path) != expected.get("sha256"):
            raise ValueError(f"CASE_RESOURCE_CHECKSUM_CHANGED:{name}")
        resolved[name] = path
    return manifest_path, resolved


def _request(client: httpx.Client, method: str, path: str, **kwargs) -> dict:
    if "json" in kwargs and kwargs["json"] is not None:
        kwargs["json"] = to_wire_json(kwargs["json"])
    if "params" in kwargs and isinstance(kwargs["params"], dict):
        kwargs["params"] = to_wire_json(kwargs["params"])
    response = client.request(method, path, **kwargs)
    response.raise_for_status()
    return from_wire_json(response.json())


def _current_knowledge(client: httpx.Client, knowledge_id: str) -> dict | None:
    headers = {"X-Project-ID": PROJECT, "X-Client-Type": "cli"}
    documents = _request(
        client,
        "GET",
        "/api/v12/knowledge/documents",
        headers=headers,
        params={"query": knowledge_id, "limit": 20},
    )
    match = next(
        (
            item
            for item in documents["items"]
            if item["document_id"] == knowledge_id
        ),
        None,
    )
    if match is None:
        return None
    return _request(
        client,
        "GET",
        "/api/v12/knowledge/versions/" + match["current_version"],
        headers=headers,
    )


def _existing_import_result(current: dict, knowledge_id: str) -> dict:
    return {
        "import_id": "existing:" + current["version_id"],
        "state": "SUCCEEDED",
        "result": {
            "versions": [{
                "version_id": current["version_id"],
                "knowledge_id": knowledge_id,
            }],
        },
    }


def _load_and_verify(
    manifest_path: Path, resolved_paths: dict[str, Path] | None = None
) -> tuple[dict, str, dict[str, Path]]:
    content = manifest_path.read_bytes()
    manifest = json.loads(content)
    if manifest.get("schemaVersion") not in {
        "transfer-case-inputs.v1",
        "transfer-case-inputs.v2",
    }:
        raise ValueError("TRANSFER_CASE_MANIFEST_INVALID")
    resources = manifest.get("resources")
    if not isinstance(resources, dict) or len(resources) != 19:
        raise ValueError("TRANSFER_CASE_REQUIRES_19_INPUTS")
    if resolved_paths is not None and set(resolved_paths) != set(resources):
        raise ValueError("CASE_RESOURCE_SET_INVALID")
    paths: dict[str, Path] = {}
    for name, item in resources.items():
        path = (
            resolved_paths[name]
            if resolved_paths is not None
            else Path(item["path"])
        )
        if not path.is_file():
            raise FileNotFoundError(path)
        if path.stat().st_size != item["sizeBytes"]:
            raise ValueError(f"SOURCE_SIZE_CHANGED:{name}")
        if "sha256:" + _sha256(path) != item["checksum"]:
            raise ValueError(f"SOURCE_CHECKSUM_CHANGED:{name}")
        paths[name] = path
    return manifest, hashlib.sha256(content).hexdigest(), paths


def _semantics(name: str, item: dict, manifest: dict) -> dict:
    expected = manifest.get("expected", {})
    limitations = [
        "历史归档和标准化资料仅用于开发验证，未独立认证为当前业务现状。",
        "执行结果不得直接用于现实地下水决策或法定边界认定。",
    ]
    descriptions = {
        "workbook": (
            f"迁移学习案例工作簿：源域 {expected.get('sourceWells')} 井、"
            f"腰站堡目标域 {expected.get('targetWells')} 井、"
            f"{expected.get('months')} 个月。"
        ),
        "boundary": "腰站堡面状边界，供案例空间裁剪与成果组织使用。",
        "imagery": "腰站堡归档 RGB 影像，供案例空间背景处理使用。",
    }
    return {
        "description": descriptions.get(name, f"腰站堡迁移学习案例输入：{name}。"),
        "fields": {},
        "grain": None,
        "time_range": "2018-01/2024-12" if name == "workbook" else None,
        "timezone": None,
        "crs": None,
        "vertical_datum": None,
        "limitations": limitations,
        "source": "pinned-transfer-case-manifest",
    }


def _publish_data(
    client: httpx.Client,
    manifest: dict,
    manifest_checksum: str,
    resolved_paths: dict[str, Path],
) -> tuple[dict, list[dict]]:
    headers = {"X-Project-ID": PROJECT, "X-Client-Type": "cli"}
    body = {
        "idempotency_key": "yaozhanbao-standardized-v2-" + manifest_checksum[:32],
        "outputs": [
            {
                "name": name,
                "title": TITLE_PREFIX + name,
                "format": item["format"],
                "media_type": item["mediaType"],
                "policy": (
                    "FILE_QUERYABLE"
                    if item["format"] in {"csv", "parquet"}
                    else "FILE_ONLY"
                ),
                "tags": [
                    *TAGS,
                    "case-batch:" + manifest_checksum,
                    "input:" + name,
                ],
                "semantics": _semantics(name, item, manifest),
            }
            for name, item in manifest["resources"].items()
        ],
        "input_versions": [],
        "provenance": {
            "purpose": "yaozhanbao-transfer-case-development",
            "source_manifest_sha256": manifest_checksum,
        },
    }
    publication = _request(
        client,
        "POST",
        "/api/v12/data/publications",
        headers=headers,
        json=body,
    )
    publication_id = publication["publication_id"]
    state = publication["state"]
    if state == "OPEN":
        uploaded = set(publication.get("uploads", []))
        for name, item in manifest["resources"].items():
            if name in uploaded:
                continue
            path = resolved_paths[name]
            response = client.put(
                f"/api/v12/data/publications/{publication_id}/items/{name}",
                headers={**headers, "X-Content-SHA256": item["checksum"]},
                content=path.read_bytes(),
            )
            response.raise_for_status()
        _request(
            client,
            "POST",
            f"/api/v12/data/publications/{publication_id}/prepare",
            headers=headers,
        )
        state = "PREPARING"
    deadline = time.monotonic() + 600
    while state == "PREPARING" and time.monotonic() < deadline:
        time.sleep(0.25)
        publication = _request(
            client,
            "GET",
            f"/api/v12/data/publications/{publication_id}",
            headers=headers,
        )
        state = publication["state"]
    if state == "FAILED":
        raise RuntimeError("DATA_PUBLICATION_FAILED:" + json.dumps(publication))
    if state == "READY":
        receipt = _request(
            client,
            "POST",
            f"/api/v12/data/publications/{publication_id}/commit",
            headers=headers,
        )
    elif state == "PUBLISHED":
        receipt = publication["receipt"]
    else:
        raise TimeoutError(f"DATA_PUBLICATION_NOT_READY:{state}")

    outputs = []
    by_name = {item["name"]: item for item in receipt["outputs"]}
    for name, source in manifest["resources"].items():
        published = by_name[name]
        detail = _request(
            client,
            "GET",
            "/api/v12/data/versions/" + published["version_id"],
            headers=headers,
        )
        raw = next(item for item in detail["objects"] if item["role"] == "raw")
        if raw["checksum"] != source["checksum"]:
            raise ValueError(f"PUBLISHED_CHECKSUM_MISMATCH:{name}")
        outputs.append(
            {
                "name": name,
                "dataset_id": published["dataset_id"],
                "version_id": published["version_id"],
                "semantic_id": published["semantic_id"],
                "object_id": raw["object_id"],
                "format": raw["format"],
                "media_type": raw["media_type"],
                "size_bytes": raw["size"],
                "checksum": raw["checksum"],
                "resolved_path": str(resolved_paths[name]),
                "source_path": source["path"],
            }
        )
    return receipt, outputs


def _publish_knowledge(
    client: httpx.Client, outputs: list[dict], manifest_checksum: str
) -> dict:
    headers = {"X-Project-ID": PROJECT, "X-Client-Type": "cli"}
    bindings = [
        {
            "service_binding": "data-primary",
            "project_id": PROJECT,
            "dataset_id": item["dataset_id"],
            "version_id": item["version_id"],
            "semantic_id": item["semantic_id"],
            "field_id": None,
        }
        for item in outputs
    ]
    package = {
        "schema_version": "knowledge-package.v1",
        "project_id": PROJECT,
        "idempotency_key": "yaozhanbao-case-" + manifest_checksum[:32],
        "documents": [
            {
                "knowledge_id": "gw-yaozhanbao-transfer-case",
                "kind": "case",
                "title": "腰站堡地下水迁移学习工作流案例",
                "summary": (
                    "腰站堡、Y01 自然语言请求可命中此案例；案例绑定 19 项"
                    "标准化 Data 版本并经确认后执行迁移学习工作流。"
                ),
                "language": "zh-CN",
                "content_version": "1.0.0",
                "aliases": [
                    "腰站堡迁移学习案例",
                    "腰站堡工作流",
                    "Y01 迁移学习",
                    "腰站堡地下水预测",
                ],
                "sources": [
                    {
                        "source_id": "yaozhanbao-transfer-manifest",
                        "kind": "LOCAL_PINNED_MANIFEST",
                        "title": "腰站堡迁移学习固定输入清单",
                        "publisher": "Groundwater development workspace",
                        "version": manifest_checksum[:16],
                        "accessed_at": KNOWLEDGE_ACCESSED_AT,
                        "review_after": KNOWLEDGE_REVIEW_AFTER,
                        "lifecycle_status": "ACTIVE",
                    }
                ],
                "units": [
                    {
                        "unit_id": "case-overview",
                        "content": (
                            "腰站堡地下水迁移学习案例使用 32 口源域井、14 口"
                            "腰站堡目标域井和 2018-01 至 2024-12 的 84 个月资料。"
                            "案例输入由工作簿、月字段映射、源域与目标域筛选规则、"
                            "标准化与时间窗规则、腰站堡边界、RGB 影像及县界、乡镇界、"
                            "河流、地下水系统、铁路和盆地边界组成，共 19 项。"
                        ),
                        "source_refs": ["yaozhanbao-transfer-manifest"],
                        "locator": {"section": "案例范围"},
                    },
                    {
                        "unit_id": "execution-method",
                        "content": (
                            "自然语言中的‘运行腰站堡迁移学习案例’、‘执行腰站堡工作流’"
                            "或‘运行 Y01’应匹配 groundwater.transfer-learning-analysis。"
                            "系统必须先核验 19 个精确 Data 版本，再生成确认项；用户确认后"
                            "执行源域预训练、目标域微调、留出回测和空间成果整理，并持续"
                            "返回执行状态，完成后返回已发布成果及自然语言解释。"
                        ),
                        "source_refs": ["yaozhanbao-transfer-manifest"],
                        "locator": {"section": "执行方法"},
                    },
                    {
                        "unit_id": "limitations",
                        "content": (
                            "本案例用于开发调试。资料来自历史归档和标准化处理，不能据此"
                            "宣称当前地下水真实状况；输入中的坐标、垂向基准、影像时效和"
                            "背景图层适用范围应以各 Data 版本语义和限制为准。执行结果不能"
                            "直接用于现实决策或法定边界认定。"
                        ),
                        "source_refs": ["yaozhanbao-transfer-manifest"],
                        "locator": {"section": "限制"},
                    },
                ],
                "bindings": bindings,
                "provenance": {
                    "purpose": "development-natural-language-workflow-case",
                    "source_manifest_sha256": manifest_checksum,
                },
            }
        ],
    }
    current = _current_knowledge(client, "gw-yaozhanbao-transfer-case")
    desired = {
        (item["dataset_id"], item["version_id"], item["semantic_id"])
        for item in bindings
    }
    existing = set()
    if current is not None:
        existing = {
            (item["dataset_id"], item["version_id"], item["semantic_id"])
            for item in current["body"].get("bindings", [])
        }
    if current is not None and existing == desired:
        return _existing_import_result(current, "gw-yaozhanbao-transfer-case")
    if current is not None:
        package["idempotency_key"] = (
            "yaozhanbao-case-v2-" + manifest_checksum[:32]
        )
        document = package["documents"][0]
        document["expected_version"] = current["version_id"]
        document["content_version"] = "2.0.0"
    accepted = _request(
        client,
        "POST",
        "/api/v12/knowledge/imports",
        headers=headers,
        json=package,
    )
    import_id = accepted["import_id"]
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        state = _request(
            client,
            "GET",
            "/api/v12/knowledge/imports/" + import_id,
            headers=headers,
        )
        if state["state"] == "SUCCEEDED":
            return state
        if state["state"] == "FAILED":
            raise RuntimeError("KNOWLEDGE_IMPORT_FAILED:" + json.dumps(state))
        time.sleep(0.5)
    raise TimeoutError("KNOWLEDGE_IMPORT_TIMEOUT")


def _publish_general_case_knowledge(
    client: httpx.Client, manifest_checksum: str
) -> dict:
    """Publish the unbound routing summary used before a Data version is selected."""
    headers = {"X-Project-ID": PROJECT, "X-Client-Type": "cli"}
    package = {
        "schema_version": "knowledge-package.v1",
        "project_id": PROJECT,
        "idempotency_key": "yaozhanbao-case-routing-" + manifest_checksum[:32],
        "documents": [
            {
                "knowledge_id": "gw-yaozhanbao-transfer-case-routing",
                "kind": "case",
                "title": "腰站堡迁移学习案例入口与执行说明",
                "summary": (
                    "用于在尚未选择 Data 版本时命中腰站堡、Y01 迁移学习"
                    "自然语言请求，并引导到精确绑定的案例知识。"
                ),
                "language": "zh-CN",
                "content_version": "1.0.0",
                "aliases": [
                    "腰站堡迁移学习案例",
                    "腰站堡工作流",
                    "运行腰站堡案例",
                    "Y01 迁移学习",
                ],
                "sources": [
                    {
                        "source_id": "yaozhanbao-transfer-manifest",
                        "kind": "LOCAL_PINNED_MANIFEST",
                        "title": "腰站堡迁移学习固定输入清单",
                        "publisher": "Groundwater development workspace",
                        "version": manifest_checksum[:16],
                        "accessed_at": KNOWLEDGE_ACCESSED_AT,
                        "review_after": KNOWLEDGE_REVIEW_AFTER,
                        "lifecycle_status": "ACTIVE",
                    }
                ],
                "units": [
                    {
                        "unit_id": "routing",
                        "content": (
                            "用户提出‘运行腰站堡迁移学习案例’、‘执行腰站堡工作流’"
                            "或‘运行 Y01’时，应选择 groundwater.transfer-learning-analysis。"
                            "该案例需要 19 项精确 Data 输入；系统先检索并核验腰站堡"
                            "标准化迁移输入，再生成确认项，确认后执行并返回状态、成果和解释。"
                        ),
                        "source_refs": ["yaozhanbao-transfer-manifest"],
                        "locator": {"section": "自然语言入口"},
                    },
                    {
                        "unit_id": "routing-limitations",
                        "content": (
                            "入口知识只说明如何定位案例，不提供 Data 身份。真正执行必须"
                            "使用与 19 项 Data 版本精确绑定的案例知识并核验版本。历史归档"
                            "和标准化资料用于开发调试，不能宣称为当前真实地下水状况。"
                        ),
                        "source_refs": ["yaozhanbao-transfer-manifest"],
                        "locator": {"section": "入口限制"},
                    },
                ],
                "bindings": [],
                "provenance": {
                    "purpose": "development-natural-language-case-routing",
                    "source_manifest_sha256": manifest_checksum,
                },
            }
        ],
    }
    current = _current_knowledge(
        client, "gw-yaozhanbao-transfer-case-routing"
    )
    if (
        current is not None
        and current["body"].get("provenance", {}).get("source_manifest_sha256")
        == manifest_checksum
    ):
        return _existing_import_result(
            current, "gw-yaozhanbao-transfer-case-routing"
        )
    accepted = _request(
        client,
        "POST",
        "/api/v12/knowledge/imports",
        headers=headers,
        json=package,
    )
    import_id = accepted["import_id"]
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        state = _request(
            client,
            "GET",
            "/api/v12/knowledge/imports/" + import_id,
            headers=headers,
        )
        if state["state"] == "SUCCEEDED":
            return state
        if state["state"] == "FAILED":
            raise RuntimeError("KNOWLEDGE_IMPORT_FAILED:" + json.dumps(state))
        time.sleep(0.5)
    raise TimeoutError("KNOWLEDGE_IMPORT_TIMEOUT")


def run(
    gateway: str,
    manifest_path: Path,
    receipt_path: Path,
    resolved_paths: dict[str, Path] | None = None,
) -> dict:
    manifest, manifest_checksum, verified_paths = _load_and_verify(
        manifest_path, resolved_paths
    )
    with httpx.Client(base_url=gateway.rstrip("/"), timeout=300, trust_env=False) as client:
        data_receipt, outputs = _publish_data(
            client, manifest, manifest_checksum, verified_paths
        )
        bound_knowledge = _publish_knowledge(client, outputs, manifest_checksum)
        general_knowledge = _publish_general_case_knowledge(client, manifest_checksum)
    knowledge_versions = [
        *bound_knowledge["result"]["versions"],
        *general_knowledge["result"]["versions"],
    ]
    result = {
        "schema_version": "yaozhanbao-v11-import-receipt.v1",
        "project_id": PROJECT,
        "source_manifest": str(manifest_path),
        "source_manifest_sha256": manifest_checksum,
        "source_files_verified": len(outputs),
        "expected": manifest.get("expected", {}),
        "data_publication": data_receipt,
        "data_versions": outputs,
        "knowledge_import_id": bound_knowledge["import_id"],
        "knowledge_import_ids": [
            bound_knowledge["import_id"], general_knowledge["import_id"]
        ],
        "knowledge_versions": knowledge_versions,
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", default="http://127.0.0.1:28087")
    parser.add_argument(
        "--case-root",
        type=Path,
        default=DEFAULT_CASE_ROOT,
        help="V6-local relocation root; ignored when --manifest is supplied.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        help="Legacy direct manifest path. Its resource paths are used as written.",
    )
    parser.add_argument("--receipt", type=Path)
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Validate the local case payload without contacting Data or Knowledge.",
    )
    args = parser.parse_args()
    if args.manifest is not None:
        manifest_path = args.manifest
        resolved_paths = None
    else:
        manifest_path, resolved_paths = _load_case_layout(args.case_root)

    manifest, manifest_checksum, verified_paths = _load_and_verify(
        manifest_path, resolved_paths
    )
    if args.verify_only:
        print(
            json.dumps(
                {
                    "status": "verified",
                    "source_manifest": str(manifest_path),
                    "source_manifest_sha256": manifest_checksum,
                    "resources": len(verified_paths),
                },
                ensure_ascii=False,
            )
        )
        return
    if args.receipt is None:
        parser.error("--receipt is required unless --verify-only is used")
    result = run(args.gateway, manifest_path, args.receipt, verified_paths)
    print(
        json.dumps(
            {
                "publication_id": result["data_publication"]["publication_id"],
                "data_versions": len(result["data_versions"]),
                "knowledge_versions": len(result["knowledge_versions"]),
                "receipt": str(args.receipt),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
