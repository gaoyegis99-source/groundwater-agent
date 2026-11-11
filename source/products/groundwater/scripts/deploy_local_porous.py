"""Deploy the pinned local porous service and prepare its research binding."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import sys

SOURCE = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SOURCE))
sys.path.insert(0, str(SOURCE / "platform/assembler"))

# Direct script execution requires the repository paths above before these imports.
from products.groundwater.scripts.deploy_local_sustainability import docker, make_tls, private_write  # noqa: E402
from products.groundwater.scripts.porous_candidate import HOST, SERVICE  # noqa: E402
from scripts.maintenance.project_environment import lifecycle  # noqa: E402

PROJECT = "groundwater-local-porous"
NETWORK = "source-groundwater_v6-egress"
ENVIRONMENT = {
    "POROUS_GROUNDWATER_BASE_URL": f"https://{HOST}:8443",
    "POROUS_GROUNDWATER_ALLOWED_HOST": HOST,
}

NGINX = b"""pid /tmp/nginx.pid;
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
        listen 127.0.0.1:8081;
        location = /readyz { proxy_pass http://porous-backend:8000; }
    }
    server {
        listen 8443 ssl;
        ssl_certificate /tls/tls.crt;
        ssl_certificate_key /tls/tls.key;
        ssl_protocols TLSv1.2 TLSv1.3;
        location / { proxy_pass http://porous-backend:8000; }
    }
}
"""


def deploy(image: str, *, device: str = "cpu") -> Path:
    os.umask(0o077)
    paths = lifecycle()
    directory = paths.state / "third-party" / SERVICE
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    docker("network", "inspect", NETWORK, "--format", "{{.Name}}", capture=True)
    make_tls(directory, HOST)
    certificate, tls_key = directory / "tls.crt", directory / "tls.key"
    tls_key.chmod(0o444)
    keys = {name: directory / name for name in ("api-key", "package-key")}
    for key in keys.values():
        if not key.exists():
            private_write(key, secrets.token_urlsafe(48).encode(), 0o444)
    nginx = directory / "nginx.conf"
    private_write(nginx, NGINX, 0o444)
    backend = docker("image", "inspect", image, "--format", "{{.Id}}", capture=True).strip()
    proxy = docker(
        "image", "inspect", "source-groundwater-gateway:latest", "--format", "{{.Id}}",
        capture=True,
    ).strip()
    hardening = {
        "read_only": True, "security_opt": ["no-new-privileges:true"], "cap_drop": ["ALL"],
        "tmpfs": ["/tmp:rw,nosuid,nodev,noexec,size=64m"], "restart": "unless-stopped",
        "pull_policy": "never",
    }
    compose = {
        "services": {
            "porous-backend": {
                **hardening, "image": backend, "init": True, "networks": ["backend"],
                "environment": {
                    "POROUS_API_KEY_FILE": "/run/secrets/api-key",
                    "POROUS_PACKAGE_KEY_FILE": "/run/secrets/package-key",
                    "POROUS_DEVICE": device,
                    "CUBLAS_WORKSPACE_CONFIG": ":4096:8",
                },
                "volumes": [
                    *(f"{file}:/run/secrets/{name}:ro" for name, file in keys.items()),
                    "porous-data:/var/lib/porous-groundwater",
                ],
            },
            "porous-tls": {
                **hardening, "image": proxy, "user": "101:101",
                "entrypoint": ["nginx", "-c", "/tls/nginx.conf", "-g", "daemon off;"],
                "volumes": [f"{path}:/tls/{path.name}:ro" for path in (nginx, certificate, tls_key)],
                "depends_on": {"porous-backend": {"condition": "service_healthy", "restart": True}},
                "networks": {"backend": {}, "v6": {"aliases": [HOST]}},
                "healthcheck": {
                    "test": ["CMD", "wget", "-qO", "/dev/null", "http://127.0.0.1:8081/readyz"],
                    "interval": "3s", "timeout": "2s", "retries": 12,
                },
            },
        },
        "networks": {"backend": {"internal": True}, "v6": {"external": True, "name": NETWORK}},
        "volumes": {"porous-data": {}},
    }
    if device == "cuda":
        compose["services"]["porous-backend"]["gpus"] = "all"
    descriptor = directory / "compose.json"
    private_write(descriptor, json.dumps(compose, indent=2).encode())
    command = ("compose", "-p", PROJECT, "-f", str(descriptor))
    docker(*command, "up", "-d", "--wait", "--wait-timeout", "90")
    if device == "cuda":
        docker(*command, "exec", "-T", "porous-backend", "python", "-c",
               "import torch; assert torch.cuda.is_available(); print(torch.cuda.get_device_name(0))")
    deployed_manifest = docker(
        *command, "exec", "-T", "porous-backend", "cat", "/app/vendor/solver-manifest.json",
        capture=True,
    )
    private_write(directory / "solver-manifest.json", deployed_manifest.encode())
    secret_directory = paths.config / "secrets/research"
    for name, origin in (("api-key", keys["api-key"]), ("ca-bundle", certificate)):
        private_write(secret_directory / f"{SERVICE}-{name}", origin.read_bytes(), 0o400)
    profile = paths.config / "profiles/research.env"
    current = profile.read_bytes()
    backup = directory / "previous-profile.env"
    if not backup.exists():
        private_write(backup, current)
    retained = [line for line in current.decode().splitlines() if line.partition("=")[0].strip() not in ENVIRONMENT]
    retained.extend(f"{name}={value}" for name, value in ENVIRONMENT.items())
    private_write(profile, ("\n".join(retained) + "\n").encode())
    private_write(directory / "deployment.json", json.dumps({
        "serviceId": SERVICE, "serviceImage": backend, "proxyImage": proxy,
        "project": PROJECT, "device": device,
    }, indent=2).encode())
    return directory


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    arguments = parser.parse_args()
    print(deploy(arguments.image, device=arguments.device))
