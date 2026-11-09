#!/usr/bin/env python3
"""Check source-relative Markdown links, heading anchors and host paths."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
LINK_PATTERN = re.compile(r'\[[^\]]*\]\((?:<([^>]+)>|([^\s)]+))(?:\s+"[^"]*")?\)')
WINDOWS_DRIVE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
HOST_ROOTS = {
    "bin", "boot", "dev", "etc", "home", "lib", "lib64", "media", "mnt", "opt",
    "proc", "root", "run", "sbin", "srv", "sys", "tmp", "usr", "var",
}


def ignored_artifact_target(path: Path) -> bool:
    try:
        relative = path.relative_to(ROOT)
    except ValueError:
        return False
    return bool(relative.parts and relative.parts[0] == ".generated")


def markdown_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", "*.md"],
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
                    not link.scheme and not link.netloc
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
            if (
                not target.is_relative_to(ROOT)
                or not target.exists()
                and not ignored_artifact_target(target)
            ):
                errors.append(f"missing source link: {relative} -> {destination}")
            elif target.is_file() and target.suffix == ".md" and link.fragment:
                if unquote(link.fragment) not in heading_anchors(target):
                    errors.append(f"missing heading anchor: {relative} -> {destination}")


def main() -> int:
    errors: list[str] = []
    check_links(errors)
    for error in errors:
        print(f"DOCUMENTATION_CHECK_FAILED: {error}")
    if errors:
        return 1
    print("DOCUMENTATION_CHECK_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
