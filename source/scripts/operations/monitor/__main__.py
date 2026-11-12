"""Run the Groundwater monitor on a private IPv4 address."""

import argparse
import ipaddress
import os
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn

from .collector import DockerCollector
from .server import ResearchIdentity, make_app
from .store import LogStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=28187)
    parser.add_argument("--http-lan", action="store_true", help="serve HTTP on an explicit private LAN address")
    parser.add_argument("--lan-read-only", type=ipaddress.IPv4Network, action="append", default=[],
        help="allow passwordless reads from this private network; may be repeated")
    parser.add_argument("--certificate", type=Path)
    parser.add_argument("--key", type=Path)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--gateway", default="http://127.0.0.1:28186")
    parser.add_argument("--project", action="append", required=True)
    args = parser.parse_args()
    address = ipaddress.IPv4Address(args.host)
    if address.is_unspecified or not (address.is_private or address.is_loopback):
        parser.error("an explicit private IPv4 address is required")
    if args.http_lan:
        if args.certificate or args.key:
            parser.error("--http-lan cannot be combined with TLS files")
    elif not (args.certificate and args.key):
        parser.error("provide --certificate and --key, or explicitly select --http-lan")
    lan_networks = args.lan_read_only
    private_networks = tuple(map(ipaddress.IPv4Network, ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")))
    if any(
        not any(network.subnet_of(private) for private in private_networks)
        for network in lan_networks
    ):
        parser.error("--lan-read-only requires private IPv4 networks")
    gateway = urlsplit(args.gateway)
    if gateway.scheme not in {"http", "https"} or gateway.hostname != "127.0.0.1":
        parser.error("the Gateway must be on loopback")
    os.umask(0o077)
    args.database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    store = LogStore(args.database)
    collector = DockerCollector(store, args.project, args.gateway.rstrip("/"))
    scheme = "http" if args.http_lan else "https"
    app = make_app(
        store, ResearchIdentity(args.gateway), collector.snapshot,
        origin=f"{scheme}://{address}:{args.port}", secure_cookie=not args.http_lan, lan_networks=lan_networks,
    )
    collector.main.start()
    try:
        uvicorn.run(
            app, host=args.host, port=args.port,
            ssl_certfile=os.fspath(args.certificate) if args.certificate else None,
            ssl_keyfile=os.fspath(args.key) if args.key else None,
            access_log=False, proxy_headers=False, timeout_graceful_shutdown=4,
        )
    finally:
        collector.close()


if __name__ == "__main__":
    main()
