"""Activate or roll back the accepted porous release in the local research instance."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

SOURCE = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SOURCE))
sys.path.insert(0, str(SOURCE / "platform/assembler"))

# Direct script execution requires the repository paths above before these imports.
from products.groundwater.scripts.deploy_local_porous import ENVIRONMENT, PROJECT, deploy  # noqa: E402
from products.groundwater.scripts.deploy_local_sustainability import docker, private_write  # noqa: E402
from products.groundwater.scripts.porous_candidate import SERVICE, stage_candidate  # noqa: E402
from products.groundwater.scripts.porous_declarations import ENTRIES  # noqa: E402
from scripts.maintenance.project_environment import lifecycle, paths as project_paths  # noqa: E402
from assembler_publication import current_bundle  # noqa: E402

FILES = {
    "product-manifest.json": SOURCE / "products/groundwater/product-manifest.json",
}


def _restart() -> dict:
    result = subprocess.run(
        [str(SOURCE.parent / "bin/gw"), "dev", "up", "--product", "groundwater"],
        cwd=SOURCE, check=True, text=True, stdout=subprocess.PIPE,
    )
    status = json.loads(result.stdout)
    if status.get("status") != "PASS":
        raise RuntimeError(f"GROUNDWATER_RESEARCH_NOT_READY:{status.get('status')}")
    return status


def _verify(report: dict) -> None:
    workflows = report.get("additionalWorkflows", {})
    transport = report.get("transportChecks", {})
    bridge = report.get("assessmentBridge", {})
    if (
        report.get("status") != "PASS"
        or set(workflows) != {entry["slug"] for entry in ENTRIES}
        or any(row.get("status") != "PASS" for row in workflows.values())
        or transport.get("workerRestart", {}).get("state") != "succeeded"
        or transport.get("cancelled", {}).get("state") != "cancelled"
        or set(bridge) != {"blocked", "prepared", "evaluated"}
        or any(row.get("status") != "PASS" for row in bridge.values())
    ):
        raise ValueError("POROUS_LOCAL_ACCEPTANCE_INCOMPLETE")


def _file_digests() -> dict:
    return {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in FILES.items()}


def activate(image: str, report_path: Path) -> None:
    report = json.loads(report_path.read_bytes())
    _verify(report)
    directory = deploy(image)
    for name, path in FILES.items():
        backup = directory / f"previous-{name}"
        if not backup.exists():
            private_write(backup, path.read_bytes())
    stage_candidate(SOURCE, full=True)
    record = {
        **json.loads((directory / "deployment.json").read_bytes()),
        "domainPackage": json.loads(FILES["product-manifest.json"].read_bytes())["domainPackage"],
        "sourceFiles": _file_digests(), "fieldScientificEquivalence": "pending",
    }
    private_write(directory / "acceptance.json", report_path.read_bytes())
    private_write(directory / "activation.json", json.dumps(record, ensure_ascii=False, indent=2).encode())
    status = _restart()
    record["researchStatus"] = status
    assembly = current_bundle(Path(project_paths()["PLATFORM_GENERATED_ROOT"]), "groundwater")
    record["assemblyDigest"] = f"sha256:{assembly.name}"
    record["assemblyPath"] = str(assembly)
    private_write(directory / "activation.json", json.dumps(record, ensure_ascii=False, indent=2).encode())
    print(json.dumps({"service": SERVICE, "domain": record["domainPackage"], "image": record["serviceImage"]}))


def rollback() -> None:
    paths = lifecycle()
    directory = paths.state / "third-party" / SERVICE
    record = json.loads((directory / "activation.json").read_bytes())
    if record["sourceFiles"] != _file_digests():
        raise ValueError("POROUS_ACTIVATION_FILES_CHANGED")
    for name, path in FILES.items():
        private_write(path, (directory / f"previous-{name}").read_bytes(), 0o644)
    profile = paths.config / "profiles/research.env"
    previous = (directory / "previous-profile.env").read_text().splitlines()
    current = profile.read_text().splitlines()
    retained = [line for line in current if line.partition("=")[0].strip() not in ENVIRONMENT]
    retained.extend(line for line in previous if line.partition("=")[0].strip() in ENVIRONMENT)
    private_write(profile, ("\n".join(retained) + "\n").encode())
    _restart()
    docker("compose", "-p", PROJECT, "-f", str(directory / "compose.json"), "stop")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args()
    if args.rollback:
        rollback()
    else:
        if not args.image or args.report is None:
            parser.error("activation requires --image and --report")
        activate(args.image, args.report)
