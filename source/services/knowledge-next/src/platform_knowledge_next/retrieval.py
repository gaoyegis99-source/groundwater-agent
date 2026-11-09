from collections import Counter
from datetime import date, datetime, timezone
import time

from psycopg.types.json import Jsonb

from . import access, catalog
from .config import INDEX_GENERATION
from .encoding import digest, normalize, tokens, uid, vector_text
from .errors import KnowledgeProblemError
from .models import EvidenceBundleV1, EvidenceItem
from .retrieval_budget import (
    additional_context_units,
    context_content_chars,
    fits_context_budget,
    required_context_ids,
)


def binding_applies(stored, requested):
    """Return whether stored evidence is exact enough for this Data request.

    A version-wide binding (no field_id) applies to every field in that exact
    immutable version.  Field-specific evidence remains limited to that field,
    and no dataset/version/semantic mismatch is ever widened.
    """
    core = (
        "service_binding",
        "project_id",
        "dataset_id",
        "version_id",
        "semantic_id",
    )
    return all(stored.get(key) == requested.get(key) for key in core) and (
        stored.get("field_id") is None
        or stored.get("field_id") == requested.get("field_id")
    )


class EvidenceService:
    def __init__(self, db, embeddings):
        self.db, self.embeddings = db, embeddings

    def filters(self, request, scope=None, actor=None):
        where = "(valid_through IS NULL OR valid_through>=CURRENT_DATE)"
        params = []
        readable, access_parameters = access.search_predicate(actor)
        where += " AND (" + readable + ")"
        params.extend(access_parameters)
        if getattr(request, "kind", None):
            where += " AND kind=%s"
            params.append(request.kind)
        metadata = getattr(request, "metadata_filters", None)
        if metadata and metadata.source_kinds:
            where += """ AND EXISTS (
                SELECT 1 FROM versions filter_version,
                jsonb_array_elements(filter_version.body->'sources') filter_source
                WHERE filter_version.id=search_entries.version_id
                AND filter_source->>'kind'=ANY(%s))"""
            params.append(metadata.source_kinds)
        if metadata and metadata.language:
            where += """ AND EXISTS (
                SELECT 1 FROM versions filter_version
                WHERE filter_version.id=search_entries.version_id
                AND filter_version.body->>'language'=%s)"""
            params.append(metadata.language)
        if metadata and metadata.review_due is not None:
            comparison = "<" if metadata.review_due else ">="
            where += f""" AND (SELECT review_after FROM lifecycle
                WHERE version_id=search_entries.version_id
                ORDER BY revision DESC LIMIT 1) {comparison} CURRENT_DATE"""
        if request.binding and request.binding.project_id != self.db.settings.project:
            raise KnowledgeProblemError("BINDING_PROJECT_MISMATCH", 422)
        if scope == "general":
            where += " AND bindings='[]'::jsonb"
        elif request.binding:
            binding = request.binding.model_dump(mode="json")
            if binding["field_id"] is None:
                where += " AND bindings @> %s"
                params.append(Jsonb([binding]))
            else:
                version_binding = {**binding, "field_id": None}
                where += " AND (bindings @> %s OR bindings @> %s)"
                params.extend((Jsonb([binding]), Jsonb([version_binding])))
        return where, params

    def candidates(self, c, request, scope, vector, actor=None):
        where, params = self.filters(request, scope, actor)
        query = " | ".join(sorted(set(tokens(request.query))))
        lexical = (
            c.execute(
                f"""SELECT version_id,unit_id,document_id,ordinal,
            ts_rank(ARRAY[1,1,1,1]::real[],lexical,to_tsquery('simple',%s),2) AS score
            FROM search_entries WHERE {where} AND lexical @@ to_tsquery('simple',%s)
            ORDER BY score DESC,document_id,ordinal LIMIT 40""",
                (query, *params, query),
            ).fetchall()
            if query
            else []
        )
        dense = []
        if vector is not None:
            if self.db.settings.vector_search == "exact":
                c.execute("SET LOCAL enable_indexscan=off")
            else:
                c.execute("SET LOCAL hnsw.ef_search=100")
                c.execute("SET LOCAL hnsw.iterative_scan='strict_order'")
            dense = c.execute(
                f"""SELECT version_id,unit_id,document_id,ordinal,
                embedding <=> %s::vector AS distance FROM search_entries WHERE {where}
                ORDER BY embedding <=> %s::vector LIMIT 40""",
                (vector_text(vector), *params, vector_text(vector)),
            ).fetchall()
            c.execute("SET LOCAL enable_indexscan=on")
        return dense, lexical

    def retrieve(self, request, actor=None):
        actor = actor or access.INTERNAL_ACTOR
        start = time.perf_counter()
        # Reject invalid project context before paying for a query embedding.
        self.filters(request, actor=actor)
        vector = None
        if request.mode == "hybrid":
            vector = self.embeddings.embed((request.query,))[0]
        provider_ms = (time.perf_counter() - start) * 1000
        scope = request.effective_scope
        cohorts = ("general", "bound") if scope == "combined" else (scope,)
        with self.db.transaction(snapshot=True) as c:
            dense, lexical, stats = [], [], {}
            for cohort in cohorts:
                d, lex = self.candidates(c, request, cohort, vector, actor)
                stats[cohort] = {
                    "dense_candidates": len(d),
                    "lexical_candidates": len(lex),
                    "candidates": len(
                        {(r["version_id"], r["unit_id"]) for r in d + lex}
                    ),
                }
                dense.extend(d)
                lexical.extend(lex)
            dense.sort(key=lambda r: (r["distance"], r["document_id"], r["ordinal"]))
            lexical.sort(key=lambda r: (-r["score"], r["document_id"], r["ordinal"]))
            scores, rows, signals = {}, {}, {}
            for channel, group in (("dense", dense), ("lexical", lexical)):
                for rank, row in enumerate(group, 1):
                    key = (row["version_id"], row["unit_id"])
                    rows[key] = row
                    scores[key] = scores.get(key, 0) + 1 / (60 + rank)
                    signals.setdefault(key, {})[f"{channel}_rank"] = rank
                    if channel == "dense":
                        signals[key]["cosine_similarity"] = round(
                            max(0.0, min(1.0, 1.0 - float(row["distance"]))), 6
                        )
                    else:
                        signals[key]["lexical_score"] = round(float(row["score"]), 6)
            counts, chosen = Counter(), []
            ranking = {}
            maximum = max(scores.values(), default=1.0)
            for key in sorted(
                scores, key=lambda k: (-scores[k], rows[k]["document_id"], k[1])
            ):
                normalized = scores[key] / maximum
                if normalized < request.min_score:
                    continue
                did = rows[key]["document_id"]
                if counts[did] < 2:
                    chosen.append((key[0], key[1], None))
                    counts[did] += 1
                    key_signals = signals[key]
                    ranking[key] = {
                        "relevance_score": round(normalized, 6),
                        "rank": len(chosen),
                        "ranking_reason": (
                            "向量语义与关键词共同命中"
                            if "dense_rank" in key_signals
                            and "lexical_rank" in key_signals
                            else "语义相似度命中"
                            if "dense_rank" in key_signals
                            else "关键词命中"
                        ),
                        "ranking_signals": {
                            "fusion": "reciprocal_rank_fusion",
                            **key_signals,
                        },
                    }
                if len(chosen) == request.top_k:
                    break
            return self.bundle(
                c,
                chosen,
                request,
                request.mode,
                start,
                provider_ms,
                {
                    "scope": scope,
                    "cohorts": stats,
                    "candidates": len(scores),
                    "dense_candidates": len(dense),
                    "lexical_candidates": len(lexical),
                    "top_k": request.top_k,
                    "min_score": request.min_score,
                    "metadata_filters": request.metadata_filters.model_dump(
                        mode="json"
                    ),
                },
                ranking=ranking,
                actor=actor,
            )

    def lookup(self, request, actor=None):
        actor = actor or access.INTERNAL_ACTOR
        start = time.perf_counter()
        with self.db.transaction(snapshot=True) as c:
            where, params = self.filters(request, actor=actor)
            if request.key is not None:
                where += " AND keys @> %s"
                params.append([normalize(request.key)])
            rows = c.execute(
                f"""SELECT version_id,unit_id FROM search_entries WHERE {where}
            ORDER BY document_id,ordinal LIMIT 17""",
                params,
            ).fetchall()
            refs = [(r["version_id"], r["unit_id"], None) for r in rows[:16]]
            return self.bundle(
                c,
                refs,
                request,
                "lookup",
                start,
                0,
                {"more_matches": len(rows) > 16},
                actor=actor,
            )

    def read(self, request, actor=None, *, validate_only=False):
        actor = actor or access.INTERNAL_ACTOR
        start = time.perf_counter()
        with self.db.transaction(snapshot=True) as c:
            refs = [(r.version_id, r.unit_id, r.checksum) for r in request.refs]
            return self.bundle(
                c,
                refs,
                request,
                "validate" if validate_only else "read",
                start,
                0,
                {},
                validate_only,
                actor=actor,
            )

    def applicability(self, version, request):
        if version is None:
            return {}
        bindings = version["body"]["bindings"]
        binding = getattr(request, "binding", None)
        return {
            "scope": "DATA_BOUND" if bindings else "GENERAL",
            "binding_match": (
                "GENERAL"
                if not bindings
                else "NOT_CHECKED"
                if binding is None
                else "EXACT"
                if any(
                    binding_applies(stored, binding.model_dump(mode="json"))
                    for stored in bindings
                )
                else "MISMATCH"
            ),
        }

    def bundle(
        self,
        c,
        refs,
        request,
        strategy,
        start,
        provider_ms,
        stats,
        validate_only=False,
        ranking=None,
        actor=None,
    ):
        actor = actor or access.INTERNAL_ACTOR
        ranking = ranking or {}
        version_ids = list({r[0] for r in refs})
        readable, access_parameters = access.readable_predicate("d", actor)
        versions = c.execute(
            """SELECT v.*,l.revision AS lifecycle_revision,l.status,l.valid_through,l.review_after
        FROM versions v JOIN documents d ON d.id=v.document_id
        JOIN LATERAL (SELECT * FROM lifecycle WHERE version_id=v.id ORDER BY revision DESC LIMIT 1) l
        ON true WHERE v.id=ANY(%s) AND ("""
            + readable
            + ")",
            (version_ids, *access_parameters),
        ).fetchall()
        versions = {v["id"]: v for v in versions}
        units = c.execute(
            "SELECT * FROM units WHERE version_id=ANY(%s) ORDER BY ordinal",
            (list(versions),),
        ).fetchall()
        units = {(u["version_id"], u["unit_id"]): u for u in units}
        items, included, chars = [], set(), 0
        for vid, unit_id, checksum in refs:
            root = units.get((vid, unit_id))
            version = versions.get(vid)
            state = (
                "NOT_FOUND"
                if root is None or version is None
                else catalog.status(version)
            )
            rank_info = ranking.get((vid, unit_id), {})
            if root and checksum is not None and root["checksum"] != checksum:
                state = "STALE"
            if state != "OK":
                items.append(
                    EvidenceItem(
                        **self.applicability(versions.get(vid), request),
                        status=state,
                        version_id=vid,
                        unit_id=unit_id,
                        checksum=checksum or "",
                        document_title=version["body"]["title"] if version else None,
                        **rank_info,
                    )
                )
                continue
            if validate_only:
                items.append(
                    EvidenceItem(
                        **self.applicability(versions.get(vid), request),
                        status="OK",
                        version_id=vid,
                        unit_id=unit_id,
                        checksum=root["checksum"],
                        lifecycle_revision=versions[vid]["lifecycle_revision"],
                        document_title=versions[vid]["body"]["title"],
                    )
                )
                continue
            # Complete transitive context, including cycles, must fit as a group.
            group = required_context_ids(units, vid, unit_id)
            new = additional_context_units(units, vid, group, included)
            size = context_content_chars(new)
            if not fits_context_budget(
                included_count=len(included),
                included_chars=chars,
                additional_count=len(new),
                additional_chars=size,
                max_units=request.budget.max_units,
                max_chars=request.budget.max_chars,
            ):
                items.append(
                    EvidenceItem(
                        **self.applicability(versions.get(vid), request),
                        status="NEEDS_EXPANSION",
                        version_id=vid,
                        unit_id=unit_id,
                        checksum=root["checksum"],
                        required_units=sorted(group),
                    )
                )
                continue
            for u in sorted(new, key=lambda row: row["ordinal"]):
                v, body = versions[vid], u["body"]
                unit_ranking = ranking.get((vid, u["unit_id"]), {})
                items.append(
                    EvidenceItem(
                        **self.applicability(versions.get(vid), request),
                        status="OK",
                        version_id=vid,
                        unit_id=u["unit_id"],
                        checksum=u["checksum"],
                        evidence_id="evidence_"
                        + digest(
                            [self.db.settings.project, vid, u["unit_id"], u["checksum"]]
                        ),
                        knowledge_id=v["document_id"],
                        content=body["content"],
                        locator=body["locator"],
                        sources=[
                            s
                            for s in v["body"]["sources"]
                            if s["source_id"] in body["source_refs"]
                        ],
                        bindings=v["body"]["bindings"],
                        bindings_verified_at=v["verified_at"].isoformat()
                        if v["verified_at"]
                        else None,
                        lifecycle_revision=v["lifecycle_revision"],
                        review_due=v["review_after"] < date.today(),
                        required_units=body["required_units"],
                        document_title=v["body"]["title"],
                        **unit_ranking,
                    )
                )
                included.add((vid, u["unit_id"]))
            chars += size
        for cohort, counts in stats.get("cohorts", {}).items():
            scope = "GENERAL" if cohort == "general" else "DATA_BOUND"
            counts["returned_units"] = sum(
                i.status == "OK" and i.scope == scope for i in items
            )
            counts["returned_primary_units"] = len(
                {
                    (i.version_id, i.unit_id)
                    for i in items
                    if i.status == "OK"
                    and i.scope == scope
                    and (i.version_id, i.unit_id) in {(v, u) for v, u, _ in refs}
                }
            )
        meta = c.execute("SELECT revision FROM meta").fetchone()
        complete = all(i.status == "OK" for i in items)
        result = EvidenceBundleV1(
            status="NO_MATCH" if not items else "OK" if complete else "PARTIAL",
            receipt_id=uid("receipt"),
            knowledge_revision=meta["revision"],
            index_generation=INDEX_GENERATION,
            observed_at=datetime.now(timezone.utc).isoformat(),
            items=items,
            coverage={
                "retrieval_exhaustive": False,
                "required_context_complete": complete,
                "strategy": strategy,
                "request_binding": getattr(request, "binding", None).model_dump(
                    mode="json"
                )
                if getattr(request, "binding", None)
                else None,
                "vector_search": self.db.settings.vector_search,
                **stats,
            },
            budget={"returned_units": len(included), "returned_chars": chars},
            timings_ms={"embedding": round(provider_ms, 3)},
        )
        receipt = result.model_dump(mode="json")
        receipt["items"] = [
            {
                k: v
                for k, v in item.items()
                if k
                in {
                    "status",
                    "version_id",
                    "unit_id",
                    "checksum",
                    "evidence_id",
                    "lifecycle_revision",
                    "scope",
                    "binding_match",
                    "relevance_score",
                    "rank",
                    "ranking_reason",
                }
            }
            for item in receipt["items"]
        ]
        c.execute(
            """INSERT INTO receipts(
                 id,request_hash,kind,body,owner_subject
               ) VALUES(%s,%s,%s,%s,%s)""",
            (
                result.receipt_id,
                digest(request.model_dump(mode="json")),
                strategy,
                Jsonb(receipt),
                actor["subject"],
            ),
        )
        result.timings_ms["internal"] = round(
            (time.perf_counter() - start) * 1000 - provider_ms, 3
        )
        return result
