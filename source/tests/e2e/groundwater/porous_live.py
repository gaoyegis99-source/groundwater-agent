"""Isolated deployment and end-to-end acceptance for the porous candidate."""

from dataclasses import dataclass
from datetime import date, timedelta
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path
import secrets
import subprocess
from uuid import uuid4
from zipfile import ZipFile

import httpx

from products.groundwater.scripts.porous_candidate import INPUT, MODEL, SERVICE, TOOL
from scripts.lib.e2e_http import public_api_headers
from tests.e2e.groundwater.model_lifecycle import PROJECT, SYNTHETIC_TEST_ONLY, _execute, _publish, _request

HOST = "porous-candidate.local"


@dataclass(frozen=True)
class PorousAcceptance:
    fixture: Path
    image: str
    report: Path
    fixtures: Path | None = None
    assessment_fixture: Path | None = None


def _track_remote(command: list[str], operator_id: str) -> dict:
    from scripts.checks.compose_e2e.environment import _exec_python

    return json.loads(_exec_python(command, "executor-control", f"""
import json, sqlite3, time
with sqlite3.connect('/data/state/executor.sqlite3') as db:
    until = time.monotonic() + 45
    while time.monotonic() < until:
        row = db.execute(
            "SELECT job_id, node_id, remote_state_json FROM job_nodes "
            "WHERE operator_id=? AND status='RUNNING' "
            "AND json_extract(remote_state_json, '$.remote_task_id') IS NOT NULL",
            ({operator_id!r},),
        ).fetchone()
        if row is not None:
            print(json.dumps({{'job': row[0], 'node': row[1], 'remote': json.loads(row[2])['remote_task_id']}}))
            break
        time.sleep(0.1)
    else:
        raise RuntimeError('ACTIVE_REMOTE_CHECKPOINT_NOT_OBSERVED')
"""))


def _transport_state(command: list[str], tracked: dict, *, cancelled: bool = False) -> dict:
    from scripts.checks.compose_e2e.environment import _exec_python

    return json.loads(_exec_python(command, "executor-control", f"""
import json, sqlite3, time
with sqlite3.connect('/data/state/executor.sqlite3') as db:
    until = time.monotonic() + 30
    while True:
        row = db.execute(
            "SELECT j.status, j.publication_id, j.outputs_json, n.lost_count, n.remote_state_json "
            "FROM executor_jobs j JOIN job_nodes n ON n.job_id=j.job_id "
            "WHERE n.job_id=? AND n.node_id=?", ({tracked['job']!r}, {tracked['node']!r}),
        ).fetchone()
        state = json.loads(row[4])
        if not {cancelled!r} or state.get('remote_termination_confirmed') or time.monotonic() >= until:
            break
        time.sleep(0.25)
    assert state['remote_task_id'] == {tracked['remote']!r}, state
    if {cancelled!r}:
        assert row[0] == 'CANCELLED' and row[1] is None and not json.loads(row[2]), row
        assert state['remote_status'] == 'cancelled' and state['remote_termination_confirmed'] is True, state
    else:
        assert row[0] == 'SUCCEEDED' and row[1] is not None and row[3] == 1, row
    print(json.dumps({{'jobId': {tracked['job']!r}, 'taskId': state['remote_task_id'],
                      'state': state['remote_status'], 'publicationId': row[1], 'lostAttempts': row[3]}}))
"""))


def configure_deployment(compose: dict, environment: Path, image: str) -> None:
    directory = environment.parent
    secret_dir = directory / "secrets"
    certificate = secret_dir / f"{SERVICE}-ca-bundle"
    credential = secret_dir / f"{SERVICE}-api-key"
    tls_key = secret_dir / "porous-tls-key"
    package_key = secret_dir / "porous-package-key"
    # The generic Compose fixture creates read-only placeholders for every
    # remote binding. Replace this isolated service's pair before startup.
    credential.unlink(missing_ok=True)
    certificate.unlink(missing_ok=True)
    for file in (credential, package_key):
        file.write_text(secrets.token_urlsafe(48), encoding="ascii")
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:3072", "-nodes",
        "-days", "1", "-subj", f"/CN={HOST}", "-addext", f"subjectAltName=DNS:{HOST}",
        "-keyout", str(tls_key), "-out", str(certificate),
    ], check=True, capture_output=True)
    for file in (certificate, credential, tls_key, package_key):
        file.chmod(0o444)
    nginx = directory / "porous-nginx.conf"
    nginx.write_text("""pid /tmp/nginx.pid;
error_log /dev/stderr warn;
events {}
http {
    access_log off;
    client_max_body_size 64m;
    client_body_temp_path /tmp/client;
    proxy_temp_path /tmp/proxy;
    fastcgi_temp_path /tmp/fastcgi;
    uwsgi_temp_path /tmp/uwsgi;
    scgi_temp_path /tmp/scgi;
    server {
        listen 127.0.0.1:8081;
        location = /readyz {
            proxy_pass http://porous-backend:8000;
        }
    }
    server {
        listen 8443 ssl;
        ssl_certificate /tls/cert;
        ssl_certificate_key /tls/key;
        ssl_protocols TLSv1.2 TLSv1.3;
        location / {
            proxy_pass http://porous-backend:8000;
        }
    }
}
""", encoding="ascii")
    nginx.chmod(0o444)
    endpoints = {
        "POROUS_GROUNDWATER_BASE_URL": f"https://{HOST}:8443",
        "POROUS_GROUNDWATER_ALLOWED_HOST": HOST,
    }
    lines = [
        line for line in environment.read_text(encoding="utf-8").splitlines()
        if line.partition("=")[0] not in endpoints
    ]
    lines.extend(f"{name}={value}" for name, value in endpoints.items())
    environment.write_text("\n".join(lines) + "\n", encoding="utf-8")
    compose.setdefault("volumes", {})["porous-data"] = {}
    compose["networks"]["porous-backend"] = {"internal": True}
    services = compose["services"]
    hardening = {
        "read_only": True,
        "security_opt": ["no-new-privileges:true"],
        "cap_drop": ["ALL"],
        "tmpfs": ["/tmp:rw,nosuid,nodev,noexec,size=64m"],
    }
    services["porous-backend"] = {
        **hardening,
        "image": image,
        "pull_policy": "never",
        "environment": {
            "POROUS_API_KEY_FILE": "/run/secrets/key",
            "POROUS_PACKAGE_KEY_FILE": "/run/secrets/package-key",
        },
        "volumes": [
            f"{credential}:/run/secrets/key:ro",
            f"{package_key}:/run/secrets/package-key:ro",
            "porous-data:/var/lib/porous-groundwater",
        ],
        "networks": ["porous-backend"],
    }
    services["porous-tls"] = {
        **hardening,
        "image": "source-groundwater-gateway:latest",
        "pull_policy": "never",
        "user": "101:101",
        "healthcheck": {
            "test": ["CMD", "wget", "-qO", "/dev/null", "http://127.0.0.1:8081/readyz"],
            "interval": "3s", "timeout": "2s", "retries": 12,
        },
        "entrypoint": ["nginx", "-c", "/tls/nginx.conf", "-g", "daemon off;"],
        "volumes": [
            f"{nginx}:/tls/nginx.conf:ro",
            f"{certificate}:/tls/cert:ro",
            f"{tls_key}:/tls/key:ro",
        ],
        "depends_on": {"porous-backend": {"condition": "service_healthy"}},
        "networks": {"porous-backend": {}, "v6-egress": {"aliases": [HOST]}},
    }


def _download(client: httpx.Client, outputs: dict, name: str) -> bytes:
    output = outputs[name]
    response = client.get(
        f"/api/v12/data/versions/{output['version_id']}/objects/{output['object_id']}/content",
        headers={"X-Project-ID": PROJECT, "X-Client-Type": "cli"},
    )
    response.raise_for_status()
    return response.content


def _exercise_additional(client, capabilities, directory, project_version, command):
    from scripts.checks.compose_e2e.environment import _run
    from products.groundwater.scripts.porous_candidate import FORMATS

    fixtures = {}
    for name, format_ in (
        ("observations.json", "json"), ("scenario.zip", "zip"), ("original.tiff", "geotiff"),
        ("training.zip", "zip"), ("sampling.json", "json"), ("agricultural.zip", "zip"),
    ):
        fixtures[name] = _publish(
            client, name=name, content=(directory / name).read_bytes(), format_=format_,
            media_type=FORMATS[format_][0], data_nature=SYNTHETIC_TEST_ONLY,
            source="porous-groundwater Linux synthetic acceptance",
        )["version_id"]
    reports = {}

    def execute(model, tool, arguments, **options):
        result = _execute(
            client, capabilities, model_id=f"groundwater.porous-{model}", tool=tool,
            arguments=arguments, input_versions=set(arguments.values()), data_nature=SYNTHETIC_TEST_ONLY,
            **options,
        )
        reports[model] = result
        return {item["output_name"]: item for item in result["output_references"]}

    tracked = {}

    def restart(_session, _run_id):
        tracked.update(_track_remote(command, "groundwater.porous.pest_calibrate"))
        _run([*command, "restart", "--timeout", "10", "executor-remote_http-worker"])

    pest = execute("calibration", "run_porous_pest_calibration", {
        INPUT: project_version, "calibrationObservationsVersionId": fixtures["observations.json"],
    }, on_confirmed=restart)
    recovery = _transport_state(command, tracked)
    fitted = json.loads(_download(client, pest, "porous-calibrated-parameters"))
    assert math.isclose(fitted["wellMultiplier"], 1.6, rel_tol=1e-3)
    diagnostics = json.loads(_download(client, pest, "porous-calibration-residuals"))
    assert diagnostics["objective"] <= 1e-4
    scenario = execute("scenario-comparison", "run_porous_scenario_compare", {
        "scenarioPackageVersionId": fixtures["scenario.zip"],
    })
    baseline, adjusted = json.loads(_download(client, scenario, "porous-scenario-table"))
    assert baseline["scenarioId"] == "baseline" and adjusted["meanFinalHeadChange"] < 0
    raster = execute("map", "run_porous_map_prepare", {"geoTiffVersionId": fixtures["original.tiff"]})
    assert json.loads(_download(client, raster, "porous-map-crs"))["epsg"] == 4326
    train = execute("inversion-training", "run_porous_inversion_train", {
        "inversionTrainingVersionId": fixtures["training.zip"],
    })
    training = json.loads(_download(client, train, "porous-training-metrics"))
    assert training["seed"] == 42 and 0 < training["bestValidationLoss"] < 10
    sampling = execute("inversion-sampling", "run_porous_inversion_sample", {
        "inversionWeightsVersionId": train["porous-inversion-weights"]["version_id"],
        "inversionSamplingVersionId": fixtures["sampling.json"],
    })
    posterior = json.loads(_download(client, sampling, "porous-sampling-metrics"))
    assert posterior["seed"] == 42 and posterior["shape"] == [2, 5, 64, 64]
    agri = execute("agricultural-inversion", "run_porous_agri_inversion", {
        "agriculturalDataVersionId": fixtures["agricultural.zip"],
    })
    metrics = json.loads(_download(client, agri, "porous-agricultural-metrics"))
    assert metrics["shape"] == [3, 8, 1]
    assert 0 <= metrics["validationOptimizedRmseM"] < metrics["validationBaselineRmseM"]
    with ZipFile(directory / "training.zip") as archive, BytesIO() as buffer:
        with ZipFile(buffer, "w") as longer:
            for name in archive.namelist():
                data = archive.read(name)
                if name == "training.json":
                    data = json.dumps({**json.loads(data), "epochs": 60}).encode()
                longer.writestr(name, data)
        cancellable = _publish(
            client, name="可取消的反演训练", content=buffer.getvalue(), format_="zip",
            media_type="application/zip", data_nature=SYNTHETIC_TEST_ONLY,
            source="porous-groundwater Linux cancellation acceptance",
        )["version_id"]

    def stop(session, agent_run):
        tracked.update(_track_remote(command, "groundwater.porous.inversion_train"))
        _request(
            client, "POST", f"/api/v12/sessions/{session}/stop",
            json={"requestId": uuid4().hex, "agentRunId": agent_run},
        )

    cancelled = _execute(
        client, capabilities, model_id="groundwater.porous-inversion-training",
        tool="run_porous_inversion_train", arguments={"inversionTrainingVersionId": cancellable},
        input_versions={cancellable}, data_nature=SYNTHETIC_TEST_ONLY,
        on_confirmed=stop, cancelled=True,
    )
    return reports, {"workerRestart": recovery, "cancelled": cancelled | _transport_state(command, tracked, cancelled=True)}


def _exercise_assessment(client, capabilities, simulation_version, fixture: Path):
    from moperators.operators.sustainability_preparation import MANAGEMENT, TEXT_FIELDS, UNITS, VOLUME_FIELDS

    base = json.loads(fixture.read_bytes())
    scope = {
        "waterSource": "矩形场地合成验收", "startDate": "2020-01-01",
        "endDate": (date(2020, 1, 1) + timedelta(days=2400)).isoformat(),
    }
    documents = {
        role: {"scope": scope, "synthetic": True, "fields": {}}
        for role in ("monitoring", "management")
    }
    for field in (UNITS.keys() | TEXT_FIELDS) - VOLUME_FIELDS:
        role = "management" if field in MANAGEMENT else "monitoring"
        documents[role]["fields"][field] = {
            "value": base[field], "unit": UNITS.get(field),
            "basis": "综合评价合成验收示例", "source": f"合成{role}资料",
        }
    documents["management"]["simulationBudget"] = [
        {"field": field, "direction": direction, "term": term,
         "basis": "合成矩形场地以全模型范围为评价区，按边界类型标识来源"}
        for field, direction, term in (
            ("recharge_r_m3_a", "in", "CONSTANT HEAD"),
            ("outflow_qout_m3_a", "out", "CONSTANT HEAD"),
            ("pumping_qp_m3_a", "out", "WELLS"),
        )
    ]

    def publish(name, body):
        return _publish(
            client, name=name, content=json.dumps(body, ensure_ascii=False).encode(),
            format_="json", media_type="application/json", data_nature=SYNTHETIC_TEST_ONLY,
            source="矩形场地端到端验收合成证据",
        )["version_id"]

    management = publish("合成管理和供需资料", documents["management"])

    def prepare_evidence(monitoring):
        arguments = {
            "simulationEvidenceVersionId": simulation_version,
            "monitoringEvidenceVersionId": monitoring,
            "managementEvidenceVersionId": management,
        }
        result = _execute(
            client, capabilities, model_id="groundwater.sustainability-input-preparation",
            tool="prepare_source_sustainability", arguments=arguments,
            input_versions=set(arguments.values()), data_nature=SYNTHETIC_TEST_ONLY,
        )
        outputs = {row["output_name"]: row for row in result["output_references"]}
        return (
            result, outputs["assessment-input-candidate"]["version_id"],
            json.loads(_download(client, outputs, "assessment-input-sources")),
            json.loads(_download(client, outputs, "assessment-input-candidate")),
        )

    invalid = {**documents["monitoring"], "scope": {**scope, "endDate": "2020-01-02"}}
    blocked, _, diagnostics, marker = prepare_evidence(publish("时段冲突的合成监测资料", invalid))
    assert diagnostics["status"] == "BLOCKED" and marker["assessmentInput"] is None
    assert any(issue["code"] == "SCOPE_MISMATCH" for issue in diagnostics["issues"])
    prepared, candidate_version, sources, candidate = prepare_evidence(
        publish("合成监测资料", documents["monitoring"]),
    )
    assert sources["status"] == "REVIEW_REQUIRED" and candidate["synthetic_example"]
    assert sources["fields"]["pumping_qp_m3_a"]["role"] == "simulation"
    assert sources["fields"]["class_iii_count_nd"]["role"] == "monitoring"
    assert sources["fields"]["prohibited_area"]["role"] == "management"
    assert math.isclose(candidate["pumping_qp_m3_a"], 36500)
    evaluated = _execute(
        client, capabilities, model_id="groundwater.source-sustainability",
        tool="run_source_sustainability", arguments={"assessmentVersionId": candidate_version},
        input_versions={candidate_version}, data_nature=SYNTHETIC_TEST_ONLY,
    )
    return {"blocked": blocked, "prepared": prepared, "evaluated": evaluated}


def gateway_probe(url: str, acceptance: PorousAcceptance, command: list[str]) -> dict:
    with httpx.Client(
        base_url=url, headers=public_api_headers(), timeout=60, trust_env=False
    ) as client:
        capabilities = _request(client, "GET", "/api/v12/capabilities")
        fixture = acceptance.fixture.read_bytes()
        ref = _publish(
            client,
            name="孔隙型矩形场地合成算例",
            content=fixture,
            format_="zip",
            media_type="application/zip",
            data_nature=SYNTHETIC_TEST_ONLY,
            source="v2.6.2 Linux candidate / idealized-rectangular-site",
        )
        report = _execute(
            client, capabilities, model_id=MODEL, tool=TOOL,
            arguments={INPUT: ref["version_id"]}, input_versions={ref["version_id"]},
            data_nature=SYNTHETIC_TEST_ONLY,
        )
        outputs = {item["output_name"]: item for item in report["output_references"]}

        def download(name: str) -> bytes:
            return _download(client, outputs, name)

        summary = json.loads(download("porous-calculation-summary"))
        assert summary["normalTermination"] is True
        assert summary["transport"]["normalTermination"] is True
        rows = json.loads(download("porous-analysis"))
        for quantity in ("head", "concentration"):
            records = [row for row in rows if row["quantity"] == quantity]
            assert [row["time"] for row in records] == list(range(300, 2401, 300))
            assert all(row["validCells"] == 1500 for row in records)
        assert math.isclose(rows[7]["mean"], 27.41500000254313, rel_tol=1e-5, abs_tol=1e-7)
        numerics = summary["transport"]["numerics"]
        assert numerics["method"] == "conservative-afc.v1"
        assert numerics["crossDispersionRetained"] is True
        assert numerics["postprocessingClipping"] is False
        assert numerics["stepCount"] == 240 and numerics["minimumAcrossAllSteps"] >= 0
        assert numerics["maxRelativePairMassResidual"] < 1e-10
        assert summary["transport"]["quality"]["negativeCellRecords"] == 0
        # Version-2 native reference, separately checked for conservation and
        # rotated-tensor convergence. The legacy concentration reference is
        # retained in the before/after report; its tolerance is not relaxed.
        assert math.isclose(rows[15]["mean"], 0.56056786, rel_tol=1e-5, abs_tol=1e-7)
        assert download("porous-figure").startswith(b"\x89PNG\r\n\x1a\n")
        with ZipFile(BytesIO(download("porous-vtk-package"))) as archive:
            assert archive.testzip() is None
            assert any(name.endswith(".vtu") for name in archive.namelist())
            assert any(name.endswith(".pvd") for name in archive.namelist())
        with ZipFile(BytesIO(download("porous-result-package"))) as archive:
            assert archive.testzip() is None
            binary_checksums = {
                name: "sha256:" + hashlib.sha256(archive.read(f"raw/{name}")).hexdigest()
                for name in ("model.hds", "transport.ucn")
            }
        report.update({
            "fixtureChecksum": "sha256:" + hashlib.sha256(fixture).hexdigest(),
            "headAndConcentrationChecksums": binary_checksums,
            "summary": summary,
            "fieldScientificEquivalence": "pending",
        })
        acceptance.report.parent.mkdir(parents=True, exist_ok=True)

        def save():
            acceptance.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

        save()
        print("Porous rectangular simulation and Data publication PASS", flush=True)
        if acceptance.assessment_fixture is not None:
            report["assessmentBridge"] = _exercise_assessment(
                client, capabilities, outputs["porous-calculation-summary"]["version_id"],
                acceptance.assessment_fixture,
            )
            save()
            print("Simulation evidence, human confirmation and assessment publication PASS", flush=True)
        if acceptance.fixtures is not None:
            report["additionalWorkflows"], report["transportChecks"] = _exercise_additional(
                client, capabilities, acceptance.fixtures, ref["version_id"], command,
            )
            save()
            print("All porous operations, remote Worker restart and cancellation PASS", flush=True)
        return report
