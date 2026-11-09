"""Ephemeral previews: no persistence, replay, evidence or execution authority."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import json


class PreviewHub:
    def __init__(self):
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._sequence = 0

    def publish(self, session_id: str, run_id: str, text: str | None):
        self._sequence += 1
        event = {"sequence": self._sequence, "agent_run_id": run_id,
                 "text": None if text is None else text[:16000]}
        for queue in self._subscribers.get(session_id, ()):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)

    @asynccontextmanager
    async def subscribe(self, session_id: str):
        queue = asyncio.Queue(maxsize=1)
        subscribers = self._subscribers.setdefault(session_id, set())
        subscribers.add(queue)
        try:
            yield queue
        finally:
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(session_id, None)


class _TextParser:
    """Incremental JSON lexer with a path stack; temporary output is never proof."""
    def __init__(self):
        self.stack = []
        self.token = None
        self.escaped = False
        self.path = ()
        self.text = None
        self.size = 0
        self.disabled = False

    def _value_path(self):
        if not self.stack:
            return ()
        frame = self.stack[-1]
        if frame["path"] is None or len(frame["path"]) >= 2:
            return None
        return frame["path"] + (frame["key"] if frame["kind"] == "object" else None,)

    def _done(self):
        if self.stack:
            self.stack[-1]["state"] = "after"

    def feed(self, fragment):
        for char in fragment:
            self.size += 1
            if self.size > 64000:
                self.disabled = True
            if self.disabled:
                return None
            if self.token is not None:
                if char == '"' and not self.escaped:
                    try:
                        value = json.loads('"' + ''.join(self.token) + '"')
                    except json.JSONDecodeError:
                        self.disabled = True
                        return None
                    self.token = None
                    if self.stack and self.stack[-1]["state"] == "key":
                        self.stack[-1].update(key=value, state="colon")
                    else:
                        if self.path == ("conclusion", "text"):
                            self.text = value
                        self._done()
                    continue
                self.token.append(char)
                self.escaped = char == "\\" and not self.escaped
                continue
            if char.isspace():
                continue
            if char == '"':
                self.path = () if self.stack and self.stack[-1]["state"] == "key" else self._value_path()
                self.token, self.escaped = [], False
            elif char in "{[":
                if len(self.stack) >= 64:
                    self.disabled = True
                    return None
                path = self._value_path()
                self.stack.append({"kind": "object" if char == "{" else "array",
                                   "state": "key" if char == "{" else "value", "key": None, "path": path})
            elif char in "}]":
                if not self.stack:
                    self.disabled = True
                    return None
                self.stack.pop()
                self._done()
            elif char == ":" and self.stack:
                self.stack[-1]["state"] = "value"
            elif char == "," and self.stack:
                self.stack[-1]["state"] = "key" if self.stack[-1]["kind"] == "object" else "value"
            else:
                self._done()  # Primitive values cannot contain a nested text field.
        if self.token is not None and self.path == ("conclusion", "text"):
            try:
                partial = json.loads('"' + ''.join(self.token) + '"')
                partial.encode("utf-8")  # Wait for both halves of a surrogate pair.
                self.text = partial
            except (json.JSONDecodeError, UnicodeEncodeError):
                pass
        return self.text


class ReplyPreview:
    def __init__(self, publish):
        self.publish = publish
        self.parsers: dict[str, _TextParser] = {}
        self.last_published: dict[str, str] = {}

    def feed(self, event):
        if event.type != 'raw_response_event':
            return
        data = event.data
        if data.type == 'response.output_item.added':
            item = data.item
            if item.type == 'function_call' and item.name == 'submit_reply':
                self.parsers[item.id] = _TextParser()
        elif data.type == 'response.function_call_arguments.delta' and data.item_id in self.parsers:
            text = self.parsers[data.item_id].feed(data.delta)
            bounded = None if text is None else text[:16000]
            if bounded is not None and self.last_published.get(data.item_id) != bounded:
                self.last_published[data.item_id] = bounded
                self.publish(bounded)
