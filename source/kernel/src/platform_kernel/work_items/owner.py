from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import json
from pathlib import Path
from sqlite3 import Connection, Row
from typing import Literal, TypeVar, cast
from uuid import uuid4

from platform_protocol_contracts import canonical_json, sha256_bytes

from ..access import RequestIdentity, resolve_identity
from ..access.research_auth import enabled_project_identity
from ..application.work_item_context import WorkItemContextBlock, WorkItemContextRequest
from ..application.work_item_links import (
    linked_workflow_run,
    require_session_link_scope,
    session_projections,
)
from ..application.work_item_lifecycle_facts import (
    SessionFacts,
    collect_session_facts,
    handoff_approval_rows,
    handoff_session_rows,
    handoff_task_rows,
)
from ..business_capabilities import visible_business_capability_refs
from ..database import begin_immediate, connect_database, notify_on_commit, utc_now
from ..errors import AgentRunError, ResourceNotFoundError, WorkItemConflictError
from ..interaction.access import current_agent_turn_subject
from ..settings import KernelSettings
from .agent_context import context_blocks
from .handoff import approved_actions
from .lifecycle import (
    PublicationState,
    ResultLink,
    ReviewCandidate,
    Track,
    delivery_workflows,
    derive_track,
    publication_for,
    summarize_tracks,
)
from .references import ReferenceAuthority


CommandKind = Literal["CREATE", "CREATE_SESSION", "LINK_SESSION", "LINK_OBJECT", "LINK_ASSET", "ARCHIVE", "UPDATE", "REVIEW"]
_AuthorityValue = TypeVar("_AuthorityValue")
_AGENT_AUTHORITY_TIMEOUT = 1.0


async def _authority_read(
    read: Awaitable[_AuthorityValue], seconds: float | None, unavailable: _AuthorityValue,
) -> _AuthorityValue:
    deadline = asyncio.timeout(seconds)
    try:
        async with deadline:
            return await read
    except TimeoutError:
        if not deadline.expired():
            raise
        return unavailable


@dataclass(frozen=True)
class Change:
    kind: CommandKind
    request_id: str
    payload: dict
    work_item_id: str | None = None
    expected_revision: int | None = None

    def fingerprint(self) -> str:
        return sha256_bytes(canonical_json({
            "command": self.kind,
            "workItemId": self.work_item_id,
            "expectedRevision": self.expected_revision,
            "payload": self.payload,
        }))


def _view(row: Row) -> dict:
    return dict(row) | {"status": "ACTIVE" if row["archived_at"] is None else "ARCHIVED"}


def _receipt(row: Row) -> dict:
    return {
        "work_item_id": row["work_item_id"],
        "revision": row["revision"],
        "changed": bool(row["changed"]),
        "link_id": row["link_id"],
    }


@dataclass(frozen=True)
class _LifecycleRead:
    item: Row
    cursor: int
    sessions: tuple[SessionFacts, ...]
    results: tuple[ResultLink, ...]
    approvals: dict[tuple[str, str], str]
    history: tuple[Row, ...]
    observed_at: str


def _activity_entry(row: Row) -> dict:
    shared = {"sequence": row["sequence"], "kind": row["kind"], "created_at": row["created_at"]}
    if row["kind"] == "SESSION":
        return shared | {"session_id": row["session_id"], "event_type": row["event_type"]}
    return shared | {
        "command": row["command"],
        "request_id": row["request_id"],
        "actor_subject": row["owner_subject"],
        "revision": row["revision"],
        "changed": bool(row["changed"]),
        "link_id": row["link_id"],
        "review_session_id": row["review_session_id"],
    }


class WorkItemOwner:
    def __init__(
        self, database: Path, settings: KernelSettings, references: ReferenceAuthority,
        create_session: Callable[[Connection, str], str],
    ) -> None:
        self.database = database
        self.project_id = settings.project_id
        self.references = references
        self._create_session = create_session
        self._settings = settings

    def list_items(
        self, actor: RequestIdentity, include_archived: bool, after: str, limit: int
    ) -> dict:
        with connect_database(self.database) as connection:
            rows = connection.execute(
                """SELECT * FROM work_items
                   WHERE project_id = ? AND owner_subject = ? AND work_item_id > ?
                     AND (? OR archived_at IS NULL)
                   ORDER BY work_item_id LIMIT ?""",
                (self.project_id, actor.subject, after, include_archived, limit + 1),
            ).fetchall()
        return {
            "items": [_view(row) for row in rows[:limit]],
            "next_after": rows[limit - 1]["work_item_id"] if len(rows) > limit else None,
        }

    def audit(self, work_item_id: str, actor: RequestIdentity, after: int, limit: int) -> dict:
        with connect_database(self.database) as connection:
            self._owned(connection, work_item_id, actor)
            rows = connection.execute(
                """SELECT sequence, request_id, owner_subject AS actor_subject,
                          command, revision, changed, link_id, created_at
                   FROM work_item_commands
                   WHERE work_item_id = ? AND sequence > ?
                     AND command IN ('CREATE','LINK_SESSION','LINK_OBJECT','LINK_ASSET','ARCHIVE')
                   ORDER BY sequence LIMIT ?""",
                (work_item_id, after, limit + 1),
            ).fetchall()
        return {
            "entries": [dict(row) | {"changed": bool(row["changed"])} for row in rows[:limit]],
            "next_after": rows[limit - 1]["sequence"] if len(rows) > limit else None,
        }

    def activity_batch(
        self, work_item_id: str, actor: RequestIdentity, after: int, limit: int,
    ) -> tuple[int, list[dict]]:
        with connect_database(self.database) as connection:
            connection.execute("BEGIN")
            self._owned(connection, work_item_id, actor)
            head = self._activity_head(connection, work_item_id)
            rows = connection.execute(
                """SELECT a.sequence, a.kind, a.session_id, a.event_type, a.created_at,
                          c.command, c.request_id, c.owner_subject, c.revision, c.changed,
                          c.link_id, c.review_session_id
                   FROM work_item_activity a
                   LEFT JOIN work_item_commands c ON c.work_item_id=a.work_item_id
                       AND c.sequence=a.command_sequence
                   WHERE a.work_item_id=? AND a.sequence>? ORDER BY a.sequence LIMIT ?""",
                (work_item_id, after, limit),
            ).fetchall()
        return head, [_activity_entry(row) for row in rows]

    @staticmethod
    def _activity_head(connection: Connection, work_item_id: str) -> int:
        return connection.execute(
            "SELECT COALESCE(MAX(sequence),0) FROM work_item_activity WHERE work_item_id=?",
            (work_item_id,),
        ).fetchone()[0]

    async def lifecycle(self, work_item_id: str, actor: RequestIdentity) -> dict:
        saved = await asyncio.to_thread(self._read_lifecycle, work_item_id, actor)
        return await self._project_lifecycle(saved, actor)

    async def _project_lifecycle(
        self, saved: _LifecycleRead, actor: RequestIdentity, *, authority_timeout: float | None = None,
    ) -> dict:
        work_item_id = saved.item["work_item_id"]
        archived = saved.item["archived_at"] is not None
        slots = asyncio.Semaphore(8)
        runs = {run.run_id: run for facts in saved.sessions for run in delivery_workflows(facts)}

        async def publication(run_id: str) -> PublicationState:
            async with slots:
                return await _authority_read(
                    self.references.publication_state(run_id, actor), authority_timeout, "UNAVAILABLE",
                )

        async def readable(link: ResultLink) -> bool:
            async with slots:
                return await _authority_read(
                    self.references.is_visible("RESULT", link.reference, actor), authority_timeout, False,
                )

        to_observe = [
            run_id for run_id, run in runs.items()
            if run.output_names and publication_for(run, saved.results, {}) != "PUBLISHED"
        ]
        observed = dict(zip(to_observe, await asyncio.gather(*(publication(key) for key in to_observe)), strict=True))
        choices = [(facts, derive_track(work_item_id, facts, saved.results, observed)) for facts in saved.sessions]
        selected = {
            link.link_id: link
            for facts, track in choices
            if track.review and not archived and (facts.session_id, track.review.token) not in saved.approvals
            for link in track.review.links
        }
        available = dict(zip(selected, await asyncio.gather(*(readable(link) for link in selected.values())), strict=True))
        tracks = []
        states = []
        for facts, track in choices:
            review = track.review
            accepted_at = saved.approvals.get((facts.session_id, review.token)) if review else None
            preview = None
            if accepted_at:
                track = Track("COMPLETED", "DELIVERY_ACCEPTED")
            elif review and not archived:
                if all(available[link.link_id] for link in review.links):
                    preview = {"token": review.token, "result_link_ids": [link.link_id for link in review.links]}
                else:
                    track = Track(track.state, "RESULT_UNAVAILABLE")
            task_spec = {} if facts.task is None else facts.task["spec"]
            tracks.append({
                "session_id": facts.session_id,
                "task_id": task_spec.get("task_id"),
                "task_revision": task_spec.get("revision"),
                "state": track.state,
                "reason": track.reason,
                "confirmation_id": facts.confirmation_id,
                "stop_pending": facts.stop_pending,
                "publications": [
                    {"run_id": run.run_id, "state": publication_for(run, saved.results, observed)}
                    for run in delivery_workflows(facts)
                ],
                "review": preview,
                "accepted_at": accepted_at,
            })
            states.append(track)
        state, reason = summarize_tracks(states, archived=archived)
        return {
            "work_item_id": work_item_id,
            "revision": saved.item["revision"],
            "event_cursor": saved.cursor,
            "state": state,
            "reason": reason,
            "tracks": tracks,
        }

    def _read_lifecycle(
        self, work_item_id: str, actor: RequestIdentity, *, history_for: str | None = None,
    ) -> _LifecycleRead:
        with connect_database(self.database) as connection:
            connection.execute("BEGIN")
            item = self._owned(connection, work_item_id, actor)
            cursor = self._activity_head(connection, work_item_id)
            sessions = collect_session_facts(connection, work_item_id, self.project_id, actor.subject)
            results = self._result_links(connection, work_item_id)
            approvals = {
                (row["review_session_id"], row["review_token"]): row["created_at"]
                for row in connection.execute(
                    """SELECT review_session_id, review_token, created_at FROM work_item_commands
                       WHERE work_item_id=? AND command='REVIEW' AND changed=1 ORDER BY sequence""",
                    (work_item_id,),
                )
            }
            history = (
                handoff_session_rows(connection, work_item_id, self.project_id, actor.subject, history_for)
                if history_for is not None else []
            )
            observed_at = utc_now()
        return _LifecycleRead(item, cursor, sessions, results, approvals, tuple(history), observed_at)

    @staticmethod
    def _result_links(connection: Connection, work_item_id: str) -> tuple[ResultLink, ...]:
        return tuple(
            ResultLink(row["link_id"], json.loads(row["reference_json"]))
            for row in connection.execute(
                """SELECT link_id, reference_json FROM work_item_references
                   WHERE work_item_id=? AND kind='RESULT'""",
                (work_item_id,),
            )
        )

    async def review(self, change: Change, actor: RequestIdentity) -> dict:
        previous, candidate = await asyncio.to_thread(self._review_preflight, change, actor)
        if previous is not None:
            return _receipt(previous)
        assert candidate is not None
        for link in candidate.links:
            await self.references.require_result(link.reference, actor)
        return await asyncio.to_thread(self.apply, change, actor, reviewed_token=candidate.token)

    def _review_preflight(
        self, change: Change, actor: RequestIdentity,
    ) -> tuple[Row | None, ReviewCandidate | None]:
        with connect_database(self.database) as connection:
            connection.execute("BEGIN")
            _, previous = self._validate(connection, change, actor)
            if previous is not None:
                return previous, None
            return None, self._review_candidate(connection, change, actor)

    def _review_candidate(self, connection: Connection, change: Change, actor: RequestIdentity) -> ReviewCandidate:
        work_item_id = cast(str, change.work_item_id)
        sessions = collect_session_facts(connection, work_item_id, self.project_id, actor.subject)
        facts = next((facts for facts in sessions if facts.session_id == change.payload["sessionId"]), None)
        if facts is None:
            raise ResourceNotFoundError("WORK_ITEM_SESSION_NOT_FOUND")
        track = derive_track(work_item_id, facts, self._result_links(connection, work_item_id), {})
        if track.review is None or track.review.token != change.payload["reviewToken"]:
            raise WorkItemConflictError("WORK_ITEM_REVIEW_STALE")
        return track.review

    async def detail(self, work_item_id: str, actor: RequestIdentity) -> dict:
        detail, stored = await asyncio.to_thread(self._snapshot, work_item_id, actor)
        await self._project_references(detail, stored, actor)
        return detail

    async def _project_references(
        self, detail: dict, stored: list[Row], actor: RequestIdentity, *, visible_only: bool = False,
        authority_timeout: float | None = None, for_agent: bool = False,
    ) -> None:
        upstream_slots = asyncio.Semaphore(8)

        async def project(row: Row) -> dict:
            reference = json.loads(row["reference_json"])
            async with upstream_slots:
                visible = await _authority_read(
                    self.references.is_visible(row["kind"], reference, actor, for_agent=for_agent),
                    authority_timeout, False,
                )
            return {
                "link_id": row["link_id"],
                "linked_at": row["linked_at"],
                "state": "AVAILABLE" if visible else "UNAVAILABLE",
                "reference": reference if visible else None,
            }

        references = await asyncio.gather(*(project(row) for row in stored))
        for row, reference in zip(stored, references, strict=True):
            if not visible_only or reference["state"] == "AVAILABLE":
                detail["objects" if row["kind"] == "OBJECT" else "assets"].append(reference)

    def _snapshot(self, work_item_id: str, actor: RequestIdentity) -> tuple[dict, list[Row]]:
        with connect_database(self.database) as connection:
            connection.execute("BEGIN")
            item = self._owned(connection, work_item_id, actor)
            links = session_projections(connection, work_item_id, self.project_id, actor.subject)
            references = connection.execute(
                """SELECT kind, link_id, reference_json, linked_at FROM work_item_references
                   WHERE work_item_id = ? ORDER BY linked_at, link_id""",
                (work_item_id,),
            ).fetchall()
        return {"work_item": _view(item), **links, "objects": [], "assets": []}, references

    async def session_context(self, session_id: str, actor: RequestIdentity) -> dict | None:
        context, stored, approvals = await asyncio.to_thread(self._read_session_context, session_id, actor)
        if context is None:
            return None
        await self._project_references(context, stored, actor, visible_only=True)
        context["approved_actions"] = approved_actions(approvals, context["assets"], self._settings.models)
        return context

    async def agent_context(
        self, request: WorkItemContextRequest,
    ) -> tuple[WorkItemContextBlock, ...]:
        actor = await asyncio.to_thread(self._agent_actor, request)
        if actor is None:
            return ()
        try:
            blocks = await self._project_agent_context(request, actor)
        except ResourceNotFoundError:
            return ()
        if blocks and await asyncio.to_thread(self._agent_actor, request) == actor:
            return blocks
        return ()

    def _agent_actor(self, request: WorkItemContextRequest) -> RequestIdentity | None:
        if request.project_id != self.project_id:
            return None
        subject = current_agent_turn_subject(
            self.database, request.project_id, request.session_id, request.agent_run_id,
            request.goal_revision,
        )
        if subject is None:
            return None
        actor = (
            enabled_project_identity(self.database, request.project_id, subject)
            if self._settings.research_auth_required else resolve_identity({}, self._settings)
        )
        if actor is None or actor.subject != subject or not actor.roles.intersection({"admin", "researcher"}):
            return None
        return actor

    async def require_agent_selection(self, request: WorkItemContextRequest) -> None:
        actor = await asyncio.to_thread(self._agent_actor, request)
        if actor is None or self._agent_selection(request, actor) is None:
            raise AgentRunError("BUSINESS_CAPABILITY_SELECTION_STALE")

    def _agent_selection(self, request: WorkItemContextRequest, actor: RequestIdentity) -> dict | None:
        return visible_business_capability_refs(
            self._settings.business_capabilities, request.selection, actor.roles,
        )

    async def _project_agent_context(
        self, request: WorkItemContextRequest, actor: RequestIdentity,
    ) -> tuple[WorkItemContextBlock, ...]:
        context, stored, _ = await asyncio.to_thread(
            self._read_session_context, request.session_id, actor,
            reference_limit=8 if request.include_resources else 0, handoff=False,
        )
        if context is None or context["work_item"]["status"] == "ARCHIVED":
            return ()
        item = context["work_item"]
        saved = await asyncio.to_thread(
            self._read_lifecycle, item["work_item_id"], actor,
            history_for=request.session_id if request.include_history else None,
        )
        if saved.item["revision"] != item["revision"]:
            return ()
        facts = next((entry for entry in saved.sessions if entry.session_id == request.session_id), None)
        if facts is None:
            return ()
        lifecycle, _ = await asyncio.gather(
            self._project_lifecycle(saved, actor, authority_timeout=_AGENT_AUTHORITY_TIMEOUT),
            self._project_references(
                context, stored, actor, visible_only=True, authority_timeout=_AGENT_AUTHORITY_TIMEOUT,
                for_agent=True,
            ),
        )
        still_current, _, _ = await asyncio.to_thread(
            self._read_session_context, request.session_id, actor, reference_limit=0, handoff=False,
        )
        if still_current is None or still_current["work_item"] != item:
            return ()
        history = ()
        if saved.history:
            latest = await asyncio.to_thread(self._agent_history, request, item["work_item_id"], actor)
            fact_revisions = {facts.session_id: facts.goal_revision for facts in saved.sessions}
            history = tuple(
                dict(row) | {"observed_at": saved.observed_at, "business_goal_revision": fact_revisions[row["session_id"]]}
                for row in saved.history if tuple(row) in latest and row["session_id"] in fact_revisions
            )
        return context_blocks(request, actor, context, lifecycle, facts, history, self._agent_selection(request, actor))

    def _agent_history(
        self, request: WorkItemContextRequest, work_item_id: str, actor: RequestIdentity,
    ) -> set[tuple]:
        with connect_database(self.database) as connection:
            return {
                tuple(row) for row in handoff_session_rows(
                    connection, work_item_id, request.project_id, actor.subject, request.session_id,
                )
            }

    def _read_session_context(
        self, session_id: str, actor: RequestIdentity, *, reference_limit: int = 24, handoff: bool = True,
    ) -> tuple[dict | None, list[Row], list[Row]]:
        with connect_database(self.database) as connection:
            connection.execute("BEGIN")
            if not require_session_link_scope(connection, session_id, self.project_id, actor.subject):
                raise ResourceNotFoundError("SESSION_NOT_FOUND")
            linked = connection.execute(
                """SELECT work_item_id, linked_at FROM work_item_sessions WHERE session_id=?""",
                (session_id,),
            ).fetchone()
            if linked is None:
                return None, [], []
            work_item_id = linked["work_item_id"]
            item = self._owned(connection, work_item_id, actor)
            context = {
                "session_id": session_id, "linked_at": linked["linked_at"], "work_item": _view(item),
                "objects": [], "assets": [], "open_tasks": [], "approved_actions": [],
            }
            if item["archived_at"] is not None:
                return context, [], []
            stored = connection.execute(
                """SELECT kind, link_id, reference_json, linked_at FROM work_item_references
                   WHERE work_item_id = ? AND kind IN ('OBJECT','DATA','KNOWLEDGE','RESULT')
                   ORDER BY linked_at DESC, link_id LIMIT ?""",
                (work_item_id, reference_limit),
            ).fetchall() if reference_limit else []
            if not handoff:
                return context, stored, []
            scope = (work_item_id, self.project_id, actor.subject, session_id)
            context["open_tasks"] = [dict(row) for row in handoff_task_rows(connection, *scope)]
            approvals = handoff_approval_rows(connection, *scope)
        return context, stored, approvals

    async def attach_reference(self, change: Change, actor: RequestIdentity) -> dict:
        previous, linked_runs = await asyncio.to_thread(self._reference_preflight, change, actor)
        if previous is not None:
            return _receipt(previous)
        if change.kind == "LINK_OBJECT":
            exact = await self.references.resolve_object(change.payload, actor)
        else:
            exact = await self.references.resolve_asset(change.payload, actor, linked_runs)
        return await asyncio.to_thread(self.apply, change, actor, exact)

    def _reference_preflight(
        self, change: Change, actor: RequestIdentity
    ) -> tuple[Row | None, frozenset[str]]:
        with connect_database(self.database) as connection:
            connection.execute("BEGIN")
            _, previous = self._validate(connection, change, actor)
            if previous is not None or change.payload.get("kind") not in {"WORKFLOW", "RESULT"}:
                return previous, frozenset()
            run_id = change.payload["runId"]
            linked = linked_workflow_run(
                connection, cast(str, change.work_item_id), self.project_id, actor.subject, run_id,
            )
            return None, frozenset({run_id}) if linked else frozenset()

    def apply(
        self, change: Change, actor: RequestIdentity, reference: dict | None = None, *,
        reviewed_token: str | None = None,
    ) -> dict:
        with connect_database(self.database) as connection:
            begin_immediate(connection)
            item, previous = self._validate(connection, change, actor)
            if previous is not None:
                return _receipt(previous)
            now = utc_now()
            review_session_id = None
            review_token = None
            if item is None:
                work_item_id, revision, changed, link_id = uuid4().hex, 1, True, None
                connection.execute(
                    """INSERT INTO work_items
                       (work_item_id, project_id, owner_subject, title, description, revision,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (work_item_id, self.project_id, actor.subject, change.payload["title"],
                     change.payload["description"], revision, now, now),
                )
            else:
                work_item_id, revision = item["work_item_id"], item["revision"]
                archived_at = item["archived_at"]
                title, description = item["title"], item["description"]
                if change.kind in {"LINK_SESSION", "CREATE_SESSION"}:
                    link_id = (
                        self._create_session(connection, actor.subject)
                        if change.kind == "CREATE_SESSION" else change.payload["sessionId"]
                    )
                    changed = self._link_session(connection, work_item_id, actor, link_id, now)
                elif change.kind == "ARCHIVE":
                    changed, link_id, archived_at = archived_at is None, None, archived_at or now
                elif change.kind == "UPDATE":
                    title = change.payload.get("title", title)
                    description = change.payload.get("description", description)
                    changed, link_id = (title, description) != (item["title"], item["description"]), None
                elif change.kind == "REVIEW":
                    review_token = self._review_candidate(connection, change, actor).token
                    if review_token != reviewed_token:
                        raise WorkItemConflictError("WORK_ITEM_REVIEW_STALE")
                    review_session_id, link_id = change.payload["sessionId"], None
                    changed = connection.execute(
                        """SELECT 1 FROM work_item_commands
                           WHERE work_item_id=? AND review_session_id=? AND review_token=? AND changed=1""",
                        (work_item_id, review_session_id, review_token),
                    ).fetchone() is None
                else:
                    assert reference is not None
                    kind = "OBJECT" if change.kind == "LINK_OBJECT" else reference["kind"]
                    changed, link_id = self._link_reference(connection, work_item_id, kind, reference, now)
                if changed:
                    revision += 1
                    connection.execute(
                        """UPDATE work_items SET revision=?, updated_at=?, archived_at=?, title=?, description=?
                           WHERE work_item_id = ?""",
                        (revision, now, archived_at, title, description, work_item_id),
                    )
            sequence = connection.execute(
                """SELECT COALESCE(MAX(sequence), 0) + 1 FROM work_item_commands
                   WHERE work_item_id = ?""",
                (work_item_id,),
            ).fetchone()[0]
            row = connection.execute(
                """INSERT INTO work_item_commands
                   (sequence, project_id, owner_subject, request_id, work_item_id, command,
                    fingerprint, revision, changed, link_id, created_at, review_session_id, review_token)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   RETURNING work_item_id, revision, changed, link_id""",
                (sequence, self.project_id, actor.subject, change.request_id, work_item_id,
                 "LINK_SESSION" if change.kind == "CREATE_SESSION" else change.kind,
                 change.fingerprint(), revision, changed, link_id, now, review_session_id, review_token),
            ).fetchone()
            notify_on_commit(connection)
            return _receipt(row)

    def _owned(self, connection: Connection, work_item_id: str, actor: RequestIdentity) -> Row:
        row = connection.execute(
            """SELECT * FROM work_items
               WHERE work_item_id = ? AND project_id = ? AND owner_subject = ?""",
            (work_item_id, self.project_id, actor.subject),
        ).fetchone()
        if row is None:
            raise ResourceNotFoundError("WORK_ITEM_NOT_FOUND")
        return row

    def _validate(
        self, connection: Connection, change: Change, actor: RequestIdentity
    ) -> tuple[Row | None, Row | None]:
        item = self._owned(connection, change.work_item_id, actor) if change.work_item_id else None
        previous = connection.execute(
            """SELECT work_item_id, fingerprint, revision, changed, link_id FROM work_item_commands
               WHERE project_id = ? AND owner_subject = ? AND request_id = ?""",
            (self.project_id, actor.subject, change.request_id),
        ).fetchone()
        if previous is not None:
            if previous["fingerprint"] != change.fingerprint():
                raise WorkItemConflictError("WORK_ITEM_REQUEST_ID_REUSED")
            return item, previous
        if item is not None:
            if item["revision"] != change.expected_revision:
                raise WorkItemConflictError("WORK_ITEM_REVISION_CONFLICT")
            if change.kind != "ARCHIVE" and item["archived_at"] is not None:
                raise WorkItemConflictError("WORK_ITEM_ARCHIVED")
        return item, None

    def _link_session(
        self, connection: Connection, work_item_id: str, actor: RequestIdentity,
        session_id: str, now: str,
    ) -> bool:
        if not require_session_link_scope(connection, session_id, self.project_id, actor.subject):
            raise ResourceNotFoundError("SESSION_NOT_FOUND")
        row = connection.execute(
            "SELECT work_item_id FROM work_item_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row is not None:
            if row["work_item_id"] != work_item_id:
                raise WorkItemConflictError("SESSION_ALREADY_LINKED")
            return False
        connection.execute(
            """INSERT INTO work_item_sessions (work_item_id, session_id, linked_at)
               VALUES (?, ?, ?)""",
            (work_item_id, session_id, now),
        )
        return True

    @staticmethod
    def _link_reference(
        connection: Connection, work_item_id: str, kind: str, reference: dict, now: str
    ) -> tuple[bool, str]:
        encoded = canonical_json(reference)
        key = sha256_bytes(encoded)
        row = connection.execute(
            """SELECT link_id FROM work_item_references
               WHERE work_item_id = ? AND kind = ? AND reference_key = ?""",
            (work_item_id, kind, key),
        ).fetchone()
        if row is not None:
            return False, row["link_id"]
        link_id = uuid4().hex
        connection.execute(
            """INSERT INTO work_item_references
               (work_item_id, kind, reference_key, reference_json, link_id, linked_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (work_item_id, kind, key, encoded.decode("utf-8"), link_id, now),
        )
        return True, link_id
