from __future__ import annotations

import argparse
import json
from pathlib import Path

from .cwl_export import export_cwl_package, write_cwl_package


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the portable workflow.v6 subset to CWL 1.2")
    parser.add_argument("--workflow", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    def load(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    files = export_cwl_package(
        load(arguments.workflow),
        load(arguments.model),
        load(arguments.registry),
        arguments.catalog,
    )
    write_cwl_package(files, arguments.output)


if __name__ == "__main__":
    main()
