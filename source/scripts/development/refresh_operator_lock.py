"""Freeze source revisions and the catalog index after reviewing pack source changes."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "packages/operator-pack"
sys.path.insert(0, str(PACK / "src"))
from moperators.catalog import OperatorCatalog  # noqa: E402


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def checksum(value):
    return "sha256:" + hashlib.sha256(value).hexdigest()


def main():
    catalog_root = PACK / "catalog"
    lock_path = catalog_root / "implementation-lock.json"
    old = json.loads(lock_path.read_text())["operators"]
    entries = {}
    for path in sorted((catalog_root / "operators").glob("*/*/manifest.json")):
        manifest = json.loads(path.read_text())
        key = f"{manifest['id']}@{manifest['version']}"
        sources = (
            set(old[key]["sourceFiles"])
            if key in old
            else {
                "src/moperators/operators/native.py",
                "src/moperators/operators/expressions.py",
                "src/moperators/operators/transfer_common.py",
                "src/moperators/operators/transfer_data.py",
                "src/moperators/operators/transfer_spatial.py",
                "src/moperators/operators/gru_attention.py",
            }
        )
        sources.update(
            {
                "src/moperators/runtime/task.py",
                "src/moperators/fixture_inspection.py",
                "src/moperators/static_implementations.py",
                "src/moperators/operators/native.py",
                "src/moperators/operators/expressions.py",
                "src/moperators/operators/transfer_common.py",
                "src/moperators/operators/transfer_data.py",
                "src/moperators/operators/transfer_spatial.py",
                "src/moperators/operators/gru_attention.py",
            }
        )
        if manifest["runtime"] == "native":
            sources.update(
                "src/moperators/operators/" + module + ".py"
                for module in (
                    "analysis",
                    "linear_trend",
                    "mann_kendall",
                    "statistics_pettitt",
                    "seasonal_mann_kendall",
                    "extension_common",
                    "table_document_common_v1",
                )
            )
        if manifest["runtime"] == "remote_http":
            sources = {
                "src/moperators/static_implementations.py",
                "src/moperators/catalog.py",
            }
        digest = hashlib.sha256()
        for relative in sorted(sources):
            digest.update(relative.encode() + b"\0" + (PACK / relative).read_bytes())
        source_digest = "sha256:" + digest.hexdigest()
        entries[key] = {
            "sourceFiles": sorted(sources),
            "sourceDigest": source_digest,
            "implementationRevision": checksum(
                canonical(manifest) + source_digest.encode()
            ),
        }
    lock_path.write_text(
        json.dumps(
            {"schemaVersion": "operator-implementation-lock.v1", "operators": entries},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    catalog = OperatorCatalog.load(catalog_root / "operators")
    index = {
        "schemaVersion": "operator-catalog-index.v1",
        "catalogDigest": catalog.digest,
        "operators": [
            {
                "id": op.id,
                "version": op.version,
                "domain": op.domain,
                "runtime": op.runtime,
                "manifestDigest": catalog.manifest_digests[(op.id, op.version)],
                "smokeDigest": checksum(
                    canonical(
                        json.loads(
                            (
                                catalog_root
                                / "operators"
                                / op.id
                                / op.version
                                / "smoke.json"
                            ).read_text()
                        )
                    )
                ),
            }
            for op in catalog.operators
        ],
    }
    (catalog_root / "index.json").write_text(
        json.dumps(index, indent=2, sort_keys=True) + "\n"
    )
    print(f"Frozen {len(entries)} implementations: {catalog.digest}")


if __name__ == "__main__":
    main()
