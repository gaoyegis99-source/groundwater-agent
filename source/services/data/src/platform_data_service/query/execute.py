import io
import json
from ..adapters.database import canonical, digest
from ..adapters.query_backends import BACKENDS
from ..errors import DataProblemError


def execute(plan, store, settings, work, query_id):
    result = BACKENDS[plan["kind"]].execute(plan, store, settings, work)
    encoded = canonical(result).encode()
    if len(encoded) > settings.result_bytes:
        raise DataProblemError("QUERY_RESULT_BUDGET", 413)
    obj = store.put(io.BytesIO(encoded), settings.result_bytes)
    receipt = {
        "query_id": query_id,
        "inputs": [
            {
                "version_id": x["version_id"],
                "semantic_id": x["semantic_id"],
                "checksums": [o["checksum"] for o in x["objects"]],
            }
            for x in plan["inputs"]
        ],
        "plan_digest": digest(plan),
        "plan": {k: v for k, v in plan.items() if k not in {"inputs", "metric_rows"}},
        "coverage": result["coverage"],
        "truncated": result["truncated"],
        "returned_rows": len(result["rows"]),
        "result_checksum": obj["checksum"],
        "engine": result["engine"],
    }
    json.dumps(receipt, allow_nan=False)
    return {"object": obj, "receipt": receipt}
