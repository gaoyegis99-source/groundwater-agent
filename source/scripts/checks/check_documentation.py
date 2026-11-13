#!/usr/bin/env python3
"""Check source-relative Markdown links, heading anchors and host paths."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
LINK_PATTERN = re.compile(r'\[[^\]]*\]\((?:<([^>]+)>|([^\s)]+))(?:\s+"[^"]*")?\)')
WINDOWS_DRIVE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
HOST_ROOTS = {
    "bin",
    "boot",
    "dev",
    "etc",
    "home",
    "lib",
    "lib64",
    "media",
    "mnt",
    "opt",
    "proc",
    "root",
    "run",
    "sbin",
    "srv",
    "sys",
    "tmp",
    "usr",
    "var",
}
MANUAL_MODEL_COUNT = re.compile(
    r"(?:[零〇一二两三四五六七八九十百]+|\d+)\s*(?:个|类)?\s*(?:模型|个\s+Executable Model)"
)
LOOPBACK_HTTP_PORT = re.compile(r"http://127\.0\.0\.1:(\d+)")


def ignored_artifact_target(path: Path) -> bool:
    try:
        relative = path.relative_to(ROOT)
    except ValueError:
        return False
    return bool(relative.parts and relative.parts[0] == ".generated")


def markdown_files() -> list[Path]:
    result = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
            "*.md",
        ],
        cwd=ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if result.returncode == 0:
        paths = [
            ROOT / relative
            for relative in sorted(set(result.stdout.split("\0")) - {""})
        ]
    else:
        paths = sorted(ROOT.rglob("*.md"))
    return [path for path in paths if path.is_file()]


def heading_anchors(path: Path) -> set[str]:
    anchors: set[str] = set()
    fence = None
    for line in path.read_text(encoding="utf-8").splitlines():
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            value = marker.group(1)
            if fence is None:
                fence = value
            elif value[0] == fence[0] and len(value) >= len(fence):
                fence = None
        if fence is not None:
            continue
        heading = re.match(r"^ {0,3}#{1,6}\s+(.+?)(?:\s+#+)?\s*$", line)
        if heading:
            slug = re.sub(r"[^\w\-\s]", "", heading.group(1).lower()).replace(" ", "-")
            anchor, suffix = slug, 0
            while anchor in anchors:
                suffix += 1
                anchor = f"{slug}-{suffix}"
            anchors.add(anchor)
    return anchors


def check_links(errors: list[str]) -> None:
    for path in markdown_files():
        relative = path.relative_to(ROOT)
        for match in LINK_PATTERN.finditer(path.read_text(encoding="utf-8")):
            destination = match.group(1) or match.group(2)
            link = urlsplit(destination)
            local_path = unquote(link.path)
            if (
                link.scheme.lower() == "file"
                or WINDOWS_DRIVE_PATH.match(destination)
                or (
                    not link.scheme
                    and not link.netloc
                    and local_path.startswith("/")
                    and local_path.split("/", 2)[1] in HOST_ROOTS
                )
            ):
                errors.append(
                    f"active documentation has clickable host path: {relative} -> {destination}"
                )
                continue
            if link.scheme or link.netloc or link.path.startswith("/"):
                continue
            target = (path.parent / local_path).resolve() if local_path else path
            plan_archive = (ROOT.parent / "plans/README.md").resolve()
            if (
                not target.is_relative_to(ROOT)
                and target != plan_archive
                or not target.exists()
                and not ignored_artifact_target(target)
            ):
                errors.append(f"missing source link: {relative} -> {destination}")
            elif target.is_file() and target.suffix == ".md" and link.fragment:
                if unquote(link.fragment) not in heading_anchors(target):
                    errors.append(
                        f"missing heading anchor: {relative} -> {destination}"
                    )


def _load_json(path: Path, errors: list[str]) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(
            f"cannot load documentation authority {path.relative_to(ROOT)}: {exc}"
        )
        return None
    if not isinstance(value, dict):
        errors.append(
            f"documentation authority is not an object: {path.relative_to(ROOT)}"
        )
        return None
    return value


def _schema_label(schema: object, prefix: str, label: str) -> str | None:
    if not isinstance(schema, str) or not schema.startswith(f"{prefix}.v"):
        return None
    return f"{label} {schema.removeprefix(f'{prefix}.')}"


def _require_doc_text(
    path: Path,
    required: tuple[str, ...],
    errors: list[str],
) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        errors.append(
            f"cannot read current product documentation {path.relative_to(ROOT)}: {exc}"
        )
        return None
    relative = path.relative_to(ROOT)
    for marker in required:
        if marker not in text:
            errors.append(
                f"current product documentation missing {marker!r}: {relative}"
            )
    if MANUAL_MODEL_COUNT.search(text):
        errors.append(
            f"current product documentation hard-codes a model count: {relative}"
        )
    return text


def check_current_product_docs(errors: list[str]) -> None:
    """Keep current-product prose aligned with manifest-owned identities.

    Exact capability counts belong to the generated inventory. These three overview
    pages may explain the product, but must not become another hand-maintained count.
    """

    product = _load_json(ROOT / "products/groundwater/product-manifest.json", errors)
    layout = _load_json(
        ROOT / "scripts/maintenance/bootstrap-assets/project-layout.json", errors
    )
    if product is None or layout is None:
        return

    domain_pin = product.get("domainPackage")
    if not isinstance(domain_pin, dict):
        errors.append("documentation authority product manifest has no domainPackage")
        return
    domain_id = domain_pin.get("domainId")
    domain_version = domain_pin.get("version")
    api_version = product.get("publicApiVersion")
    if not all(
        isinstance(value, str) and value
        for value in (domain_id, domain_version, api_version)
    ):
        errors.append(
            "documentation authority product manifest has invalid current identities"
        )
        return

    domain_root = ROOT / "domains" / domain_id / domain_version
    domain = _load_json(domain_root / "domain-manifest.json", errors)
    if domain is None:
        return

    model_labels: set[str] = set()
    for relative in domain.get("executableModels", []):
        model = _load_json(domain_root / str(relative), errors)
        if model is not None:
            label = _schema_label(
                model.get("schemaVersion"), "executable-model", "Executable Model"
            )
            if label is not None:
                model_labels.add(label)
    workflow_labels: set[str] = set()
    for relative in domain.get("workflowDefinitions", []):
        workflow = _load_json(domain_root / str(relative), errors)
        if workflow is not None:
            label = _schema_label(workflow.get("schemaVersion"), "workflow", "Workflow")
            if label is not None:
                workflow_labels.add(label)
    product_label = _schema_label(
        product.get("schemaVersion"), "product-manifest", "Product Manifest"
    )
    if product_label is None or len(model_labels) != 1 or len(workflow_labels) != 1:
        errors.append(
            "documentation authorities do not expose one current schema identity"
        )
        return
    model_label = next(iter(model_labels))
    workflow_label = next(iter(workflow_labels))

    ports = layout.get("ports")
    research = ports.get("research") if isinstance(ports, dict) else None
    gateway_port = research.get("gateway") if isinstance(research, dict) else None
    if not isinstance(gateway_port, int):
        errors.append("documentation authority layout has no research gateway port")
        return

    inventory_link = "../development/current-capability-inventory.md"
    _require_doc_text(
        ROOT / "docs/architecture/architecture-structure-and-flow.md",
        (product_label, model_label, workflow_label, inventory_link),
        errors,
    )
    domain_docs = _require_doc_text(
        ROOT / "docs/capabilities/domain-packages.md",
        (
            f"# {api_version} 领域包",
            f"`{domain_id}@{domain_version}`",
            model_label,
            workflow_label,
            inventory_link,
        ),
        errors,
    )
    webui = _require_doc_text(
        ROOT / "docs/capabilities/webui.md",
        (
            f"公共 {api_version} 合同",
            f"http://127.0.0.1:{gateway_port}/",
            inventory_link,
        ),
        errors,
    )

    for path, text in (
        (ROOT / "docs/capabilities/domain-packages.md", domain_docs),
        (ROOT / "docs/capabilities/webui.md", webui),
    ):
        if text is not None and "v11" in text:
            errors.append(
                f"current product documentation still describes v11: {path.relative_to(ROOT)}"
            )
    if webui is not None:
        for port in LOOPBACK_HTTP_PORT.findall(webui):
            if int(port) != gateway_port:
                errors.append(
                    "current product documentation uses a stale gateway port: "
                    f"docs/capabilities/webui.md -> {port}"
                )


def main() -> int:
    errors: list[str] = []
    check_links(errors)
    check_current_product_docs(errors)
    for error in errors:
        print(f"DOCUMENTATION_CHECK_FAILED: {error}")
    if errors:
        return 1
    print("DOCUMENTATION_CHECK_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
