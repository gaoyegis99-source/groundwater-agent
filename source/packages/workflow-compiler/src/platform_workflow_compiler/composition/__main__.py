"""Validate composition files using an explicitly supplied offline catalog."""

from __future__ import annotations

import argparse
import json

from ..validation import WorkflowValidationError
from . import load_json, validate_composition, validate_operator_manifest, validate_vocabulary
from .execution import ArtifactComposition


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", type=argparse.FileType("rb"))
    parser.add_argument("--operator", type=argparse.FileType("rb"), action="append")
    parser.add_argument("--vocabulary", type=argparse.FileType("rb"))
    parser.add_argument("--environment", type=argparse.FileType("rb"))
    parser.add_argument("--executor-config", type=argparse.FileType("rb"))
    parser.add_argument("--project")
    args = parser.parse_args()
    if args.executor_config:
        if not args.project or any((args.operator, args.vocabulary, args.environment)):
            parser.error("--executor-config requires --project and uses the installed catalog")
    elif args.project or not all((args.operator, args.vocabulary, args.environment)):
        parser.error("supply --operator, --vocabulary and --environment or --executor-config with --project")
    try:
        workflow = load_json(args.workflow.read())
        if args.executor_config:
            config = load_json(args.executor_config.read())
            if not isinstance(config.get("workflowComposition"), dict):
                raise WorkflowValidationError("COMPOSITION_CONFIG_INVALID")
            _, report = ArtifactComposition.from_config(config["workflowComposition"]).compile(
                workflow, args.project,
            )
        else:
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
            report = validate_composition(workflow, catalog, vocabulary, environment_ref=environment)
    except WorkflowValidationError as error:
        print(json.dumps({"status": "INVALID", "code": error.code, "path": error.path}, ensure_ascii=False))
        return 1
    except OSError as error:
        parser.exit(2, f"{error}\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "VALIDATED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
