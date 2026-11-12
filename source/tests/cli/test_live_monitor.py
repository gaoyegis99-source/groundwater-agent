from datetime import datetime, timezone

from fastapi.testclient import TestClient

from scripts.operations.monitor.server import make_app
from scripts.operations.monitor.store import LogStore


class Identity:
    role = "admin"
    offline = False

    def login(self, username, password):
        return ("upstream-session", "csrf", {"roles": [self.role]})

    def verify(self, session):
        if self.offline:
            raise OSError("offline")
        return {"roles": [self.role]}

    def logout(self, session, csrf):
        pass


def test_access_outage_and_revocation(tmp_path):
    clock = [0]
    identity = Identity()
    store = LogStore(tmp_path / "logs.db")
    app = make_app(store, identity, lambda: {"services": []}, clock=lambda: clock[0])
    with TestClient(app, base_url="https://monitor.test") as client:
        for path in ("/api/status", "/api/logs", "/api/events"):
            assert client.get(path).status_code == 401
        identity.role = "researcher"
        assert client.post("/api/login", json={"username": "r", "password": "pw"}).status_code == 403
        identity.role = "admin"
        login = client.post("/api/login", json={"username": "a", "password": "pw"})
        assert login.status_code == 204
        cookie = login.headers["set-cookie"]
        assert "gw_monitor_session=" in cookie and "Secure" in cookie and "HttpOnly" in cookie
        assert client.get("/api/status").status_code == 200
        identity.offline = True
        clock[0] = 31
        assert client.get("/api/logs").status_code == 200
        clock[0] = 901
        assert client.get("/api/logs").status_code == 401
        identity.offline = False
        assert client.post("/api/login", json={"username": "a", "password": "pw"}).status_code == 204
        identity.role = "viewer"
        clock[0] += 31
        assert client.get("/api/events").status_code == 403
        assert client.get("/api/status").status_code == 401


def test_history_replay_retention_and_filters(tmp_path):
    path = tmp_path / "logs.db"
    now = datetime.now(timezone.utc).timestamp()
    stamp = "2026-11-12T08:00:00.123456789Z"
    store = LogStore(path)
    for ordinal in (1, 2):
        store.append("a", "kernel", stamp, ordinal, now, "same line")
    store = LogStore(path)
    assert store.checkpoint("a") == (stamp, 2)
    store.append("a", "kernel", stamp, 2, now, "same line")
    store.append("b", "gateway", stamp, 1, now, "request xyz")
    rows = store.read(service="kernel")
    assert len(rows) == 2 and rows[0]["line"] == rows[1]["line"]
    assert len(store.read(after=rows[0]["id"])) == 2
    assert store.read(needle="xyz")[0]["service"] == "gateway"
    store.prune(now + 86401)
    assert store.read() == []
    assert store.expired(rows[0]["id"])


def test_docker_checkpoint_missing_marks_possible_rotation(tmp_path, monkeypatch):
    import os
    from threading import Event

    from scripts.operations.monitor.collector import DockerCollector

    store = LogStore(tmp_path / "monitor.sqlite")
    collector = DockerCollector(store, ["isolated"], "http://127.0.0.1:28186")
    previous = "2026-11-12T08:00:00.123456789Z"
    incoming = "2026-11-12T08:01:00.123456789Z"
    store.append("docker-id", "isolated/kernel", previous, 1, 1, "last saved line")
    stopped = Event()

    read_fd, write_fd = os.pipe()
    os.write(write_fd, f"{incoming}  raw log spacing \n".encode())
    os.close(write_fd)

    class Process:
        stdout = os.fdopen(read_fd, "rb")

        def wait(self, timeout=None):
            stopped.set()
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr("scripts.operations.monitor.collector.subprocess.Popen", lambda *args, **kwargs: Process())
    collector.follow("docker-id", "isolated/kernel", stopped)
    rows = store.read(service="isolated/kernel")
    assert [row["kind"] for row in rows] == ["log", "gap", "log"]
    assert rows[-1]["line"] == " raw log spacing "
    assert store.read(before=rows[-1]["id"])[-1]["kind"] == "gap"
    assert store.checkpoint("docker-id") == (incoming, 1)


def test_open_sse_closes_after_role_revocation(tmp_path):
    from contextlib import closing
    import socket
    from threading import Thread

    import httpx
    import uvicorn

    clock = [0]
    identity = Identity()
    store = LogStore(tmp_path / "logs.db")
    app = make_app(store, identity, lambda: {"services": []}, clock=lambda: clock[0])
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    with closing(socket.socket()) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=4, trust_env=False) as client:
                response = client.post("/api/login", json={"username": "a", "password": "pw"})
                assert response.status_code == 204
                cookie = "gw_monitor_session=" + response.cookies["gw_monitor_session"]
                with client.stream("GET", "/api/events", headers={"Cookie": cookie}) as events:
                    assert events.status_code == 200
                    iterator = events.iter_lines()
                    assert any(line == "event: status" for line in iterator)
                    identity.role = "researcher"
                    clock[0] += 31
                    assert "event: auth" in list(iterator)
                assert client.get("/api/status", headers={"Cookie": cookie}).status_code == 401
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            assert not thread.is_alive()


def test_lan_http_login_requires_same_origin_and_uses_independent_cookie(tmp_path):
    origin = "http://192.168.1.10:28187"
    store = LogStore(tmp_path / "logs.db")
    app = make_app(store, Identity(), lambda: {"services": []}, origin=origin, secure_cookie=False)
    with TestClient(app, base_url=origin) as client:
        credentials = {"username": "admin", "password": "fixture"}
        assert client.post("/api/login", json=credentials).status_code == 403
        response = client.post("/api/login", json=credentials, headers={"Origin": origin})
        assert response.status_code == 204
        cookie = response.headers["set-cookie"]
        assert "gw_monitor_session=" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
        assert "Secure" not in cookie
        assert client.get("/api/status").status_code == 200
        assert client.post("/api/logout", headers={"Origin": origin}).status_code == 204
        assert client.get("/api/status").status_code == 401


def test_passwordless_monitor_is_read_only_and_restricted_to_its_network(tmp_path):
    from ipaddress import IPv4Network

    origin = "http://192.168.167.87:28187"
    store = LogStore(tmp_path / "lan.db")
    app = make_app(
        store, Identity(), lambda: {"services": []}, origin=origin,
        lan_networks=(IPv4Network("192.168.167.0/24"), IPv4Network("192.168.161.79/32")),
    )
    with TestClient(app, base_url=origin, client=("192.168.167.50", 1234)) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/status").json()["accessMode"] == "lan"
        assert client.get("/api/logs").status_code == 200
        assert client.post("/api/login", json={}).status_code == 405
        assert client.post("/api/logout").status_code == 405
        assert client.get("/api/status", headers={"Host": "other.example:28187"}).status_code == 403
    with TestClient(app, base_url=origin, client=("192.168.161.79", 1234)) as client:
        assert client.get("/api/status").status_code == 200
        assert client.get("/api/logs").status_code == 200
    with TestClient(app, base_url=origin, client=("192.168.161.80", 1234)) as client:
        assert client.get("/api/logs").status_code == 403
    with TestClient(app, base_url=origin, client=("192.168.168.50", 1234)) as client:
        for path in ("/", "/api/status", "/api/logs", "/api/events"):
            assert client.get(path, headers={"X-Forwarded-For": "192.168.167.50"}).status_code == 403
