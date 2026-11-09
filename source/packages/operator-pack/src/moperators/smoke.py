from __future__ import annotations

import argparse
import hashlib
import json
import resource
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Literal

from moperators.catalog import OperatorCatalog, SmokeInputRef


def bundled_catalog_directory() -> Path:
    return Path(__file__).with_name("catalog_data") / "operators"


def _limits(timeout_seconds: int, memory_bytes: int) -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (timeout_seconds, timeout_seconds))
    resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
    resource.setrlimit(resource.RLIMIT_FSIZE, (memory_bytes, memory_bytes))


def run_smokes(
    catalog_directory: Path, runtime: Literal["native", "qgis", "remote_http"]
) -> tuple[str, ...]:
    catalog = OperatorCatalog.load(catalog_directory)
    selected = tuple(item for item in catalog.operators if item.runtime == runtime)
    if runtime != "remote_http":
        # The remote worker intentionally carries only control-plane
        # dependencies. Importing native operators here would make its build
        # depend on NumPy/GDAL even though remote smoke cases validate contracts
        # and never execute a local implementation.
        from moperators.operators.native import StagedInput

    completed: list[str] = []
    with tempfile.TemporaryDirectory(prefix=f"operator-{runtime}-smoke-") as temporary:
        root = Path(temporary)
        for operator in selected:
            identity = (operator.id, operator.version)
            smoke = catalog.smoke_cases[identity]
            if runtime == "remote_http":
                if set(smoke.inputs) != {port.name for port in operator.inputs}:
                    raise ValueError(f"smoke input ports mismatch: {operator.id}")
                if set(smoke.expected_outputs) != {
                    port.name for port in operator.outputs
                }:
                    raise ValueError(f"smoke output ports mismatch: {operator.id}")
                completed.append(f"{operator.id}@{operator.version}")
                continue
            inputs: dict[str, list[StagedInput]] = {}
            input_directory = root / operator.id / "inputs"
            input_directory.mkdir(parents=True)
            for port, references in smoke.inputs.items():
                refs = references if isinstance(references, list) else [references]
                inputs[port] = []
                for index, reference in enumerate(refs):
                    if not isinstance(reference, SmokeInputRef):
                        raise TypeError(f"invalid smoke input for {operator.id}:{port}")
                    fixture = catalog.fixtures[reference.fixture_id]
                    path = catalog.fixture_root / fixture.path
                    digest = f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
                    if path.stat().st_size != fixture.size_bytes or digest != fixture.sha256:
                        raise ValueError(f"fixture integrity mismatch: {reference.fixture_id}")
                    staged_path = input_directory / f"{port}-{index}"
                    shutil.copyfile(path, staged_path)
                    inputs[port].append(
                        StagedInput(
                            path=staged_path,
                            media_type=fixture.media_type,
                            format_id=fixture.format,
                            metadata={},
                        )
                    )
            output_directory = root / operator.id / "outputs"
            payload_path = root / operator.id / "payload.json"
            result_path = root / operator.id / "result.json"
            payload_path.write_text(
                json.dumps(
                    {
                        "operatorId": operator.id,
                        "operatorVersion": operator.version,
                        "inputs": {
                            port: [
                                {
                                    "path": str(item.path),
                                    "mediaType": item.media_type,
                                    "format": item.format_id,
                                    "metadata": item.metadata,
                                }
                                for item in items
                            ]
                            for port, items in inputs.items()
                        },
                        "parameters": smoke.parameters,
                        "outputDirectory": str(output_directory),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "moperators.runtime.task",
                    str(payload_path),
                    str(result_path),
                ],
                check=True,
                timeout=operator.timeout_seconds,
                preexec_fn=lambda operator=operator: _limits(
                    operator.timeout_seconds, operator.memory_limit_bytes
                ),
            )
            result = json.loads(result_path.read_text(encoding="utf-8"))
            if error := result.get("error"):
                raise ValueError(f"smoke execution failed: {operator.id}:{error['code']}")
            outputs = result["outputs"]
            if set(outputs) != set(smoke.expected_outputs):
                raise ValueError(f"smoke output ports mismatch: {operator.id}")
            for port, expected in smoke.expected_outputs.items():
                actual = outputs[port]
                if actual.get("kind") != expected.kind:
                    raise ValueError(f"smoke output kind mismatch: {operator.id}:{port}")
                if expected.media_type and actual.get("mediaType") != expected.media_type:
                    raise ValueError(f"smoke output media type mismatch: {operator.id}:{port}")
                if expected.format and actual.get("format") != expected.format:
                    raise ValueError(f"smoke output format mismatch: {operator.id}:{port}")
                if expected.kind == "artifact":
                    path = Path(actual["path"])
                    if not path.is_file() or path.is_symlink():
                        raise ValueError(f"smoke artifact is invalid: {operator.id}:{port}")
                elif "value" not in actual:
                    raise ValueError(f"smoke value is missing: {operator.id}:{port}")
            completed.append(f"{operator.id}@{operator.version}")
    return tuple(completed)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--runtime", choices=("native", "qgis", "remote_http"), required=True
    )
    args = parser.parse_args()
    completed = run_smokes(bundled_catalog_directory(), args.runtime)
    print(f"{args.runtime} operator smokes PASS ({len(completed)})")


if __name__ == "__main__":
    main()
