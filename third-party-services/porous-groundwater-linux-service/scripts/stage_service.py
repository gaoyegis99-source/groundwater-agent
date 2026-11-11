"""Stage a private service image context and the bundled rectangular fixture."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
from zipfile import ZIP_DEFLATED, ZipFile

from build_native_solvers import ARCHIVE_SHA256, SOURCES, sha256
from patch_transport_worker import patch_worker

SERVICE = Path(__file__).resolve().parents[1]
ASSESSMENT = SERVICE.parent / "groundwater-sustainability-service"
NATIVE_LOCK = SERVICE.parents[1] / "source/environments/native/requirements.lock"
SCIENCE_PACKAGES = {
    "affine", "attrs", "certifi", "click-plugins", "cligj", "filelock", "fsspec",
    "jinja2", "markupsafe", "mpmath", "networkx", "numpy", "pyparsing", "rasterio",
    "scipy", "setuptools", "sympy", "torch",
}


def _stage_wheels(image: Path, wheelhouse: Path) -> None:
    wheels = image / "wheels"
    wheels.mkdir()
    blocks = re.split(r"(?m)(?=^[A-Za-z0-9_-]+==)", NATIVE_LOCK.read_text())
    entries = {block.partition("==")[0]: block for block in blocks[1:]}
    requirements = []
    for name in sorted(SCIENCE_PACKAGES):
        block = entries[name]
        digest_pattern = set(re.findall(r"--hash=sha256:([a-f0-9]{64})", block))
        prefix = name.replace("-", "_").lower() + "-"
        matches = [wheel for wheel in wheelhouse.glob("*.whl")
                   if wheel.name.lower().startswith(prefix) and sha256(wheel) in digest_pattern]
        if len(matches) != 1:
            raise ValueError(f"SCIENCE_WHEEL_NOT_LOCKED:{name}")
        [wheel] = matches
        shutil.copyfile(wheel, wheels / wheel.name)
        requirements.append(f"{block.split()[0]} --hash=sha256:{sha256(wheel)}")
    (image / "requirements-science.lock").write_text("\n".join(requirements) + "\n")


def _portable(value):
    if isinstance(value, dict):
        return {name: "" if name == "executable" else _portable(item) for name, item in value.items()}
    if isinstance(value, list):
        return [_portable(item) for item in value]
    return value


def stage(archive: Path, build: Path, output: Path, libraries: dict[str, Path], wheelhouse: Path, afc_build: Path) -> None:
    if sha256(archive) != ARCHIVE_SHA256:
        raise ValueError("DELIVERY_ARCHIVE_SHA256_MISMATCH")
    manifest = json.loads((build / "solver-manifest.json").read_bytes())
    if (
        manifest["sourceArchiveSha256"] != f"sha256:{ARCHIVE_SHA256}"
        or manifest["platform"] != "linux/amd64"
        or set(manifest["binaries"]) != set(SOURCES)
    ):
        raise ValueError("SOLVER_MANIFEST_INVALID")
    for name, record in manifest["binaries"].items():
        if (
            record["sha256"] != f"sha256:{sha256(build / 'bin' / name)}"
            or record["sourceArchiveSha256"] != f"sha256:{SOURCES[name][3]}"
        ):
            raise ValueError(f"SOLVER_HASH_MISMATCH:{name}")
    output.mkdir(mode=0o700)
    image = output / "image"
    image.mkdir()
    vendor = image / "vendor"
    (vendor / "bin").mkdir(parents=True)
    (vendor / "lib").mkdir()
    (vendor / "template").mkdir()
    (vendor / "template/service_inventory.json").write_text("{}", encoding="ascii")
    for name in SOURCES:
        shutil.copyfile(build / "bin" / name, vendor / "bin" / name)
        (vendor / "bin" / name).chmod(0o555)
    afc = json.loads((afc_build / "afc-manifest.json").read_bytes())
    if (afc["sha256"] != f"sha256:{sha256(afc_build / 'bin/mt3dusgs-afc')}"
        or afc["sourceArchiveSha256"] != f"sha256:{SOURCES['mt3dusgs'][3]}"
        or afc.get("numericalMethod") != "conservative-afc.v1"):
        raise ValueError("AFC_SOLVER_MANIFEST_MISMATCH")
    shutil.copyfile(afc_build / "bin/mt3dusgs-afc", vendor / "bin/mt3dusgs-afc")
    (vendor / "bin/mt3dusgs-afc").chmod(0o555)
    manifest["supplementalSolvers"] = {"mt3dusgs-afc": afc}
    for name, path in libraries.items():
        shutil.copyfile(path, vendor / "lib" / name)
    manifest["runtimeLibraries"] = {
        name: f"sha256:{sha256(path)}" for name, path in libraries.items()
    }
    with ZipFile(archive) as delivery:
        [root] = {info.filename.split("/", 1)[0] for info in delivery.infolist()}
        prefix = f"{root}/src/gw_platform/"
        for member in delivery.infolist():
            if not member.filename.startswith(prefix) or not member.filename.endswith(".py"):
                continue
            path = PurePosixPath(member.filename.removeprefix(prefix))
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("VENDOR_PATH_INVALID")
            target = (vendor / "src/gw_platform").joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(delivery.read(member))
        with ZipFile(io.BytesIO(delivery.read(f"{root}/packages/porous-flow-1.3.1.hmpkg"))) as plugin:
            (vendor / "worker.py").write_bytes(plugin.read("worker/worker.py"))
        definition = json.loads(delivery.read(
            f"{root}/projects/idealized-rectangular-site/model_definition.json"
        ))
        with ZipFile(output / "rectangular-project.zip", "x", compression=ZIP_DEFLATED) as fixture:
            fixture.writestr("model_definition.json", json.dumps(_portable(definition), ensure_ascii=False))
    inventory = hashlib.sha256()
    for path in sorted((vendor / "src").rglob("*.py")):
        inventory.update(path.relative_to(vendor).as_posix().encode() + b"\0" + path.read_bytes())
    manifest["upstreamWorkerSha256"] = f"sha256:{sha256(vendor / 'worker.py')}"
    manifest["servicePatches"] = [patch_worker(vendor / "worker.py")]
    manifest["upstreamPythonSourceTreeSha256"] = f"sha256:{inventory.hexdigest()}"
    (vendor / "solver-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    shutil.copyfile(SERVICE / "Dockerfile.service", image / "Dockerfile")
    shutil.copyfile(ASSESSMENT / "requirements.lock", image / "requirements.lock")
    _stage_wheels(image, wheelhouse)
    shutil.copytree(SERVICE / "porous_remote", image / "porous_remote",
                    ignore=shutil.ignore_patterns("__pycache__"))
    for file in ("uploads.py", "errors.py"):
        shutil.copyfile(ASSESSMENT / "app" / file, image / "porous_remote" / file)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("build", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--libquadmath", type=Path, required=True)
    parser.add_argument("--libexpat", type=Path, required=True)
    parser.add_argument("--libgfortran", type=Path)
    parser.add_argument("--native-wheelhouse", type=Path, required=True)
    parser.add_argument("--afc-build", type=Path, required=True)
    args = parser.parse_args()
    runtime = {
        "libquadmath.so.0": args.libquadmath,
        "libexpat.so.1": args.libexpat,
    }
    if args.libgfortran is not None:
        runtime["libgfortran.so.5"] = args.libgfortran
    stage(args.archive, args.build, args.output, runtime, args.native_wheelhouse, args.afc_build)
