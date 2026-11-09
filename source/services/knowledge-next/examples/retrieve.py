"""Retrieve evidence with an optional exact Data binding through the v3 SDK."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from platform_knowledge_client import KnowledgeClient, models

parser = argparse.ArgumentParser()
parser.add_argument("--url", default="http://127.0.0.1:8120")
parser.add_argument("--query", required=True)
parser.add_argument("--binding-file", type=Path)
args = parser.parse_args()
binding = json.loads(args.binding_file.read_text()) if args.binding_file else None
with KnowledgeClient(args.url) as client:
    request = models.RetrievalRequest(
        query=args.query,
        mode="hybrid",
        purpose="sdk-example",
        scope="combined" if binding else "general",
        binding=models.DataBinding.model_validate(binding) if binding else None,
    )
    result = client.retrieve(request)
    print(json.dumps(result.model_dump(mode="json", by_alias=True), ensure_ascii=False, indent=2))
    refs = [
        models.EvidenceRef(versionId=item.versionId, unitId=item.unitId, checksum=item.checksum)
        for item in result.items if item.status == "OK"
    ]
    if refs:
        historical = client.read_evidence(models.EvidenceReadRequest(refs=refs))
        print(json.dumps(historical.model_dump(mode="json", by_alias=True), ensure_ascii=False, indent=2))
