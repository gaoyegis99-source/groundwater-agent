from __future__ import annotations

import hashlib
import io
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.maintenance import dependency_materializer


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "dockerfile",
    (
        "services/data/Dockerfile",
        "services/executor/Dockerfile.native",
        "services/executor/Dockerfile.qgis",
    ),
)
def test_offline_system_package_layers_do_not_invoke_apt_network(
    dockerfile: str,
) -> None:
    content = (ROOT / dockerfile).read_text()

    assert "RUN --network=none" in content
    assert "deb [trusted=yes] file:/debs ./" in content
    assert 'dpkg-deb -f "$archive"' in content
    assert "Filename: ./%s" in content
    assert "SHA256: %s" in content
    assert "Dir::Etc::sourcelist=/tmp/locked-debs.list" in content
    assert "Dir::Etc::sourceparts=-" in content
    assert "--no-install-recommends" in content
    assert "--allow-downgrades" in content
    assert 'test -z "$(dpkg --audit)"' in content
    assert "http://" not in content
    assert "https://" not in content


def test_npm_offline_verification_uses_a_disposable_cache_copy(
    tmp_path: Path, monkeypatch
) -> None:
    cache = tmp_path / "canonical-cache"
    cache.mkdir()
    marker = cache / "content"
    marker.write_text("immutable")
    observed: dict[str, Path] = {}

    monkeypatch.setattr(dependency_materializer.shutil, "which", lambda name: name)

    def fake_run(command: list[str], *, cwd: Path | None = None) -> None:
        scratch = Path(command[command.index("--cache") + 1])
        observed["cache"] = scratch
        assert cwd is not None
        assert scratch != cache
        assert (scratch / "content").read_text() == "immutable"
        (scratch / "_logs").mkdir()
        (scratch / "_logs/run.log").write_text("volatile")

    monkeypatch.setattr(dependency_materializer, "_run", fake_run)

    result = dependency_materializer.verify_npm_offline(ROOT, cache)

    assert result["status"] == "PASS"
    assert marker.read_text() == "immutable"
    assert not (cache / "_logs").exists()
    assert not observed["cache"].exists()


def test_npm_receipt_excludes_volatile_client_metadata(tmp_path: Path) -> None:
    (tmp_path / "cache/_logs").mkdir(parents=True)
    (tmp_path / "cache/_logs/run.log").write_text("log")
    (tmp_path / "cache/_cacache").mkdir()
    (tmp_path / "cache/_cacache/_lastverified").write_text("timestamp")
    (tmp_path / "cache/_cacache/content").write_text("package")

    files = dependency_materializer._files(tmp_path, scope="npm")

    assert [item["path"] for item in files] == ["cache/_cacache/content"]


def test_apt_uri_parser_uses_the_local_archive_name(tmp_path: Path) -> None:
    manifest = tmp_path / "uris.txt"
    manifest.write_text(
        "'https://example.test/pkg_1%3a2_amd64.deb' "
        "pkg_1%3a2_amd64.deb 42 MD5Sum:deadbeef\n"
    )

    assert dependency_materializer._apt_uris(manifest) == {
        "pkg_1%3a2_amd64.deb": "https://example.test/pkg_1%3a2_amd64.deb"
    }


def test_qgis_resolution_configures_the_verified_ltr_repository() -> None:
    script = dependency_materializer._repository_setup(
        "qgis", ["ubuntu-noble", "qgis-ltr-noble"]
    )

    assert dependency_materializer.QGIS_ARCHIVE_KEY_URL in script
    assert dependency_materializer.QGIS_ARCHIVE_KEY_FINGERPRINT in script
    assert dependency_materializer.QGIS_LTR_REPOSITORY in script
    assert "Signed-By: /etc/apt/keyrings/qgis-archive-keyring.gpg" in script
    assert (
        "--download-only --print-uris --no-install-recommends install "
        "ca-certificates gnupg wget "
        ">> /out/uris.txt" in script
    )
    assert "apt-get --simulate --no-install-recommends install" in script
    assert "xargs -r -n 1 -P 8 /bin/sh -c" in script
    assert "while [ \"$attempt\" -le 3 ]" in script
    assert "Acquire::Retries=2" in script
    assert "Acquire::http::Timeout=120" in script
    assert "download -- \"$selection\"" in script
    assert "dpkg --unpack \"$@\"" in script
    assert "DEBIAN_FRONTEND=noninteractive dpkg --configure -a" in script
    assert "apt-get install -y" not in script


def test_native_resolution_uses_only_the_declared_ubuntu_components() -> None:
    script = dependency_materializer._repository_setup(
        "native", ["base-image"]
    )

    assert "URIs: http://archive.ubuntu.com/ubuntu" in script
    assert "Suites: noble noble-updates" in script
    assert "URIs: http://security.ubuntu.com/ubuntu" in script
    assert "Suites: noble-security" in script
    assert "Components: main universe" in script
    assert "backports" not in script
    assert "restricted" not in script
    assert "multiverse" not in script


def test_qgis_resolution_rejects_an_unexpected_repository_contract() -> None:
    with pytest.raises(
        dependency_materializer.DependencyRefusal,
        match="SYSTEM_QGIS_REPOSITORY_CONTRACT_INVALID",
    ):
        dependency_materializer._repository_setup("qgis", ["ubuntu-noble"])


def test_resolver_proxy_arguments_do_not_embed_proxy_values() -> None:
    arguments = dependency_materializer._docker_proxy_arguments(
        {
            "HTTP_PROXY": "http://user:secret@proxy.example:8080",
            "HTTPS_PROXY": "",
            "NO_PROXY": "localhost",
        }
    )

    assert arguments == ["--env", "HTTP_PROXY", "--env", "NO_PROXY"]
    assert all("secret" not in argument for argument in arguments)


def test_system_materialization_downloads_validated_packages_concurrently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    packages = [
        {
            "name": f"package-{index}",
            "version": "1",
            "url": f"https://packages.example/package-{index}.deb",
            "sha256": "sha256:" + f"{index:064x}",
            "sizeBytes": index + 1,
        }
        for index in range(4)
    ]
    monkeypatch.setattr(
        dependency_materializer,
        "_system_lock",
        lambda _source: {
            "resolution": {"status": "PASS"},
            "aptProfiles": {"native": {"packages": packages}},
        },
    )
    lock = threading.Lock()
    two_workers_started = threading.Event()
    active = 0
    maximum_active = 0

    def download(url: str, target: Path, expected: str, size: int) -> None:
        nonlocal active, maximum_active
        assert url.startswith("https://packages.example/")
        assert expected.startswith("sha256:")
        assert size > 0
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
            if active >= 2:
                two_workers_started.set()
        assert two_workers_started.wait(timeout=2)
        target.write_text("downloaded")
        with lock:
            active -= 1

    monkeypatch.setattr(dependency_materializer, "_download", download)
    destination = tmp_path / "system"
    destination.mkdir()

    dependency_materializer._system(tmp_path, destination)

    assert maximum_active > 1
    assert sorted(path.name for path in (destination / "native").iterdir()) == [
        f"package-{index}.deb" for index in range(4)
    ]


def test_system_package_download_retries_a_digest_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    content = b"verified package"
    responses = iter((io.BytesIO(b"truncated"), io.BytesIO(content)))
    attempts = 0

    def urlopen(_request, *, timeout: int):
        nonlocal attempts
        assert timeout == 120
        attempts += 1
        return next(responses)

    monkeypatch.setattr(dependency_materializer.urllib.request, "urlopen", urlopen)
    target = tmp_path / "package.deb"

    dependency_materializer._download(
        "https://packages.example/package.deb",
        target,
        "sha256:" + hashlib.sha256(content).hexdigest(),
        len(content),
    )

    assert attempts == 2
    assert target.read_bytes() == content


def test_reuse_scope_copies_only_verified_compatible_content(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    lock_bytes = b'{"lock":"one"}'
    (source / "system-deps.lock.json").write_bytes(lock_bytes)
    cache = tmp_path / "cache"
    candidate_digest = "a" * 64
    candidate = cache / "deps" / f"sha256-{candidate_digest}"
    candidate_scope = candidate / "system"
    candidate_scope.mkdir(parents=True)
    (candidate_scope / "package.deb").write_bytes(b"package")
    files = dependency_materializer._files(candidate_scope, scope="system")
    (candidate / "system.receipt.json").write_text(
        json.dumps(
            {
                "status": "PASS",
                "scope": "system",
                "dependencyDigest": candidate_digest,
                "files": files,
            }
        )
    )
    (candidate / "materialization-manifest.json").write_text(
        json.dumps(
            {
                "status": "PASS",
                "dependencyDigest": candidate_digest,
                "inputs": {
                    "system-deps.lock.json": dependency_materializer.sha256_digest(
                        lock_bytes
                    )
                },
            }
        )
    )
    target = cache / "deps" / f"sha256-{'b' * 64}"
    destination = target / ".system.tmp"
    destination.mkdir(parents=True)

    reused = dependency_materializer._reuse_scope(
        source,
        SimpleNamespace(cache=cache),
        target,
        destination,
        "system",
    )

    assert reused == candidate_digest
    assert (destination / "package.deb").read_bytes() == b"package"

    (source / "system-deps.lock.json").write_text('{"lock":"two"}')
    second_target = cache / "deps" / f"sha256-{'c' * 64}"
    second_destination = second_target / ".system.tmp"
    second_destination.mkdir(parents=True)
    assert (
        dependency_materializer._reuse_scope(
            source,
            SimpleNamespace(cache=cache),
            second_target,
            second_destination,
            "system",
        )
        is None
    )
