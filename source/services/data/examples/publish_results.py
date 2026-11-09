"""Publish synthetic outputs and query them through the strict Data SDK."""
from __future__ import annotations

import argparse
import base64
import io
import json
import time

from platform_data_client import DataClient, models, operations


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8110")
    parser.add_argument("--project", default="local-data")
    parser.add_argument("--key", default="example-model-run-001")
    args = parser.parse_args()
    if len(args.key) > 80:
        parser.error("key must be at most 80 characters")

    outputs = [
        models.Output(name="prediction", title="示例：模型预测明细", format="csv", policy="FILE_QUERYABLE", conversion=models.Conversion(types={"f1": "float64", "f2": "float64"}), semantics=models.Semantics(description="合成验收数据，不是实际监测结果", fields={"f1": models.FieldMeaning(unit="m"), "f2": models.FieldMeaning(unit="m")}, source="caller_declared")),
        models.Output(name="metrics", title="示例：模型评价指标", format="json", policy="FILE_AND_METRICS"),
        models.Output(name="image", title="示例：模型结果图", format="png", policy="FILE_ONLY"),
    ]
    payloads = {
        "prediction": b"well,predicted,observed\n001,10,9\n002,12,11\n",
        "metrics": json.dumps({"metrics": [{"name": "RMSE", "value": 1.0}]}).encode(),
        "image": base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="),
    }
    with DataClient(args.url, args.project, writes=True, client_type="cli") as client:
        publication = client.begin_publication(models.BeginPublicationRequest(idempotencyKey=args.key, outputs=outputs, provenance={"run_id": args.key}))
        for name, content in payloads.items():
            client.upload(publication.publicationId, name, io.BytesIO(content))
        client.prepare_publication(publication.publicationId)
        deadline = time.monotonic() + 60
        while True:
            publication = client.call(operations.DATA_GET_PUBLICATION, path={"publicationId": publication.publicationId})
            if publication.state != "PREPARING":
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("publication preparation timed out")
            time.sleep(0.25)
        receipt = publication.receipt or client.commit_publication(publication.publicationId)
        version_id = next(item.versionId for item in receipt.outputs if item.name == "prediction")
        accepted = client.create_query(models.DataQueryRequest(idempotencyKey=args.key + "-query", kind="table", versionIds=[version_id]))
        while True:
            result = client.query(accepted.queryId)
            if result.state not in {"QUEUED", "RUNNING"}:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("query timed out")
            time.sleep(0.25)
        print(json.dumps({"publication": receipt.model_dump(mode="json", by_alias=True), "query": result.model_dump(mode="json", by_alias=True)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
