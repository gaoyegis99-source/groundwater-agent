from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from email.parser import Parser
from pathlib import Path
import re
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[2]
VERSIONS = {
    "platform-workflow-compiler": "1.0.0",
    "platform-assembler": "7.0.0",
    "platform-executor-service": "7.0.0",
    "platform-embedding-client": "6.0.1",
    "platform-data-service": "3.0.0",
    "platform-data-client": "2.0.0",
    "platform-knowledge-next": "2.0.0",
    "platform-knowledge-client": "2.0.0",
    "platform-operator-pack": "2.0.0",
    "platform-kernel-services": "7.0.0",
    "platform-protocol-contracts": "2.0.0",
    "platform-executor-client": "1.0.0",
    "groundwater-api-client": "1.0.0",
}
DEFAULT_VERSION = "6.0.0"


@dataclass(frozen=True)
class Distribution:
    source_root: Path
    package: str | None


DISTRIBUTIONS = {
    "platform-workflow-compiler": Distribution(
        ROOT / "packages" / "workflow-compiler" / "src", "platform_workflow_compiler"
    ),
    "platform-assembler": Distribution(ROOT / "platform" / "assembler", None),
    "platform-executor-service": Distribution(
        ROOT / "services" / "executor" / "src", "platform_executor_service"
    ),
    "platform-kernel-services": Distribution(
        ROOT / "kernel" / "src", "platform_kernel"
    ),
    "platform-embedding-client": Distribution(
        ROOT / "packages" / "embedding-client" / "src", "platform_embedding_client"
    ),
    "platform-operator-pack": Distribution(
        ROOT / "packages" / "operator-pack" / "src", "moperators"
    ),
    "platform-data-service": Distribution(
        ROOT / "services" / "data" / "src", "platform_data_service"
    ),
    "platform-data-client": Distribution(
        ROOT / "services" / "data" / "client" / "src", "platform_data_client"
    ),
    "platform-knowledge-next": Distribution(
        ROOT / "services" / "knowledge-next" / "src", "platform_knowledge_next"
    ),
    "platform-knowledge-client": Distribution(
        ROOT / "services" / "knowledge-next" / "client" / "src",
        "platform_knowledge_client",
    ),
    "platform-protocol-contracts": Distribution(
        ROOT / "packages" / "protocol-contracts" / "src",
        "platform_protocol_contracts",
    ),
    "platform-executor-client": Distribution(
        ROOT / "services" / "executor" / "client" / "src",
        "platform_executor_client",
    ),
    "groundwater-api-client": Distribution(
        ROOT / "packages" / "groundwater-api-client" / "src",
        "groundwater_api_client",
    ),
}


def _source_files(distribution: str) -> dict[str, Path]:
    definition = DISTRIBUTIONS[distribution]
    if definition.package is None:
        files = {
            path.name: path
            for path in definition.source_root.glob("*.py")
            if path.is_file()
        }
        if distribution == "platform-assembler":
            files[
                "platform_assembler-7.0.0.data/data/share/platform-assembler/"
                "product-ui.v1.schema.json"
            ] = definition.source_root / "product-ui.v1.schema.json"
        return files
    package = definition.source_root / definition.package
    files = {
        path.relative_to(definition.source_root).as_posix(): path
        for path in package.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
        and path.name != ".gitignore"
    }
    if distribution == "platform-operator-pack":
        catalog = ROOT / "packages" / "operator-pack" / "catalog"
        files.update(
            {
                (Path("moperators/catalog_data") / path.relative_to(catalog)).as_posix(): path
                for path in catalog.rglob("*")
                if path.is_file()
            }
        )
    return files


def _normalize_distribution(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def check_wheel(distribution: str, wheel: Path) -> list[str]:
    expected = _source_files(distribution)
    problems: list[str] = []
    with ZipFile(wheel) as archive:
        entries = [item for item in archive.infolist() if not item.is_dir()]
        counts = Counter(item.filename for item in entries)
        duplicates = sorted(name for name, count in counts.items() if count > 1)
        dist_info_roots = {
            item.filename.split("/", 1)[0]
            for item in entries
            if item.filename.split("/", 1)[0].endswith(".dist-info")
        }
        if len(dist_info_roots) != 1:
            problems.append(
                "dist-info: expected one, found "
                + (", ".join(sorted(dist_info_roots)) or "none")
            )
            dist_info = None
        else:
            dist_info = next(iter(dist_info_roots))
            metadata_path = f"{dist_info}/METADATA"
            if metadata_path not in counts:
                problems.append(f"missing: {metadata_path}")
            else:
                metadata = Parser().parsestr(
                    archive.read(metadata_path).decode("utf-8")
                )
                actual_name = metadata.get("Name", "")
                actual_version = metadata.get("Version", "")
                if _normalize_distribution(actual_name) != distribution:
                    problems.append(
                        f"distribution: expected {distribution}, found {actual_name}"
                    )
                expected_version = VERSIONS.get(distribution, DEFAULT_VERSION)
                if actual_version != expected_version:
                    problems.append(
                        f"version: expected {expected_version}, found {actual_version}"
                    )

        actual = {
            name
            for name in counts
            if not name.split("/", 1)[0].endswith(".dist-info")
        }
        changed = sorted(
            name
            for name in actual & expected.keys()
            if archive.read(name) != expected[name].read_bytes()
        )

    for name in sorted(actual - expected.keys()):
        problems.append(f"extra: {name}")
    for name in sorted(expected.keys() - actual):
        problems.append(f"missing: {name}")
    for name in changed:
        problems.append(f"changed: {name}")
    for name in duplicates:
        problems.append(f"duplicate: {name}")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("distribution", choices=sorted(DISTRIBUTIONS))
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()

    problems = check_wheel(args.distribution, args.wheel)
    if problems:
        print("\n".join(problems))
        raise SystemExit(1)
    print(f"Wheel content PASS: {args.distribution}: {args.wheel}")


if __name__ == "__main__":
    main()
