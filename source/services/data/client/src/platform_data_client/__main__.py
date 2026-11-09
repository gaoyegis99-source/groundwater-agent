import argparse
import json
from pathlib import Path
from . import DataClient, operations


def main():
    parser = argparse.ArgumentParser(description="HTTP CLI for standalone Data Service")
    parser.add_argument("--url", default="http://127.0.0.1:8110")
    parser.add_argument("--project", default="local-data")
    parser.add_argument("--writes", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    version = commands.add_parser("get-version")
    version.add_argument("version_id")
    upload = commands.add_parser("upload")
    upload.add_argument("publication")
    upload.add_argument("name")
    upload.add_argument("file", type=Path)
    commands.add_parser("operations")
    args = parser.parse_args()
    client = DataClient(args.url, args.project, writes=args.writes, client_type="cli")
    try:
        if args.command == "operations":
            result = sorted(operations.OPERATIONS)
        elif args.command == "upload":
            with args.file.open("rb") as source:
                result = client.upload(args.publication, args.name, source)
        else:
            result = client.read_version(args.version_id)
        if hasattr(result, "model_dump"):
            result = result.model_dump(by_alias=True)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    finally:
        client.close()


if __name__ == "__main__":
    main()
