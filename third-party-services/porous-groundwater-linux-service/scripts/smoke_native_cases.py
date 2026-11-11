"""Run the shipped v2.6.2 cases with the built Linux binaries and original worker."""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
from pathlib import Path, PurePosixPath
import re
from struct import Struct, unpack_from
import subprocess
import sys
from zipfile import ZipFile

from build_native_solvers import ARCHIVE_SHA256, SOURCES, sha256

CASES = {
    "official-mf2005-twri": "mf2005",
    "official-nwt-pr1a": "mfnwt",
    "guide-mt3d-p01b": "mf2005",
    "idealized-rectangular-site": "mf2005",
}


def _series(path: Path, header: Struct, indices: tuple[int, ...]) -> dict:
    series = {}
    with path.open("rb") as stream:
        while raw := stream.read(header.size):
            _, _, _, time, _, cols, rows, layer = header.unpack(raw)
            if (cols, rows, layer) != (50, 30, 1):
                raise ValueError("RECTANGULAR_REFERENCE_GRID_MISMATCH")
            cells = stream.read(4 * cols * rows)
            if len(cells) != 4 * cols * rows:
                raise ValueError("RECTANGULAR_REFERENCE_TRUNCATED")
            series[time] = tuple(unpack_from("<f", cells, 4 * index)[0] for index in indices)
    return series


def _rectangular_reference(delivery: ZipFile, prefix: str, results: Path) -> dict:
    points = {
        row["name"]: 50 * (int(row["row"]) - 1) + int(row["column"]) - 1
        for row in csv.DictReader(
            delivery.read(prefix + "监测点位.csv").decode("utf-8-sig").splitlines()
        )
    }
    heads = _series(results / "model.hds", Struct("<iiff16siii"), (points["W1"],))
    concentrations = _series(
        results / "transport.ucn", Struct("<iiif16siii"),
        tuple(points[name] for name in ("M1", "M2", "W1")),
    )
    names = ("well_head_m", "M1_concentration_mgL", "M2_concentration_mgL", "well_concentration_mgL")
    differences = {name: 0.0 for name in names}
    rows = list(csv.DictReader(
        delivery.read(prefix + "参考结果_时间序列.csv").decode("utf-8-sig").splitlines()
    ))
    for row in rows:
        time = float(row["time_days"])
        if time not in heads or time not in concentrations:
            raise ValueError(f"RECTANGULAR_REFERENCE_TIME_MISSING:{time}")
        actual = (*heads[time], *concentrations[time])
        for name, value in zip(names, actual, strict=True):
            expected = float(row[name])
            if not math.isclose(value, expected, rel_tol=1e-5, abs_tol=1e-7):
                raise ValueError(f"RECTANGULAR_REFERENCE_MISMATCH:{time}:{name}:{value}:{expected}")
            differences[name] = max(differences[name], abs(value - expected))
    return {"referenceTimes": len(rows), "maxAbsoluteError": differences}


def _single_frame(path: Path, header: Struct, expected: tuple[int, int, int]) -> tuple[float, tuple]:
    data = path.read_bytes()
    _, _, _, time, _, columns, rows, layer = header.unpack_from(data)
    values = Struct(f"<{columns * rows}f")
    if (columns, rows, layer) != expected or len(data) != header.size + values.size:
        raise ValueError(f"REFERENCE_GRID_OR_FRAME_COUNT_MISMATCH:{path.name}")
    return time, values.unpack_from(data, header.size)


def _official_nwt_reference(delivery: ZipFile, root: str, results: Path) -> dict:
    archive = delivery.read(f"{root}/third_party_sources/{SOURCES['mfnwt'][0]}")
    with ZipFile(io.BytesIO(archive)) as upstream:
        listing = upstream.read("MODFLOW-NWT/output_test/Pr1aMFNWT.LST").decode("ascii")
    block = listing.split("HEAD IN LAYER", 1)[1].split("HEAD WILL BE SAVED", 1)[0]
    expected = [float(number) for number in re.findall(r"\d+\.\d+", block)]
    time, heads = _single_frame(results / "Pr1a_MFNWT.hds", Struct("<iiff16siii"), (100, 1, 1))
    if time != 1.0 or len(expected) != 100 or not all(
        math.isclose(value, reference, rel_tol=1e-5, abs_tol=1e-7)
        for value, reference in zip(heads, expected, strict=True)
    ):
        raise ValueError("NWT_OFFICIAL_HEAD_REFERENCE_MISMATCH")
    official = re.search(r"PERCENT DISCREPANCY\s*=\s*([+-]?\d+\.\d+)", listing)
    actual = re.search(r"PERCENT DISCREPANCY\s*=\s*([+-]?\d+\.\d+)",
                       (results / "Pr1a_MFNWT.list").read_text())
    if official is None or actual is None or abs(float(official[1]) - float(actual[1])) > 0.01:
        raise ValueError("NWT_OFFICIAL_BALANCE_REFERENCE_MISMATCH")
    return {"reference": "MODFLOW-NWT/output_test/Pr1aMFNWT.LST", "cells": len(heads),
            "timeDays": time, "maxAbsoluteError": max(abs(a - b) for a, b in zip(heads, expected))}


def _mt3d_guide_reference(results: Path) -> dict:
    head_time, heads = _single_frame(results / "model.hds", Struct("<iiff16siii"), (101, 1, 1))
    time, concentrations = _single_frame(results / "transport.ucn", Struct("<iiif16siii"), (101, 1, 1))
    if time != 2000 or head_time != time or not all(
        math.isclose(head, 60 - index * 0.6, rel_tol=1e-5, abs_tol=1e-7)
        for index, head in enumerate(heads)
    ):
        raise ValueError("MT3D_GUIDE_FLOW_REFERENCE_MISMATCH")
    velocity = 0.24
    dispersion = 2.4
    width = 2 * math.sqrt(dispersion * time)
    reference = [
        0.5 * (math.erfc((x - velocity * time) / width)
               + math.exp(velocity * x / dispersion) * math.erfc((x + velocity * time) / width))
        for x in range(0, 1001, 10)
    ]
    rmse = math.sqrt(math.fsum((value - expected) ** 2 for value, expected in
                               zip(concentrations, reference, strict=True)) / len(reference))
    if not rmse <= 0.04 or not all(0 <= value <= 1.000001 for value in concentrations):
        raise ValueError("MT3D_GUIDE_CONCENTRATION_REFERENCE_MISMATCH")
    return {"reference": "Ogata–Banks continuous 1D concentration profile",
            "timeDays": time, "cells": len(concentrations), "continuousProfileRmse": rmse,
            "continuousProfileRmseLimit": 0.04}


def run_case(delivery: ZipFile, root: str, worker: Path, binaries: Path, case: Path) -> dict:
    case.mkdir()
    project = case / "project"
    project.mkdir()
    prefix = f"{root}/projects/{case.name}/"
    for member in delivery.infolist():
        if member.is_dir() or not member.filename.startswith(prefix):
            continue
        relative = PurePosixPath(member.filename.removeprefix(prefix))
        if (
            relative.parts[:2] != ("models", "base")
            and str(relative) not in {"project.json", "model_definition.json"}
        ):
            continue
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("BUNDLED_CASE_PATH_INVALID")
        target = project.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(delivery.read(member))
    project_file = project / "project.json"
    config = json.loads(project_file.read_bytes())
    config["_projectFile"] = str(project_file)
    config["model"].update(
        sourceDirectory=str(project / "models/base"),
        solverExecutable=str(binaries / CASES[case.name]),
    )
    transport = config.get("transport")
    if transport and transport.get("enabled"):
        transport["executable"] = str(binaries / "mt3dusgs")
    directories = {"run": str(case)}
    for key in ("snapshot", "work", "rawResults", "processedResults", "logs"):
        directory = case / key
        directory.mkdir()
        directories[key] = str(directory)
    request = case / "request.json"
    request.write_text(
        json.dumps(
            {
                "protocolVersion": "1.0",
                "runId": f"native-smoke-{case.name}",
                "project": config,
                "plugin": {"id": "org.groundwater.porous-flow", "version": "1.3.1"},
                "directories": directories,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with (case / "console.log").open("wb") as console:
        subprocess.run(
            [
                sys.executable, "-I", "-B", "-c",
                "import runpy,sys; sys.path.insert(0, sys.argv[1]); sys.argv = sys.argv[2:]; "
                "runpy.run_path(sys.argv[0], run_name='__main__')",
                str(worker.parent / "src"), str(worker), str(request),
            ],
            cwd=case,
            check=True,
            timeout=120,
            stdout=console,
            stderr=subprocess.STDOUT,
        )
    summary = case / "processedResults/summary.json"
    if not summary.is_file():
        raise ValueError(f"SOLVER_SUMMARY_MISSING:{case.name}")
    details = json.loads(summary.read_bytes())
    if details.get("status") != "succeeded" or not details.get("normalTermination"):
        raise ValueError(f"SOLVER_TERMINATION_UNCONFIRMED:{case.name}")
    report = {
        "case": case.name,
        "status": "PASS",
        "summarySha256": f"sha256:{sha256(summary)}",
        "waterBalance": details["waterBalance"],
    }
    if config.get("transport", {}).get("enabled"):
        transport = details["transport"]
        if not transport.get("normalTermination") or not transport.get("executed"):
            raise ValueError(f"TRANSPORT_TERMINATION_UNCONFIRMED:{case.name}")
        mass_file = case / "rawResults/transport.mas"
        last = [line.split() for line in mass_file.read_text().splitlines() if line.strip()][-1]
        mass_balance = [float(value) for value in last[-2:]]
        if len(last) != 9 or not all(math.isfinite(value) for value in mass_balance):
            raise ValueError(f"TRANSPORT_MASS_BALANCE_INVALID:{case.name}")
        if any(abs(value) > 0.01 for value in mass_balance):
            raise ValueError(f"TRANSPORT_MASS_BALANCE_REFERENCE_MISMATCH:{case.name}")
        report["massBalancePercent"] = {
            "totalInOut": mass_balance[0],
            "alternative": mass_balance[1],
        }
    if case.name == "idealized-rectangular-site":
        report["bundledReference"] = _rectangular_reference(delivery, prefix, case / "rawResults")
    if case.name == "official-nwt-pr1a":
        report["bundledReference"] = _official_nwt_reference(delivery, root, case / "rawResults")
    if case.name == "guide-mt3d-p01b":
        report["analyticalReference"] = _mt3d_guide_reference(case / "rawResults")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("build", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--case", choices=CASES, action="append")
    args = parser.parse_args()
    if sha256(args.archive) != ARCHIVE_SHA256:
        raise ValueError("DELIVERY_ARCHIVE_SHA256_MISMATCH")
    args.output.mkdir(mode=0o700)
    output = args.output.resolve()
    binaries = args.build.resolve() / "bin"
    manifest = json.loads((args.build / "solver-manifest.json").read_bytes())
    if (
        manifest.get("sourceArchiveSha256") != f"sha256:{ARCHIVE_SHA256}"
        or set(manifest["binaries"]) != {"mf2005", "mfnwt", "mt3dusgs", "pest"}
    ):
        raise ValueError("SOLVER_MANIFEST_INVALID")
    for name, spec in manifest["binaries"].items():
        if f"sha256:{sha256(binaries / name)}" != spec["sha256"]:
            raise ValueError(f"SOLVER_BINARY_HASH_MISMATCH:{name}")
    with ZipFile(args.archive) as delivery:
        [root] = {member.filename.split("/", 1)[0] for member in delivery.infolist()}
        app_prefix = f"{root}/src/gw_platform/"
        for member in delivery.infolist():
            if not member.filename.startswith(app_prefix) or not member.filename.endswith(".py"):
                continue
            relative = PurePosixPath(member.filename.removeprefix(app_prefix))
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("BUNDLED_APPLICATION_PATH_INVALID")
            target = (output / "src/gw_platform").joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(delivery.read(member))
        package = delivery.read(f"{root}/packages/porous-flow-1.3.1.hmpkg")
        worker = output / "bundled-worker.py"
        with ZipFile(io.BytesIO(package)) as plugin:
            worker.write_bytes(plugin.read("worker/worker.py"))
        reports = []
        for case in args.case or CASES:
            try:
                reports.append(run_case(delivery, root, worker, binaries, output / case))
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                reports.append({"case": case, "status": "FAIL", "error": str(error)})
    passed = all(item["status"] == "PASS" for item in reports)
    report = {"status": "SMOKE_PASSED" if passed else "SMOKE_FAILED", "reports": reports}
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
