"""Deploy the local assessment service next to the Groundwater research stack."""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
from tempfile import NamedTemporaryFile

SOURCE = Path(__file__).resolve().parents[3]
ROOT = SOURCE.parent
sys.path.insert(0, str(SOURCE))

from scripts.maintenance.project_environment import lifecycle  # noqa: E402

HOST = "groundwater-sustainability.local"
SERVICE = "groundwater-sustainability"
IMAGE = "groundwater-v6/sustainability:0.7.0"
PROJECT = "groundwater-local-sustainability"


def run(*command: str, capture: bool = False) -> str:
    return subprocess.run(
        command, check=True, text=True, stdout=subprocess.PIPE if capture else None
    ).stdout or ""


def docker(*arguments: str, capture: bool = False) -> str:
    return run(str(ROOT / "bin/docker"), *arguments, capture=capture)


def private_write(target: Path, value: bytes, mode: int = 0o600) -> None:
    with NamedTemporaryFile(dir=target.parent, delete=False) as staged:
        staged.write(value)
        path = Path(staged.name)
    path.chmod(mode)
    path.replace(target)


def make_tls(directory: Path, host: str = HOST) -> None:
    cert = directory / "tls.crt"
    key = directory / "tls.key"
    if cert.exists() or key.exists():
        if not cert.is_file() or not key.is_file():
            raise ValueError("LOCAL_SUSTAINABILITY_TLS_INCOMPLETE")
        run("openssl", "x509", "-in", str(cert), "-checkend", "86400", "-noout")
        return
    run(
        "openssl", "req", "-x509", "-newkey", "rsa:3072", "-nodes",
        "-days", "365", "-subj", f"/CN={host}", "-addext", f"subjectAltName=DNS:{host}",
        "-keyout", str(key), "-out", str(cert),
    )
    key.chmod(0o400)
    cert.chmod(0o444)


def update_profile(profile: Path, directory: Path) -> None:
    backup = directory / "previous-profile.env"
    if not backup.exists():
        private_write(backup, profile.read_bytes())
    updates = {
        "GROUNDWATER_SUSTAINABILITY_BASE_URL": f"https://{HOST}:8443",
        "GROUNDWATER_SUSTAINABILITY_ALLOWED_HOST": HOST,
    }
    retained = [
        line for line in profile.read_text().splitlines()
        if line.split("=", 1)[0].strip() not in updates
    ]
    content = "\n".join([*retained, *(f"{key}={value}" for key, value in updates.items())])
    private_write(profile, (content + "\n").encode())


def main() -> None:
    os.umask(0o077)
    paths = lifecycle()
    profile = paths.config / "profiles/research.env"
    secret_dir = paths.config / "secrets/research"
    network = "source-groundwater_v6-egress"
    docker("network", "inspect", network, "--format", "{{.Name}}", capture=True)
    directory = paths.state / "third-party" / SERVICE
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    make_tls(directory)
    # The proxy runs unprivileged inside Docker. The host parent is private.
    (directory / "tls.key").chmod(0o444)
    key = directory / "api-key"
    if not key.exists():
        private_write(key, secrets.token_urlsafe(48).encode(), 0o444)
    nginx = directory / "nginx.conf"
    private_write(nginx, b"""pid /tmp/nginx.pid;
error_log /dev/stderr warn;
events {}
http {
    access_log off;
    client_max_body_size 64m;
    client_body_temp_path /tmp/client;
    proxy_temp_path /tmp/proxy;
    fastcgi_temp_path /tmp/fastcgi;
    uwsgi_temp_path /tmp/uwsgi;
    scgi_temp_path /tmp/scgi;
    server {
        listen 8443 ssl;
        ssl_certificate /tls/tls.crt;
        ssl_certificate_key /tls/tls.key;
        ssl_protocols TLSv1.2 TLSv1.3;
        location / {
            proxy_pass http://assessment:8000;
        }
    }
}
""", 0o444)
    proxy_image = docker(
        "image", "inspect", "source-groundwater-gateway", "--format", "{{.Id}}",
        capture=True,
    ).strip()
    docker(
        "build", "-t", IMAGE, str(ROOT / "third-party-services" / f"{SERVICE}-service")
    )
    service_image = docker("image", "inspect", IMAGE, "--format", "{{.Id}}", capture=True).strip()
    hardening = {
        "read_only": True,
        "security_opt": ["no-new-privileges:true"],
        "tmpfs": ["/tmp:rw,nosuid,nodev,noexec,size=64m"],
        "restart": "unless-stopped",
    }
    compose = {
        "services": {
            "assessment": {
                **hardening,
                "cap_drop": ["ALL"],
                "image": service_image,
                "environment": {"GW_API_KEY_FILE": "/run/secrets/key"},
                "volumes": [
                    f"{key}:/run/secrets/key:ro",
                    "assessment-data:/var/lib/groundwater-sustainability",
                ],
                "networks": ["backend"],
            },
            "tls": {
                **hardening,
                "cap_drop": ["ALL"],
                "user": "101:101",
                "image": proxy_image,
                "healthcheck": {
                    "test": ["CMD", "curl", "--fail", "--silent", "--noproxy", "*",
                             "--cacert", "/tls/tls.crt", "--resolve", f"{HOST}:8443:127.0.0.1",
                             f"https://{HOST}:8443/readyz"],
                    "interval": "3s", "timeout": "2s", "retries": 12,
                },
                "entrypoint": ["nginx", "-c", "/tls/nginx.conf", "-g", "daemon off;"],
                "volumes": [
                    f"{path}:/tls/{path.name}:ro"
                    for path in (nginx, directory / "tls.crt", directory / "tls.key")
                ],
                "depends_on": {"assessment": {"condition": "service_healthy"}},
                "networks": {"backend": {}, "v6": {"aliases": [HOST]}},
            },
        },
        "networks": {"backend": {"internal": True}, "v6": {"external": True, "name": network}},
        "volumes": {"assessment-data": {}},
    }
    descriptor = directory / "compose.json"
    private_write(descriptor, json.dumps(compose, indent=2).encode())
    docker("compose", "-p", PROJECT, "-f", str(descriptor), "up", "-d", "--wait")
    for name, origin in (
        (f"{SERVICE}-api-key", key),
        (f"{SERVICE}-ca-bundle", directory / "tls.crt"),
    ):
        target = secret_dir / name
        backup = directory / f"previous-{name}"
        if target.exists() and not backup.exists():
            shutil.copyfile(target, backup)
            backup.chmod(0o400)
        private_write(target, origin.read_bytes(), 0o400)
    update_profile(profile, directory)
    print(f"{SERVICE} ready at https://{HOST}:8443 ({service_image})")


if __name__ == "__main__":
    main()
