from datetime import datetime, timedelta, timezone

from pydantic import TypeAdapter, ValidationError
from platform_embedding_client import TransientUpstreamError, UpstreamServiceError
from psycopg.types.json import Jsonb

from . import catalog
from .access import INTERNAL_ACTOR
from .config import MODEL_PROFILE
from .data_adapter import DataTransientError, retry_after
from .encoding import digest, input_hash, input_text, uid, vector_text
from .errors import KnowledgeProblemError
from .models import ID, KnowledgePackageV1, document_content


LEASE_SECONDS = 120
_RETRY_REQUEST_ID = TypeAdapter(ID)
_MANUAL_RETRYABLE_FAILURES = frozenset(
    {
        "DATA_RETRY_EXHAUSTED",
        "DATA_RETRY_DEADLINE_EXCEEDED",
        "EMBEDDING_RETRY_EXHAUSTED",
        "EMBEDDING_RETRY_DEADLINE_EXCEEDED",
    }
)


def submit(db, package, actor=None):
    if package.project_id != db.settings.project:
        raise KnowledgeProblemError("PROJECT_MISMATCH", 422)
    body = package.model_dump(mode="json")
    checksum = digest(body)
    actor = actor or INTERNAL_ACTOR
    with db.transaction() as c:
        c.execute(
            """INSERT INTO imports(id,idempotency_key,digest,payload,actor,state)
        VALUES(%s,%s,%s,%s,%s,'QUEUED')
        ON CONFLICT(idempotency_key) DO NOTHING""",
            (
                uid("import"),
                package.idempotency_key,
                checksum,
                Jsonb(body),
                Jsonb(actor),
            ),
        )
        row = c.execute(
            """SELECT id,digest,state,data_attempts,retry_generation,next_retry_at,retry_deadline,
            last_error,actor FROM imports WHERE idempotency_key=%s""",
            (package.idempotency_key,),
        ).fetchone()
        if row["digest"] != checksum:
            raise KnowledgeProblemError("IDEMPOTENCY_CONFLICT", 409)
        return {
            "import_id": row["id"],
            **{k: v for k, v in row.items() if k not in ("id", "digest")},
        }


def request_manual_retry(db, import_id, request_id, actor=None):
    """Requeue one transiently exhausted import without changing its identity.

    The request ID is an operator-supplied idempotency key. Lifetime attempt
    counters remain monotonic; persisted bases delimit the next retry budget.
    """

    try:
        request_id = _RETRY_REQUEST_ID.validate_python(request_id)
    except ValidationError as error:
        raise KnowledgeProblemError("RETRY_REQUEST_ID_INVALID", 422) from error
    actor = actor or INTERNAL_ACTOR
    with db.transaction() as c:
        row = c.execute(
            """SELECT id,state,error,attempt,data_attempts,retry_generation
               FROM imports WHERE id=%s FOR UPDATE""",
            (import_id,),
        ).fetchone()
        if row is None:
            raise KnowledgeProblemError("IMPORT_NOT_FOUND", 404)
        failure = row["error"]
        generation = row["retry_generation"] + 1
        inserted = c.execute(
            """INSERT INTO import_manual_retries
               (request_id,import_id,retry_generation,from_attempt,actor,failure)
               VALUES(%s,%s,%s,%s,%s,%s)
               ON CONFLICT(request_id) DO NOTHING RETURNING request_id""",
            (
                request_id,
                import_id,
                generation,
                max(1, row["attempt"]),
                Jsonb(actor),
                Jsonb(failure),
            ),
        ).fetchone()
        if inserted is None:
            previous = c.execute(
                "SELECT import_id FROM import_manual_retries WHERE request_id=%s",
                (request_id,),
            ).fetchone()
            if previous is None or previous["import_id"] != import_id:
                raise KnowledgeProblemError("RETRY_REQUEST_CONFLICT", 409)
            return import_id
        if (
            row["state"] != "FAILED"
            or not isinstance(failure, dict)
            or failure.get("code") not in _MANUAL_RETRYABLE_FAILURES
        ):
            raise KnowledgeProblemError("IMPORT_NOT_RETRYABLE", 409)
        now = utcnow()
        c.execute(
            """UPDATE imports SET state='QUEUED',retry_generation=%s,
               retry_base_attempt=attempt,data_retry_base_attempt=data_attempts,
               error=NULL,last_error=NULL,result=NULL,lease_until=NULL,
               next_retry_at=NULL,retry_deadline=NULL,updated_at=%s
               WHERE id=%s""",
            (generation, now, import_id),
        )
    return import_id


def utcnow():
    return datetime.now(timezone.utc)


def claim(db, clock=utcnow):
    now = clock()
    with db.transaction() as c:
        row = c.execute(
            """SELECT * FROM imports WHERE state='QUEUED' OR
        (state='RETRY_WAIT' AND next_retry_at<=%s) OR
        (state IN ('VALIDATING','INDEXING') AND lease_until<=%s)
        ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1""",
            (now, now),
        ).fetchone()
        if not row:
            return None
        return c.execute(
            """UPDATE imports SET state='VALIDATING',attempt=attempt+1,
        next_retry_at=NULL,lease_until=%s,updated_at=%s WHERE id=%s RETURNING *""",
            (now + timedelta(seconds=LEASE_SECONDS), now, row["id"]),
        ).fetchone()


def renew(db, job, state, clock=utcnow):
    now = clock()
    with db.transaction() as c:
        row = c.execute(
            """UPDATE imports SET state=%s,lease_until=%s,updated_at=%s
        WHERE id=%s AND attempt=%s AND state IN ('VALIDATING','INDEXING') AND lease_until>%s RETURNING id""",
            (
                state,
                now + timedelta(seconds=LEASE_SECONDS),
                now,
                job["id"],
                job["attempt"],
                now,
            ),
        ).fetchone()
        if not row:
            raise KnowledgeProblemError("IMPORT_LEASE_LOST", 409)


def check_versions(c, package):
    for d in package.documents:
        current = c.execute(
            "SELECT current_version FROM documents WHERE id=%s", (d.knowledge_id,)
        ).fetchone()
        if (current["current_version"] if current else None) != d.expected_version:
            raise KnowledgeProblemError("VERSION_CONFLICT", 409, {"knowledge_id": d.knowledge_id})


class Importer:
    def __init__(self, db, embeddings, data, clock=utcnow):
        self.db, self.embeddings, self.data = db, embeddings, data
        self.clock = clock

    def run_once(self):
        job = claim(self.db, self.clock)
        if job is None:
            return False
        try:
            self.process(job)
        except DataTransientError as error:
            self.retry(job, error)
        except TransientUpstreamError as error:
            self.retry(job, error)
        except (KnowledgeProblemError, UpstreamServiceError) as error:
            self.fail(
                job,
                error.code if isinstance(error, KnowledgeProblemError) else str(error),
                error.details if isinstance(error, KnowledgeProblemError) else None,
            )
        except Exception:
            self.fail(job, "INTERNAL_ERROR", None)
            raise  # Programming failures stop the worker, never masquerade as no knowledge.
        return True

    def fail(self, job, code, details):
        now = self.clock()
        with self.db.transaction() as c:
            c.execute(
                """UPDATE imports SET state='FAILED',error=%s,lease_until=NULL,
            next_retry_at=NULL,updated_at=%s WHERE id=%s AND attempt=%s
            AND state IN ('VALIDATING','INDEXING') AND lease_until>%s""",
                (
                    Jsonb({"code": code, "details": details}),
                    now,
                    job["id"],
                    job["attempt"],
                    now,
                ),
            )

    def retry(self, job, error):
        now = self.clock()
        deadline = job["retry_deadline"] or now + timedelta(minutes=10)
        embedding = isinstance(error, TransientUpstreamError)
        attempts = (
            job["attempt"] - job["retry_base_attempt"]
            if embedding
            else job["data_attempts"] - job["data_retry_base_attempt"]
        )
        operation = "EMBEDDING" if embedding else "DATA"
        last_error = {
            "code": error.code,
            "details": None if embedding else error.details,
        }
        terminal = None
        if now >= deadline:
            terminal = f"{operation}_RETRY_DEADLINE_EXCEEDED"
        elif attempts >= 5:
            terminal = f"{operation}_RETRY_EXHAUSTED"
        delay = (5, 15, 45, 120)[min(max(attempts - 1, 0), 3)]
        retry_hint = (
            retry_after(error.retry_after, now) if embedding else error.retry_after
        )
        delay = max(delay, min(retry_hint or 0, 600))
        next_at = min(now + timedelta(seconds=delay), deadline)
        with self.db.transaction() as c:
            c.execute(
                """UPDATE imports SET state=%s,error=%s,last_error=%s,retry_deadline=%s,
                next_retry_at=%s,lease_until=NULL,updated_at=%s
                WHERE id=%s AND attempt=%s AND state IN ('VALIDATING','INDEXING') AND lease_until>%s""",
                (
                    "FAILED" if terminal else "RETRY_WAIT",
                    Jsonb({"code": terminal, "details": last_error})
                    if terminal
                    else Jsonb(last_error),
                    Jsonb(last_error),
                    deadline,
                    None if terminal else next_at,
                    now,
                    job["id"],
                    job["attempt"],
                    now,
                ),
            )

    def check_deadline(self, job, *, operation="DATA"):
        if job["retry_deadline"] and self.clock() >= job["retry_deadline"]:
            raise KnowledgeProblemError(f"{operation}_RETRY_DEADLINE_EXCEEDED", 503)

    def begin_data_attempt(self, job):
        self.check_deadline(job)
        if job["data_attempts"] - job["data_retry_base_attempt"] >= 5:
            raise KnowledgeProblemError("DATA_RETRY_EXHAUSTED", 503)
        now = self.clock()
        with self.db.transaction() as c:
            row = c.execute(
                """UPDATE imports SET data_attempts=data_attempts+1 WHERE id=%s AND attempt=%s
                AND state='VALIDATING' AND lease_until>%s RETURNING data_attempts""",
                (job["id"], job["attempt"], now),
            ).fetchone()
            if not row:
                raise KnowledgeProblemError("IMPORT_LEASE_LOST", 409)
        job["data_attempts"] = row["data_attempts"]
        self.data.begin_attempt()

    def process(self, job):
        package = KnowledgePackageV1.model_validate(job["payload"])
        with self.db.transaction() as c:
            check_versions(c, package)
        if any(d.bindings for d in package.documents):
            self.begin_data_attempt(job)
        seen = set()
        for d in package.documents:
            for b in d.bindings:
                key = b.model_dump_json()
                if key not in seen:
                    renew(self.db, job, "VALIDATING", self.clock)
                    self.check_deadline(job)
                    try:
                        self.data.validate(b)
                    except KnowledgeProblemError as e:
                        e.details = {
                            "knowledge_id": d.knowledge_id,
                            "binding": b.model_dump(),
                        }
                        raise
                    self.check_deadline(job)
                    seen.add(key)
        verified_at = self.clock() if seen else None
        inputs = {
            input_hash(d, u): input_text(d, u)
            for d in package.documents
            for u in d.units
        }
        with self.db.transaction() as c:
            existing = {
                r["input_hash"]
                for r in c.execute(
                    "SELECT input_hash FROM embeddings WHERE input_hash=ANY(%s)",
                    (list(inputs),),
                ).fetchall()
            }
        pending = [key for key in inputs if key not in existing]
        for start in range(0, len(pending), 16):
            renew(self.db, job, "INDEXING", self.clock)
            self.check_deadline(job, operation="EMBEDDING")
            batch = pending[start : start + 16]
            vectors = self.embeddings.embed(tuple(inputs[k] for k in batch))
            if len(vectors) != len(batch):
                raise KnowledgeProblemError("EMBEDDING_RESPONSE_INVALID", 503)
            with self.db.transaction() as c:
                for key, vector in zip(batch, vectors, strict=True):
                    c.execute(
                        "INSERT INTO embeddings(input_hash,embedding) VALUES(%s,%s::vector) ON CONFLICT DO NOTHING",
                        (key, vector_text(vector)),
                    )
        renew(self.db, job, "INDEXING", self.clock)
        self.commit(job, package, verified_at)

    def commit(self, job, package, verified_at):
        with self.db.transaction() as c:
            c.execute("SELECT * FROM meta FOR UPDATE")
            live = c.execute(
                """SELECT * FROM imports WHERE id=%s AND attempt=%s
            AND state='INDEXING' AND lease_until>%s FOR UPDATE""",
                (job["id"], job["attempt"], self.clock()),
            ).fetchone()
            if not live:
                raise KnowledgeProblemError("IMPORT_LEASE_LOST", 409)
            check_versions(c, package)
            result = []
            for d in package.documents:
                vid = uid("version")
                body = document_content(d)
                c.execute(
                    """INSERT INTO documents(id,owner_subject)
                       VALUES(%s,%s) ON CONFLICT DO NOTHING RETURNING id""",
                    (d.knowledge_id, job["actor"]["subject"]),
                )
                c.execute(
                    "INSERT INTO versions(id,document_id,body,checksum,import_id,verified_at) VALUES(%s,%s,%s,%s,%s,%s)",
                    (
                        vid,
                        d.knowledge_id,
                        Jsonb(body),
                        digest(body),
                        job["id"],
                        verified_at if d.bindings else None,
                    ),
                )
                life = catalog.lifecycle_from_sources(d)
                c.execute(
                    """INSERT INTO lifecycle
                    (version_id,revision,status,valid_through,review_after,reason,actor)
                    VALUES(%s,1,%s,%s,%s,'import',%s)""",
                    (
                        vid,
                        life["status"],
                        life["valid_through"],
                        life["review_after"],
                        Jsonb(job["actor"]),
                    ),
                )
                for i, u in enumerate(d.units):
                    ub = u.model_dump(mode="json")
                    c.execute(
                        "INSERT INTO units(version_id,unit_id,ordinal,checksum,body) VALUES(%s,%s,%s,%s,%s)",
                        (vid, u.unit_id, i, digest(ub), Jsonb(ub)),
                    )
                catalog.project_search(c, vid, d, life)
                c.execute(
                    "UPDATE documents SET current_version=%s WHERE id=%s",
                    (vid, d.knowledge_id),
                )
                result.append({"knowledge_id": d.knowledge_id, "version_id": vid})
            revision = c.execute(
                "UPDATE meta SET revision=revision+1 RETURNING revision"
            ).fetchone()["revision"]
            c.execute(
                """UPDATE imports SET state='SUCCEEDED',result=%s,error=NULL,next_retry_at=NULL,lease_until=NULL,updated_at=now()
            WHERE id=%s""",
                (
                    Jsonb(
                        {
                            "versions": result,
                            "knowledge_revision": revision,
                            "model_profile": MODEL_PROFILE,
                        }
                    ),
                    job["id"],
                ),
            )
