"""Build Linux solvers from the exact v2.6.2 upstream source archives."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import tarfile
from zipfile import ZipFile

ARCHIVE_SHA256 = "037c93c8d057d75090f1587bec14e4254c41b854b5bdb33b1dc79f20227eb719"
SOURCES = {
    "mf2005": ("modflow-2005-1.12.00.zip", "1.12.00", "MF2005.1_12u",
               "e5188ae499bc65d124983d9f295f01ee4257543849083878e38dfe8dca39f0ad"),
    "mfnwt": ("modflow-nwt-1.3.0.zip", "1.3.0", "MODFLOW-NWT",
              "65241180e5a0754688d2952f17bc0ae8953fdaa53da2122632fcf9642d6e20dc"),
    "mt3dusgs": ("mt3d-usgs-1.1.0.zip", "1.1.0", "mt3dusgs1.1.0",
                 "1aa8cb45e1776a08c6bea5adcb5935677889c63de17dfcb63dde7120707e2891"),
    "pest": ("pest-18.8-source.tar.zip", "18.8", "pest_source",
             "64f0f631d3fb9bdf522a9ab52f8eff915fc03015b18da193201605a1797b98c8"),
}
BASE_FLAGS = ["-O2", "-fno-fast-math", "-frecursive", "-std=legacy"]
FORTRAN_EXTENSIONS = {".f", ".for", ".f90", ".fpp"}
MODULE = re.compile(r"(?im)^\s*module\s+(?!procedure\b|subroutine\b|function\b)(\w+)")
USES = re.compile(r"(?im)^\s*use(?:\s*,\s*\w+\s*::|\s*::|\s+)\s*(\w+)")


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(directory: Path, *command: str) -> None:
    subprocess.run(command, cwd=directory, check=True)


def unpack_sources(source: ZipFile, root: str, target: Path, solver: str) -> None:
    archive_name, _, prefix, expected_hash = SOURCES[solver]
    content = source.read(f"{root}/third_party_sources/{archive_name}")
    if hashlib.sha256(content).hexdigest() != expected_hash:
        raise ValueError(f"SOLVER_SOURCE_HASH_MISMATCH:{solver}")
    with ZipFile(io.BytesIO(content)) as nested:
        if solver == "pest":
            [member] = nested.namelist()
            with tarfile.open(fileobj=io.BytesIO(nested.read(member))) as tar:
                for item in tar:
                    name = PurePosixPath(item.name)
                    if item.isfile() and name.parent == PurePosixPath(prefix):
                        with tar.extractfile(item) as stream:
                            (target / name.name).write_bytes(stream.read())
            return
        for item in nested.infolist():
            name = PurePosixPath(item.filename)
            if not item.is_dir() and name.parent == PurePosixPath(prefix, "src"):
                (target / name.name).write_bytes(nested.read(item))
        if solver == "mf2005":
            (target / "upstream.mk").write_bytes(nested.read(f"{prefix}/make/makefile"))


def compile_fortran(
    source: Path, output: Path, compiler: str, flags: list[str], excluded: tuple[str, ...]
) -> None:
    files = sorted(
        path for path in source.iterdir()
        if path.suffix.lower() in FORTRAN_EXTENSIONS and path.name not in excluded
    )
    modules = {}
    uses = {}
    for path in files:
        code = path.read_text(encoding="latin-1").lower()
        uses[path] = set(USES.findall(code))
        for name in MODULE.findall(code):
            modules[name] = path
    dependencies = {
        path: {modules[name] for name in uses[path] if name in modules} - {path}
        for path in files
    }
    objects = []
    compiled = set()
    with ThreadPoolExecutor(max_workers=4) as pool:
        while dependencies:
            ready = [path for path in dependencies if dependencies[path] <= compiled]
            if not ready:
                raise ValueError(f"FORTRAN_MODULE_CYCLE:{sorted(path.name for path in dependencies)}")
            jobs = {}
            for path in ready:
                obj = path.with_suffix(".o")
                preprocessing = ["-cpp", "-ffixed-form"] if path.suffix == ".fpp" else []
                jobs[path] = pool.submit(
                    run, source, compiler, *flags, *preprocessing, "-c", path.name, "-o", obj.name
                )
                objects.append(obj.name)
            for path, job in jobs.items():
                job.result()
                compiled.add(path)
                del dependencies[path]
    run(source, compiler, *flags, *objects, "-o", str(output))


def compile_solver(
    solver: str, directory: Path, target: Path, compiler: str, flags: list[str]
) -> dict:
    source_digest = hashlib.sha256()
    for path in sorted(directory.iterdir()):
        if path.is_file():
            source_digest.update(path.name.encode() + b"\0" + bytes.fromhex(sha256(path)))
    # The NWT main program does not call the legacy restart routines. The
    # archive ships this unused file without the openspec.inc it requires.
    excluded = ("Irestart.f",) if solver == "mfnwt" else ()
    if solver == "mf2005":
        run(directory, "make", "-f", "upstream.mk", "SOURCEDIR1=.",
            f"F90={compiler}", "F90FLAGS=" + " ".join(flags), "all")
        shutil.copyfile(directory / solver, target)
    elif solver == "pest":
        run(directory, "make", f"F90={compiler}", "cppp")
        run(directory, "make", "-f", "pest.mak", f"F90={compiler}", f"LD={compiler}",
            "FFLAGS=-c -static " + " ".join(flags), "pest")
        shutil.copyfile(directory / solver, target)
    else:
        compile_fortran(directory, target, compiler, flags, excluded)
    with target.open("rb") as stream:
        if stream.read(4) != b"\x7fELF":
            raise ValueError(f"SOLVER_ELF_REQUIRED:{solver}")
    target.chmod(0o555)
    _, version, _, digest = SOURCES[solver]
    record = {
        "sourceArchiveSha256": f"sha256:{digest}",
        "sourceTreeSha256": f"sha256:{source_digest.hexdigest()}",
        "declaredVersion": version,
        "excludedFiles": list(excluded),
        "sha256": f"sha256:{sha256(target)}",
        "sizeBytes": target.stat().st_size,
    }
    if solver == "pest":
        source_version = re.search(
            r"(?i)aversion\s*=\s*'([0-9.]+)'",
            (directory / "version.inc").read_text(encoding="ascii"),
        )
        probe = subprocess.run(
            [str(target)], cwd=directory, stdin=subprocess.DEVNULL, check=False,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=10,
        )
        observed_version = re.search(r"PEST Version (\d+(?:\.\d+)+)", probe.stdout)
        if not source_version or not observed_version or source_version[1] != observed_version[1]:
            raise ValueError("PEST_RUNTIME_VERSION_MISMATCH")
        record["observedVersion"] = observed_version[1]
    return record


def build(archive: Path, output: Path, compiler: str) -> None:
    recipe_digest = sha256(Path(__file__))
    if (platform.system(), platform.machine()) != ("Linux", "x86_64"):
        raise ValueError("SOLVER_BUILD_REQUIRES_LINUX_AMD64")
    if sha256(archive) != ARCHIVE_SHA256:
        raise ValueError("DELIVERY_ARCHIVE_SHA256_MISMATCH")
    if output.is_symlink() or not output.is_dir() or any(output.iterdir()):
        raise ValueError("BUILD_DIRECTORY_MUST_BE_EMPTY")
    output.chmod(0o700)
    compiler_label = subprocess.check_output([compiler, "--version"], text=True).splitlines()[0]
    major = int(subprocess.check_output([compiler, "-dumpversion"], text=True).split(".")[0])
    flags = [*BASE_FLAGS, *(["-fallow-argument-mismatch"] if major >= 10 else [])]
    binaries = output / "bin"
    binaries.mkdir()
    with ZipFile(archive) as source:
        [root] = {item.filename.split("/", 1)[0] for item in source.infolist()}
        for solver in SOURCES:
            directory = output / solver / "src"
            directory.mkdir(parents=True)
            unpack_sources(source, root, directory, solver)
    with ThreadPoolExecutor(max_workers=len(SOURCES)) as pool:
        jobs = {
            solver: pool.submit(
                compile_solver, solver, output / solver / "src", binaries / solver, compiler, flags
            )
            for solver in SOURCES
        }
        records = {solver: job.result() for solver, job in jobs.items()}
    manifest = {
        "schemaVersion": "groundwater-solver-manifest.v2",
        "sourceArchiveSha256": f"sha256:{ARCHIVE_SHA256}",
        "platform": "linux/amd64",
        "compiler": compiler_label,
        "defaultFortranFlags": flags,
        "buildRecipeSha256": f"sha256:{recipe_digest}",
        "binaries": records,
    }
    (output / "solver-manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("delivery_zip", type=Path)
    parser.add_argument("private_output_root", type=Path)
    parser.add_argument("--compiler", default="gfortran")
    args = parser.parse_args()
    build(args.delivery_zip.resolve(strict=True), args.private_output_root.absolute(), args.compiler)
