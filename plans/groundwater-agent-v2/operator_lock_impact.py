#!/usr/bin/env python3
"""Read-only Phase 1 spike for comparing operator lock and review impact."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import shlex
from collections import Counter
from functools import cache
from importlib.util import resolve_name
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PACK = Path("source/packages/operator-pack")
CATALOG = PACK / "catalog"
EXECUTOR = Path("source/services/executor")
NATIVE = "moperators.operators.native"
DISPATCH = "moperators.static_implementations"
WORKER = "platform_executor_service.worker"
TASK = "moperators.runtime.task"
CHILD = "platform_executor_service.operator_child"
DOCKERFILES = {
    "native": EXECUTOR / "Dockerfile.native",
    "qgis": EXECUTOR / "Dockerfile.qgis",
    "remote_http": EXECUTOR / "Dockerfile.remote-http",
}
PYTHON_ROOTS = (
    PACK / "src",
    EXECUTOR / "src",
    Path("source/packages/protocol-contracts/src"),
    Path("source/packages/workflow-compiler/src"),
    Path("source/services/data/client/src"),
)
PILOTS = (
    "raster.reproject@1.0.0",
    "raster.reproject@1.1.0",
    "vector.spatial_join@1.0.0",
    "groundwater.porous.pest_calibrate@1.0.0",
    "groundwater.porous.pest_calibrate@1.1.0",
)
DEFAULT_CHANGES = (
    PACK / "src/moperators/operators/native.py",
    PACK / "src/moperators/operators/analysis.py",
    PACK / "src/moperators/operators/raster_reference_grid.py",
    PACK / "src/moperators/operators/qgis.py",
    PACK / "src/moperators/static_implementations.py",
    PACK / "src/moperators/runtime/task.py",
    PACK / "src/moperators/catalog.py",
    EXECUTOR / "src/platform_executor_service/remote_http.py",
    EXECUTOR / "src/platform_executor_service/operator_child.py",
    Path("source/environments/native/requirements.lock"),
    Path("source/environments/build/requirements.lock"),
    Path("source/environments/common/requirements.lock"),
    Path("source/system-deps.lock.json"),
    DOCKERFILES["qgis"],
    "image:platform-base-pdal",
    "dependency:system/qgis",
    "remote-service:porous-groundwater",
    "unknown:unresolved-runtime-dependency",
)


def read_json(path):
    return json.loads((ROOT / path).read_bytes())


def lock_hash(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def lock_matches(entry, manifest):
    source_files = entry["sourceFiles"]
    if not source_files or source_files != sorted(set(source_files)):
        return False
    source = b"".join(
        relative.encode() + b"\0" + (ROOT / PACK / relative).read_bytes()
        for relative in source_files
    )
    source_digest = lock_hash(source)
    manifest_bytes = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return (
        entry["sourceDigest"] == source_digest
        and entry["implementationRevision"] == lock_hash(manifest_bytes + source_digest.encode())
    )


def python_modules():
    paths = {}
    for source in PYTHON_ROOTS:
        for path in (ROOT / source).rglob("*.py"):
            parts = path.relative_to(ROOT / source).with_suffix("").parts
            module = ".".join(parts[:-1] if parts[-1] == "__init__" else parts)
            paths[module] = path.relative_to(ROOT)
    return paths


def imported_names(nodes, package):
    for node in nodes:
        if isinstance(node, ast.Import):
            for item in node.names:
                yield item.asname or item.name.partition(".")[0], item.name
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                module = resolve_name("." * node.level + module, package)
            for item in node.names:
                yield item.asname or item.name, f"{module}.{item.name}"


def dispatch_table(tree):
    (mapping,) = (
        node.value
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "IMPLEMENTATIONS"
    )
    if not isinstance(mapping, ast.Dict):
        raise ValueError("expected a literal IMPLEMENTATIONS mapping")
    table = {
        "@".join(ast.literal_eval(key)): value
        for key, value in zip(mapping.keys, mapping.values, strict=True)
    }
    if len(table) != len(mapping.keys):
        raise ValueError("duplicate static dispatch identity")
    return table


def execution_graph(manifests, modules):
    trees = {name: ast.parse((ROOT / path).read_bytes(), str(path)) for name, path in modules.items()}
    mapping = dispatch_table(trees[DISPATCH])
    if mapping.keys() != manifests.keys():
        raise ValueError("static dispatch identities differ from manifest identities")
    dispatch_imports = dict(imported_names(trees[DISPATCH].body, "moperators"))
    wrappers = {node.name: node for node in trees[DISPATCH].body if isinstance(node, ast.FunctionDef)}

    def runner_imports(runner):
        if isinstance(runner, ast.Attribute) and isinstance(runner.value, ast.Name):
            return {dispatch_imports[runner.value.id]}
        function = wrappers[runner.id]
        names = {node.id for node in ast.walk(function) if isinstance(node, ast.Name)}
        imports = {dispatch_imports[name] for name in names & dispatch_imports.keys()}
        return imports | {name for _, name in imported_names(ast.walk(function), "moperators")}

    def local_prefixes(qualified_name):
        parts = qualified_name.split(".")
        prefixes = {".".join(parts[:i]) for i in range(1, len(parts) + 1)}
        return prefixes & modules.keys()

    @cache
    def closure(roots, runner_roots):
        seen = set()
        pending = set().union(*(local_prefixes(root) for root in roots | runner_roots))
        while pending:
            name = pending.pop()
            seen.add(name)
            tree = trees[name]
            package = name if modules[name].name == "__init__.py" else name.rpartition(".")[0]
            # These shared modules also contain deferred dispatch. Each exact binding
            # adds its runner imports and a selected native runner expands all branches.
            shared_dispatch = name == DISPATCH or (name == NATIVE and name not in runner_roots)
            nodes = tree.body if shared_dispatch else ast.walk(tree)
            dependencies = {
                dependency
                for _, target in imported_names(nodes, package)
                for dependency in local_prefixes(target)
            }
            pending.update(dependencies - seen)
        return {str(modules[name]) for name in seen}

    graph = {}
    for key, manifest in manifests.items():
        runner = mapping[key]
        remote = manifest["runtime"] == "remote_http"
        if remote != (ast.unparse(runner) == "_remote_http"):
            raise ValueError(f"unexpected remote dispatch binding for {key}")
        # These entry points cross a Worker lease branch and two `python -m` calls.
        roots = {WORKER} if remote else {WORKER, CHILD, TASK}
        imports = set() if remote else runner_imports(runner)
        runner_roots = set().union(*(local_prefixes(target) for target in imports))
        if not remote and not runner_roots:
            raise ValueError(f"unresolved local runner dependency for {key}")
        graph[key] = (ast.unparse(runner), closure(frozenset(roots), frozenset(runner_roots)))
    return graph


def worker_image_inputs():
    base_images = read_json(Path("source/system-deps.lock.json"))["baseImages"]
    inputs = {}
    for runtime, dockerfile in DOCKERFILES.items():
        paths = {str(dockerfile), "source/.dockerignore", "source/system-deps.lock.json"}
        bases = set()
        artifacts = set()
        stages = set()
        for line in (ROOT / dockerfile).read_text().splitlines():
            command, _, arguments = line.strip().partition(" ")
            if command == "FROM":
                base, *alias = shlex.split(arguments)
                name = base.removeprefix("platform-base-")
                if name == base or name not in base_images:
                    raise ValueError(f"unresolved base image in {dockerfile}: {base}")
                bases.add(base)
                if alias:
                    keyword, stage = alias
                    if keyword.upper() != "AS":
                        raise ValueError(f"unrecognized FROM instruction in {dockerfile}")
                    stages.add(stage)
            if command == "COPY":
                parts = shlex.split(arguments)
                if parts[0] == "--from=platform-deps":
                    artifacts.update(parts[1:-1])
                    for artifact in parts[1:-1]:
                        group, _, name = artifact.partition("/")
                        if group == "python":
                            paths.add(f"source/environments/{name}/requirements.lock")
                elif parts[0].startswith("--from="):
                    if parts[0].removeprefix("--from=") not in stages:
                        raise ValueError(f"unresolved COPY stage in {dockerfile}: {arguments}")
                else:
                    sources = parts[:-1]
                    if not sources or any(not (ROOT / "source" / source).exists() for source in sources):
                        raise ValueError(f"unresolved local COPY in {dockerfile}: {arguments}")
                    paths.update(f"source/{source}" for source in sources)
        inputs[runtime] = {
            "paths": sorted(paths), "bases": sorted(bases), "artifacts": sorted(artifacts)
        }
    return inputs


def impact_summary(keys, manifests):
    return {
        "count": len(keys),
        "byRuntime": dict(sorted(Counter(manifests[key]["runtime"] for key in keys).items())),
        "pilots": [key for key in PILOTS if key in keys],
    }


def report(changes):
    lock = read_json(CATALOG / "implementation-lock.json")["operators"]
    index = read_json(CATALOG / "index.json")["operators"]
    operator_dir = CATALOG / "operators"
    paths = {
        f"{path.parent.parent.name}@{path.parent.name}": path.relative_to(ROOT)
        for path in sorted((ROOT / operator_dir).glob("*/*/manifest.json"))
    }
    manifests = {key: read_json(path) for key, path in paths.items()}
    indexed = {f"{row['id']}@{row['version']}": row["runtime"] for row in index}
    runtimes = {key: row["runtime"] for key, row in manifests.items()}
    if len(indexed) != len(index) or indexed != runtimes or lock.keys() != manifests.keys() or any(
        key != f"{m['id']}@{m['version']}" for key, m in manifests.items()
    ):
        raise ValueError("index, manifest and lock identities differ")
    graph = execution_graph(manifests, python_modules())
    images = worker_image_inputs()
    if set(images) != set(runtimes.values()):
        raise ValueError("manifest runtimes differ from modeled Worker runtimes")

    comparisons = {}
    for path in changes:
        locked = {
            key for key, row in lock.items()
            if path == str(paths[key]) or path in {str(PACK / source) for source in row["sourceFiles"]}
        }
        observed = {key for key, (_, dependencies) in graph.items() if path in dependencies}
        image_runtimes = [
            runtime for runtime, inputs in images.items()
            if any(path == source or path.startswith(source + "/") for source in inputs["paths"])
            or path in {f"image:{base}" for base in inputs["bases"]}
            or path in {f"dependency:{artifact}" for artifact in inputs["artifacts"]}
        ]
        by_image = {key for key, row in manifests.items() if row["runtime"] in image_runtimes}
        by_remote_service = {
            key for key, row in manifests.items()
            if (binding := row.get("remoteBinding")) and path == f"remote-service:{binding['serviceId']}"
        }
        review = locked | observed | by_image | by_remote_service
        comparisons[path] = {
            "currentLock": impact_summary(locked, manifests),
            "observedPython": impact_summary(observed, manifests),
            "workerImageRuntimes": image_runtimes,
            "remoteService": impact_summary(by_remote_service, manifests),
            "unknownFallback": not review,
            "reviewScope": impact_summary(review or manifests.keys(), manifests),
        }

    return {
        "catalog": impact_summary(manifests, manifests),
        "lockMismatches": [key for key, row in lock.items() if not lock_matches(row, manifests[key])],
        "pilots": {
            key: {
                "runtime": manifests[key]["runtime"],
                "dispatch": graph[key][0],
                "remoteBinding": manifests[key].get("remoteBinding"),
                "implementationRevision": lock[key]["implementationRevision"],
                "lockedSourceCount": len(lock[key]["sourceFiles"]),
                "lockedSources": [str(PACK / source) for source in lock[key]["sourceFiles"]],
                "observedPythonSources": sorted(graph[key][1]),
            }
            for key in PILOTS
        },
        "workerImageInputs": images,
        "comparisons": comparisons,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "changes", nargs="*",
        help="Repo-relative files, image:<base>, dependency:<artifact>, or remote-service:<serviceId>",
    )
    args = parser.parse_args()
    result = report(args.changes or [str(path) for path in DEFAULT_CHANGES])
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return int(bool(result["lockMismatches"]))


if __name__ == "__main__":
    raise SystemExit(main())
