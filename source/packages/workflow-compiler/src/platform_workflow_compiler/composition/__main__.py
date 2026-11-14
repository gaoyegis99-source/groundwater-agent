"""Validate composition files using an explicitly supplied offline catalog."""

from __future__ import annotations

import argparse
import json

from ..validation import WorkflowValidationError
from . import load_json, validate_composition, validate_operator_manifest, validate_vocabulary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", type=argparse.FileType("rb"))
    parser.add_argument("--operator", type=argparse.FileType("rb"), required=True, action="append")
    parser.add_argument("--vocabulary", type=argparse.FileType("rb"), required=True)
    parser.add_argument("--environment", type=argparse.FileType("rb"), required=True)
    args = parser.parse_args()
    try:
        vocabulary = load_json(args.vocabulary.read())
        validate_vocabulary(vocabulary)
        environment = load_json(args.environment.read())
        catalog = {}
        for path in args.operator:
            manifest = load_json(path.read())
            validate_operator_manifest(manifest, vocabulary)
            key = (manifest["id"], manifest["version"])
            if key in catalog:
                raise WorkflowValidationError("COMPOSITION_OPERATOR_DUPLICATE")
            catalog[key] = manifest
        report = validate_composition(
            load_json(args.workflow.read()), catalog, vocabulary, environment_ref=environment,
        )
    except WorkflowValidationError as error:
        print(json.dumps({"status": "INVALID", "code": error.code, "path": error.path}, ensure_ascii=False))
        return 1
    except OSError as error:
        parser.exit(2, f"{error}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "VALIDATED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
