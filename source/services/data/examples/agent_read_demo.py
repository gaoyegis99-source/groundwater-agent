"""Read-only Data SDK example using the frozen HTTP v3 contract."""
from __future__ import annotations

import argparse
import json

from platform_data_client import DataClient, models


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8110")
    parser.add_argument("--project", default="local-data")
    parser.add_argument("--search", default="模型预测明细")
    args = parser.parse_args()

    with DataClient(args.url, args.project, client_type="agent") as client:
        inventory = client.inventory()
        discovery = client.discover(models.DiscoveryRequest(query=args.search, limit=20))
        versions = [client.read_version(item.currentVersion) for item in discovery.items]
        print(json.dumps({
            "inventory": inventory.model_dump(mode="json", by_alias=True),
            "discovery": discovery.model_dump(mode="json", by_alias=True),
            "versions": [item.model_dump(mode="json", by_alias=True) for item in versions],
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
