"""Synthetic scale test using repeated corpus units and cached real embeddings.

Run on a disposable *_bench database restored from a verified evaluation DB.
No quality claim is made for replicated documents. Query vectors are computed
once using the real provider; the measured service path excludes provider time.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import math
import platform
from pathlib import Path
import threading
import time

from platform_embedding_client import EmbeddingClient
from platform_knowledge_next.config import Settings
from platform_knowledge_next.database import Database
from platform_knowledge_next.data_adapter import DataAdapter
from platform_knowledge_next.ingestion import Importer, submit
from platform_knowledge_next.models import (
    DataBinding,
    Document,
    EvidenceRead,
    EvidenceRef,
    KnowledgePackageV1,
    Lookup,
    Retrieval,
)
from platform_knowledge_next.retrieval import EvidenceService


def p95(values):
    return round(sorted(values)[math.ceil(len(values) * 0.95) - 1], 3)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=24)
    parser.add_argument("--sizes", nargs="+", type=int, default=[5000, 50000])
    parser.add_argument(
        "--binding-file",
        type=Path,
        help="Exact real Data binding; alternate replicated docs between general and bound",
    )
    args = parser.parse_args()
    binding = (
        DataBinding.model_validate_json(args.binding_file.read_text())
        if args.binding_file
        else None
    )
    settings = Settings.from_env()
    if not settings.database_url.endswith("_bench"):
        raise ValueError("Disposable *_bench database required")
    db = Database(settings)
    db.check()
    embedding = EmbeddingClient(settings.embedding_url)
    query = "地下水趋势分析需要哪些输入和质量检查"
    provider_start = time.perf_counter()
    vector = embedding.embed((query,))[0]
    provider_ms = (time.perf_counter() - provider_start) * 1000

    class CachedQuery:
        def embed(self, texts):
            assert texts == (query,)
            return (vector,)

    importer = Importer(db, embedding, DataAdapter(settings))
    with db.transaction() as c:
        seed = [
            Document.model_validate(r["body"])
            for r in c.execute(
                "SELECT v.body FROM versions v JOIN documents d ON d.current_version=v.id WHERE EXISTS (SELECT 1 FROM search_entries s WHERE s.version_id=v.id) ORDER BY v.id"
            ).fetchall()
        ]
    with db.transaction() as c:
        count = c.execute("SELECT count(*) AS n FROM search_entries").fetchone()["n"]
    if not seed or count <= 0:
        raise ValueError("Verified non-empty benchmark seed required")
    seed_count = count
    serial = 0
    report = {
        "host": platform.platform(),
        "processor": platform.processor(),
        "samples": args.samples,
        "query_embedding_ms": round(provider_ms, 3),
        "binding": binding.model_dump() if binding else None,
        "corpus": f"replicated verified {seed_count}-unit corpus; query embedding replayed",
        "seed_units": seed_count,
        "results": [],
    }

    def import_package(documents, key):
        result = submit(
            db,
            KnowledgePackageV1(
                project_id=settings.project, idempotency_key=key, documents=documents
            ),
        )
        importer.run_once()
        with db.transaction() as c:
            row = c.execute(
                "SELECT * FROM imports WHERE id=%s", (result["import_id"],)
            ).fetchone()
        if row["state"] != "SUCCEEDED":
            raise RuntimeError(row["error"])

    for target in args.sizes:
        while count < target:
            batch = []
            for _ in range(64):
                if count >= target:
                    break
                d = seed[serial % len(seed)].model_copy(deep=True)
                d.knowledge_id, d.expected_version = f"bench-{serial:06}", None
                d.bindings = [binding] if binding and serial % 2 else []
                n = min(64, target - count)
                d.units = [
                    d.units[i % len(d.units)].model_copy(
                        update={"unit_id": f"unit-{i}", "required_units": []}
                    )
                    for i in range(n)
                ]
                batch.append(d)
                count += n
                serial += 1
            import_package(batch, f"bench-{serial}")
        with db.transaction() as c:
            c.execute("ANALYZE search_entries")
            assert (
                c.execute("SELECT count(*) AS n FROM search_entries").fetchone()["n"]
                == target
            )
            v = c.execute(
                "SELECT v.* FROM versions v JOIN documents d ON d.current_version=v.id WHERE EXISTS (SELECT 1 FROM search_entries s WHERE s.version_id=v.id) ORDER BY v.id LIMIT 1"
            ).fetchone()
            original = Document.model_validate(v["body"])
            original.expected_version = v["id"]
        for search in ("exact", "hnsw"):
            service = EvidenceService(
                Database(replace(settings, vector_search=search)), CachedQuery()
            )
            lookup = Lookup(key=original.knowledge_id)
            first = service.lookup(lookup).items[0]
            read = EvidenceRead(
                refs=[
                    EvidenceRef(
                        version_id=first.version_id,
                        unit_id=first.unit_id,
                        checksum=first.checksum,
                    )
                ]
            )
            for busy in (False, True):
                stop = threading.Event()

                def ingest_loop():
                    index = 0
                    while not stop.is_set():
                        with db.transaction() as c:
                            active = c.execute(
                                "SELECT current_version FROM documents WHERE id=%s",
                                (original.knowledge_id,),
                            ).fetchone()["current_version"]
                        d = original.model_copy(deep=True)
                        d.expected_version, d.content_version = active, f"bench-{index}"
                        import_package([d], f"busy-{target}-{search}-{index}")
                        index += 1
                        stop.wait(0.1)

                with ThreadPoolExecutor(1) as background:
                    future = background.submit(ingest_loop) if busy else None
                    try:
                        for concurrency in (1, 4, 8):
                            values = {}
                            for name, fn in [
                                ("lookup", lambda: service.lookup(lookup)),
                                ("read", lambda: service.read(read)),
                                (
                                    "hybrid",
                                    lambda: service.retrieve(Retrieval(query=query)),
                                ),
                            ] + (
                                [
                                    (
                                        "bound",
                                        lambda: service.retrieve(
                                            Retrieval(
                                                query=query,
                                                scope="bound",
                                                binding=binding,
                                            )
                                        ),
                                    ),
                                    (
                                        "combined",
                                        lambda: service.retrieve(
                                            Retrieval(
                                                query=query,
                                                scope="combined",
                                                binding=binding,
                                            )
                                        ),
                                    ),
                                ]
                                if binding
                                else []
                            ):

                                def measure(_):
                                    start = time.perf_counter()
                                    fn()
                                    return (time.perf_counter() - start) * 1000

                                with ThreadPoolExecutor(concurrency) as pool:
                                    times = list(pool.map(measure, range(args.samples)))
                                values[name] = p95(times)
                            row = {
                                "units": target,
                                "vector_search": search,
                                "ingesting": busy,
                                "concurrency": concurrency,
                                "p95_ms": values,
                            }
                            report["results"].append(row)
                            print(json.dumps(row), flush=True)
                            args.output.write_text(json.dumps(report, indent=2))
                    finally:
                        stop.set()
                        if future:
                            future.result()
    embedding.close()


if __name__ == "__main__":
    main()
