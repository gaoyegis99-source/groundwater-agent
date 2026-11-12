"""Collect logs and operational status from the configured Compose projects."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
import re
import subprocess
from threading import Event, Thread
import time
from urllib.request import ProxyHandler, build_opener

_TIMESTAMP = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{9}Z) (.*)$")
_METRIC = re.compile(r'^(platform_\w+)(\{.*\})? ([-+0-9.eE]+)$')
_LABEL = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')
_PROBES = {
    "kernel-service": "kernel-service",
    "data-service": "data-service",
    "knowledge-next-service": "knowledge-next-service",
    "executor-control": "executor-control",
}
_METRIC_PREFIXES = (
    "platform_queue_", "platform_kernel_", "platform_intent_routing_", "platform_provider_",
    "platform_worker_", "platform_executor_", "platform_sse_", "platform_sqlite_",
)


def utc_stamp(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "000Z")


def parse_metrics(text):
    samples = []
    for line in text.splitlines():
        match = _METRIC.fullmatch(line)
        if match is None or not match[1].startswith(_METRIC_PREFIXES):
            continue
        samples.append({"name": match[1], "labels": dict(_LABEL.findall(match[2] or "")), "value": float(match[3])})
    return samples


def pipe_lines(pipe):
    tail = b""
    while chunk := os.read(pipe.fileno(), 65536):
        *lines, tail = (tail + chunk).split(b"\n")
        yield [line.decode("utf-8", "replace") for line in lines]
    if tail:
        yield [tail.decode("utf-8", "replace")]


class DockerCollector:
    def __init__(self, store, projects, gateway_url, *, docker="docker"):
        self.store = store
        self.projects = tuple(projects)
        self.gateway_url = gateway_url
        self.docker = docker
        self.followers = {}
        self.services = {}
        self.stop = Event()
        self.current = {"collectedAt": None, "services": [], "warning": "等待首次采集"}
        self.main = Thread(target=self.run, name="monitor-collector", daemon=True)

    def snapshot(self):
        return self.current

    def command(self, *args):
        return subprocess.check_output(
            [self.docker, *args], stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", timeout=3,
        )

    def inventory(self):
        items = {}
        for project in self.projects:
            output = self.command(
                "ps", "-a", "--no-trunc", "--filter", f"label=com.docker.compose.project={project}",
                "--format", '{{.ID}}\t{{.Label "com.docker.compose.service"}}',
            )
            for line in output.splitlines():
                container, service = line.split("\t", 1)
                if service and re.fullmatch(r"[0-9a-f]{64}", container):
                    items[container] = (project, service)
        if not items:
            return {}
        output = self.command("inspect", "--format", "{{.Id}}\t{{json .State}}\t{{.RestartCount}}", *items)
        states = {}
        for line in output.splitlines():
            container, raw, restarts = line.split("\t", 2)
            project, name = items[container]
            state = json.loads(raw)
            states[container] = {
                "project": project, "name": name, "service": f"{project}/{name}",
                "process": state["Status"], "health": (state.get("Health") or {}).get("Status"),
                "restarts": int(restarts), "startedAt": state["StartedAt"],
                "exitCode": state["ExitCode"], "container": container[:12],
            }
        return states

    def probe(self, gateway, host):
        result = {"ready": None, "metrics": [], "metricsAvailable": False}
        try:
            ready = self.command("exec", gateway, "curl", "-fsS", "--max-time", "2", f"http://{host}:8000/internal/health/ready")
            result["ready"] = True
            if host == "executor-control":
                result["workers"] = json.loads(ready).get("components", {})
        except subprocess.CalledProcessError:
            result["ready"] = False
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass
        try:
            metrics = self.command("exec", gateway, "curl", "-fsS", "--max-time", "2", f"http://{host}:8000/internal/metrics")
            result.update(metrics=parse_metrics(metrics), metricsAvailable=True)
        except (OSError, subprocess.SubprocessError):
            pass
        return result

    def gateway_ready(self):
        try:
            with build_opener(ProxyHandler({})).open(self.gateway_url + "/product-ui.json", timeout=2) as result:
                return result.status == 200
        except OSError:
            return False

    def gap(self, container, service, message):
        now = time.time()
        self.store.append(container, service, utc_stamp(now), 0, now, message, kind="gap")

    def follow(self, container, service, done):
        while not self.stop.is_set() and not done.is_set():
            checkpoint = self.store.checkpoint(container)
            since = checkpoint[0] if checkpoint else utc_stamp(time.time() - 86400)
            last_stamp, ordinal = "", 0
            replay_seen = False
            proc = None
            try:
                proc = subprocess.Popen(
                    [self.docker, "logs", "--follow", "--timestamps", "--since", since, container],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                )
                done.proc = proc
                if done.is_set() or self.stop.is_set():
                    proc.terminate()
                with proc.stdout as output:
                    for lines in pipe_lines(output):
                        records = []
                        for raw in lines:
                            match = _TIMESTAMP.fullmatch(raw)
                            if not match:
                                continue
                            stamp, line = match.groups()
                            ordinal = ordinal + 1 if stamp == last_stamp else 1
                            last_stamp = stamp
                            if checkpoint and stamp == checkpoint[0] and ordinal == checkpoint[1]:
                                replay_seen = True
                            if checkpoint and (stamp, ordinal) <= checkpoint:
                                continue
                            if checkpoint and not replay_seen:
                                self.gap(container, service, "续接点已不在 Docker 可读日志中，此处可能缺少记录。")
                                replay_seen = True
                            epoch = datetime.fromisoformat(stamp).timestamp()
                            records.append((stamp, ordinal, epoch, line))
                        self.store.append_batch(container, service, records)
                proc.wait(timeout=2)
            except (OSError, subprocess.SubprocessError):
                pass
            finally:
                if proc and proc.poll() is None:
                    proc.kill()
                    proc.wait()
                done.proc = None
            if not done.is_set() and not self.stop.is_set():
                self.gap(container, service, "日志采集连接中断，正在重新连接。")
            done.wait(1)

    def sync_followers(self, states):
        for container, done in list(self.followers.items()):
            if container not in states or done.running != (states[container]["process"] == "running"):
                self.end_follow(done)
                del self.followers[container]
        for container, item in states.items():
            service = item["service"]
            if item["process"] == "running" and self.services.get(service) != container:
                previous = self.store.remember_service(service, container)
                self.services[service] = container
                if previous and previous != container and previous not in states:
                    self.gap(container, service, "服务容器已替换，旧容器不可读的日志可能缺失。")
            if container not in self.followers and item["process"] in {"running", "exited", "dead"}:
                done = Event()
                done.proc = None
                done.running = item["process"] == "running"
                self.followers[container] = done
                if done.running:
                    Thread(target=self.follow, args=(container, service, done), daemon=True).start()
                else:
                    Thread(target=self.read_stopped, args=(container, service, done), daemon=True).start()

    def read_stopped(self, container, service, done):
        try:
            checkpoint = self.store.checkpoint(container)
            since = checkpoint[0] if checkpoint else utc_stamp(time.time() - 86400)
            output = subprocess.check_output(
                [self.docker, "logs", "--timestamps", "--since", since, container],
                stderr=subprocess.STDOUT, encoding="utf-8", errors="replace", timeout=3,
            )
            last_stamp, ordinal = "", 0
            for raw in output.split("\n"):
                match = _TIMESTAMP.fullmatch(raw)
                if not match:
                    continue
                stamp, line = match.groups()
                ordinal = ordinal + 1 if stamp == last_stamp else 1
                last_stamp = stamp
                if not checkpoint or (stamp, ordinal) > checkpoint:
                    self.store.append(container, service, stamp, ordinal, datetime.fromisoformat(stamp).timestamp(), line)
        except (OSError, subprocess.SubprocessError):
            self.gap(container, service, "无法读取已停止容器的日志。")
        finally:
            done.set()

    @staticmethod
    def end_follow(done):
        done.set()
        proc = done.proc
        if proc and proc.poll() is None:
            proc.terminate()

    def tick(self):
        states = self.inventory()
        self.sync_followers(states)
        gateway = next((cid for cid, state in states.items() if state["project"] == self.projects[0]
                        and state["name"] == "gateway" and state["process"] == "running"), None)
        with ThreadPoolExecutor(max_workers=5) as pool:
            live = pool.submit(self.gateway_ready)
            probes = {
                cid: pool.submit(self.probe, gateway, _PROBES[item["name"]])
                for cid, item in states.items()
                if gateway and item["project"] == self.projects[0] and item["name"] in _PROBES
                and item["process"] == "running"
            }
            for cid, probe in probes.items():
                states[cid].update(probe.result())
            gateway_up = live.result()
        executor = next((item for item in states.values() if item["name"] == "executor-control"), {})
        for item in states.values():
            if item["project"] == self.projects[0] and item["name"].startswith("executor-") and item["name"].endswith("-worker"):
                runtime = item["name"].removeprefix("executor-").removesuffix("-worker")
                item["heartbeat"] = executor.get("workers", {}).get(runtime)
        self.current = {
            "collectedAt": time.time(), "services": sorted(states.values(), key=lambda row: row["service"]),
            "gatewayUp": gateway_up, "warning": None if states else "未发现配置中的项目容器",
        }

    def run(self):
        next_prune = 0
        while not self.stop.is_set():
            started = time.monotonic()
            try:
                if started >= next_prune:
                    self.store.prune(time.time())
                    next_prune = started + 60
                self.tick()
            except (OSError, subprocess.SubprocessError, ValueError, KeyError) as error:
                self.current = {**self.current, "warning": f"采集中断（{type(error).__name__}）"}
            self.stop.wait(max(0, 5 - (time.monotonic() - started)))

    def close(self):
        self.stop.set()
        self.main.join(timeout=8)
        for done in self.followers.values():
            self.end_follow(done)
