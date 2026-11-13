"""Read-only monitoring HTTP surface, authenticated by the research instance."""

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass
from ipaddress import ip_address
import json
from pathlib import Path
import secrets
from threading import Lock
from urllib.parse import urlsplit
import time

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
import httpx

COOKIE = "gw_monitor_session"
WEB = Path(__file__).with_name("web")


class ResearchIdentity:
    def __init__(self, base_url):
        self.base_url = base_url

    def request(self, method, path, **kwargs):
        try:
            with httpx.Client(base_url=self.base_url, timeout=2, trust_env=False) as client:
                response = client.request(method, "/api/v12/auth/" + path, **kwargs)
        except httpx.RequestError as error:
            raise OSError("研究实例认证暂不可用") from error
        if response.status_code >= 500:
            raise OSError("研究实例认证暂不可用")
        if response.status_code >= 400:
            raise HTTPException(response.status_code, "身份验证失败")
        return response

    def login(self, username, password):
        result = self.request("POST", "login", json={"username": username, "password": password})
        body = result.json()
        return result.cookies["gw_research_session"], body["csrfToken"], body["currentUser"]

    def verify(self, session):
        return self.request("GET", "me", headers={"Cookie": f"gw_research_session={session}"}).json()

    def logout(self, session, csrf):
        self.request("POST", "logout", headers={"Cookie": f"gw_research_session={session}", "X-CSRF-Token": csrf})


@dataclass
class Session:
    upstream: str
    csrf: str
    verified: float
    next_check: float
    expires: float


def make_app(store, identity, snapshot, *, clock=time.monotonic, origin=None, secure_cookie=True, lan_networks=()):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    expected_host = urlsplit(origin).netloc if origin else None
    if lan_networks and not expected_host:
        raise ValueError("LAN monitoring requires an explicit origin")
    sessions = {}
    attempts = defaultdict(deque)
    lock = Lock()

    def browser_origin(request):
        if origin and request.headers.get("origin") != origin:
            raise HTTPException(403, "页面来源不符")

    def require_access(request):
        if lan_networks:
            return
        token = request.cookies.get(COOKIE)
        with lock:
            session = sessions.get(token)
            now = clock()
            if not session or session.expires <= now:
                sessions.pop(token, None)
                raise HTTPException(401, "请登录")
            if now < session.next_check:
                return
        try:
            roles = identity.verify(session.upstream).get("roles", [])
            if "admin" not in roles:
                raise HTTPException(403, "需要管理员权限")
        except OSError:
            with lock:
                if sessions.get(token) is session and clock() - session.verified < 900:
                    session.next_check = min(clock() + 5, session.verified + 900)
                    return
                sessions.pop(token, None)
            raise HTTPException(401, "认证服务暂不可用")
        except HTTPException:
            with lock:
                sessions.pop(token, None)
            raise
        with lock:
            if sessions.get(token) is not session:
                raise HTTPException(401, "请登录")
            session.verified = clock()
            session.next_check = session.verified + 30

    @app.middleware("http")
    async def headers(request, call_next):
        if lan_networks:
            try:
                peer = ip_address(request.client.host) if request.client else None
                allowed_peer = peer is not None and any(peer in network for network in lan_networks)
            except ValueError:
                allowed_peer = False
            if not allowed_peer or request.headers.get("host") != expected_host:
                return JSONResponse({"detail": "仅允许从配置的局域网地址访问"}, status_code=403)
            if request.method not in {"GET", "HEAD"}:
                return JSONResponse({"detail": "监控端口为只读"}, status_code=405, headers={"Allow": "GET, HEAD"})
        response = await call_next(request)
        response.headers.update({
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; form-action 'self'",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        })
        return response

    @app.get("/")
    def page():
        return FileResponse(WEB / "index.html")

    @app.get("/monitor.js")
    def javascript():
        return FileResponse(WEB / "monitor.js", media_type="text/javascript")

    @app.get("/monitor.css")
    def stylesheet():
        return FileResponse(WEB / "monitor.css", media_type="text/css")

    @app.post("/api/login")
    def login(request: Request, body: dict[str, str]):
        browser_origin(request)
        now = clock()
        peer = request.client.host if request.client else "unknown"
        with lock:
            for key, times in list(attempts.items()):
                while times and times[0] <= now - 60:
                    times.popleft()
                if not times:
                    del attempts[key]
            times = attempts[peer]
            if len(times) >= 8:
                raise HTTPException(429, "登录过于频繁")
            times.append(now)
        try:
            upstream, csrf, user = identity.login(body.get("username", ""), body.get("password", ""))
        except OSError as error:
            raise HTTPException(503, "认证服务暂不可用") from error
        if "admin" not in user.get("roles", []):
            try:
                identity.logout(upstream, csrf)
            except (OSError, HTTPException):
                pass
            raise HTTPException(403, "需要管理员权限")
        token = secrets.token_urlsafe(32)
        with lock:
            for key, session in list(sessions.items()):
                if session.expires <= now or key == request.cookies.get(COOKIE):
                    del sessions[key]
            sessions[token] = Session(upstream, csrf, now, now + 30, now + 8 * 3600)
        response = Response(status_code=204)
        response.set_cookie(COOKIE, token, secure=secure_cookie, httponly=True, samesite="strict", max_age=8 * 3600)
        return response

    @app.post("/api/logout")
    def logout(request: Request):
        browser_origin(request)
        with lock:
            session = sessions.pop(request.cookies.get(COOKIE), None)
        if session:
            try:
                identity.logout(session.upstream, session.csrf)
            except (OSError, HTTPException):
                pass
        response = Response(status_code=204)
        response.delete_cookie(COOKIE, secure=secure_cookie, httponly=True, samesite="strict")
        return response

    @app.get("/api/status")
    def status(request: Request):
        require_access(request)
        return {**snapshot(), "accessMode": "lan" if lan_networks else "admin"}

    @app.get("/api/logs")
    def history(
        request: Request, service: str | None = None, q: str | None = Query(None, max_length=200),
        start: float | None = None, end: float | None = None, after: int | None = Query(None, ge=0),
        before: int | None = Query(None, ge=1), limit: int = Query(300, ge=1, le=1000),
    ):
        require_access(request)
        cursor = store.latest()
        expired = after is not None and store.expired(after)
        if after is not None and before is not None:
            raise HTTPException(400, "只能选择一个日志方向")
        rows = store.read(
            after=None if expired else after, before=before, service=service,
            needle=q, start=start, end=end, limit=limit,
        )
        if rows and (after is None or expired):
            cursor = max(cursor, rows[-1]["id"])
        if after is not None and not expired and len(rows) == limit:
            cursor = rows[-1]["id"]
        return {
            "logs": rows, "cursor": cursor, "expired": expired,
            "before": rows[0]["id"] if rows and len(rows) == limit else None,
            "retentionSeconds": 86400,
        }

    @app.get("/api/events")
    async def events(request: Request, after: int | None = Query(None, ge=0)):
        await asyncio.to_thread(require_access, request)
        cursor = after if after is not None else store.latest()
        header = request.headers.get("last-event-id", "")
        if header.isascii() and header.isdecimal() and len(header) <= 18:
            cursor = int(header)

        async def stream():
            nonlocal cursor
            yield "retry: 1000\n\n"
            if cursor and await asyncio.to_thread(store.expired, cursor):
                cursor = await asyncio.to_thread(store.latest)
                yield f"id: {cursor}\nevent: gap\ndata: {{}}\n\n"
            last_snapshot = None
            while not await request.is_disconnected():
                try:
                    await asyncio.to_thread(require_access, request)
                except HTTPException:
                    yield "event: auth\ndata: {}\n\n"
                    break
                current = snapshot()
                if current is not last_snapshot:
                    yield f"event: status\ndata: {json.dumps(current, ensure_ascii=False)}\n\n"
                    last_snapshot = current
                for row in await asyncio.to_thread(store.read, after=cursor):
                    cursor = row["id"]
                    yield f"id: {cursor}\nevent: log\ndata: {json.dumps(row, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0.35)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    return app
