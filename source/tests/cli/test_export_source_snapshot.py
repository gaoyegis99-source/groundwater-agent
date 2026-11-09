import json
from pathlib import Path

from scripts.release import export_source_snapshot as snapshot


def test_explicit_manifest_selects_only_declared_sources(tmp_path: Path) -> None:
    manifest_path = tmp_path / "export-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schemaVersion": "source-export-manifest.v1",
                "releaseLabel": "test",
                "publicApiVersion": "v11",
                "includeExact": [
                    "products/groundwater/product-manifest.json",
                    "products/groundwater/contracts/public-api-v12.json",
                ],
                "includePrefixes": ["packages/", "domains/"],
                "excludeExact": [],
                "excludePrefixes": ["domains/groundwater/3.0.0/"],
                "excludePathSegments": ["artifacts"],
                "excludeSuffixes": [".log"],
            }
        ),
        encoding="utf-8",
    )
    manifest = snapshot._load_manifest(manifest_path)
    assert snapshot._selected("packages/protocol-contracts/pyproject.toml", manifest)
    assert snapshot._selected(
        "domains/groundwater/6.0.0/domain-manifest.json", manifest
    )
    assert snapshot._selected("products/groundwater/contracts/public-api-v12.json", manifest)
    assert not snapshot._selected(
        "domains/groundwater/3.0.0/domain-manifest.json", manifest
    )
    assert not snapshot._selected("packages/example/artifacts/report.json", manifest)
