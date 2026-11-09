from __future__ import annotations

from pathlib import Path

import yaml

from deployment_topology import (
    ASSEMBLED_NETWORKS,
    HARDENING_EXCEPTIONS,
    SERVICE_NETWORKS,
    apply_assembled_topology,
)


ROOT = Path(__file__).resolve().parents[2]


def test_assembled_topology_has_only_declared_network_roles() -> None:
    services = {role: {} for role in SERVICE_NETWORKS}

    topology = apply_assembled_topology(services, "groundwater-demo")

    assert set(topology["networks"]) == set(ASSEMBLED_NETWORKS)
    assert topology["networks"]["v6-egress"] == {}
    assert topology["networks"]["v6-ingress"] == {}
    assert all(
        topology["networks"][name] == {"internal": True}
        for name in set(ASSEMBLED_NETWORKS) - {"v6-ingress", "v6-egress"}
    )
    assert topology["services"]["gateway"]["networks"] == {
        "v6-ingress": {},
        "v6-service": {},
    }
    assert topology["services"]["kernel-service"]["networks"] == {
        "v6-service": {},
        "v6-egress": {},
    }
    assert topology["services"]["data-db"]["networks"] == {"v6-data-store": {}}
    assert topology["services"]["executor-control"]["networks"] == {
        "v6-service": {},
        "v6-executor": {},
    }


def test_topology_applies_hardening_except_registered_roles() -> None:
    services = {role: {} for role in SERVICE_NETWORKS}

    topology = apply_assembled_topology(services, "groundwater-demo")

    for role, service in topology["services"].items():
        labels = service["labels"]
        assert labels["io.platform.project"] == "groundwater-demo"
        assert labels["io.platform.role"] == role
        assert "/var/run/docker.sock" not in str(service.get("volumes", []))
        if role in HARDENING_EXCEPTIONS:
            assert (
                labels["io.platform.hardening-exception"] == HARDENING_EXCEPTIONS[role]
            )
            assert "read_only" not in service
        else:
            assert service["read_only"] is True
            assert service["cap_drop"] == ["ALL"]
            assert "no-new-privileges:true" in service["security_opt"]
            assert service["tmpfs"]
    gateway = topology["services"]["gateway"]
    assert gateway["user"] == "101:101"
    assert any(
        value.startswith("/var/cache/nginx:")
        and "uid=101" in value
        and "gid=101" in value
        and "mode=0700" in value
        for value in gateway["tmpfs"]
    )


def test_independent_data_and_knowledge_compose_follow_the_topology_contract() -> None:
    data = yaml.safe_load(
        (ROOT / "services/data/compose.yaml").read_text(encoding="utf-8")
    )
    knowledge = yaml.safe_load(
        (ROOT / "services/knowledge-next/compose.yaml").read_text(encoding="utf-8")
    )
    integration = yaml.safe_load(
        (ROOT / "services/knowledge-next/compose.data.yaml").read_text(encoding="utf-8")
    )

    assert data["networks"]["data-contract"]["name"] == (
        "${V6_DATA_CONTRACT_NETWORK:-groundwater-v6-data-contract}"
    )
    assert data["networks"]["data-contract"]["internal"] is True
    assert data["services"]["api"]["networks"]["data-contract"]["aliases"] == [
        "data-api"
    ]
    assert set(data["services"]["api"]["networks"]) == {
        "data-store",
        "data-service",
        "data-contract",
    }
    assert set(data["services"]["api"]["ports"]) == {
        "127.0.0.1:${DATA_PORT:-8110}:8000"
    }
    assert not data["services"]["data-db"].get("ports")
    assert data["services"]["api"]["read_only"] is True
    assert data["services"]["worker"]["cap_drop"] == ["ALL"]
    assert (
        data["services"]["data-db"]["labels"]["io.platform.hardening-exception"]
        == "database"
    )
    assert "volumes" not in data
    assert data["services"]["data-db"]["volumes"][0]["source"] == (
        "${PLATFORM_INSTANCE_DATA_ROOT:?required}/data/database"
    )
    assert data["services"]["api"]["volumes"][0]["source"] == (
        "${PLATFORM_INSTANCE_DATA_ROOT:?required}/data/objects"
    )
    assert all(
        mount["bind"] == {"create_host_path": False}
        for name in ("data-db", "api", "worker")
        for mount in data["services"][name].get("volumes", [])
    )

    assert knowledge["services"]["api"]["ports"] == [
        "127.0.0.1:${KNOWLEDGE_PORT:-8120}:8000"
    ]
    assert knowledge["services"]["api"]["read_only"] is True
    assert knowledge["services"]["knowledge-db"]["labels"][
        "io.platform.hardening-exception"
    ] == ("database")
    assert "volumes" not in knowledge
    assert knowledge["services"]["knowledge-db"]["volumes"][0]["source"] == (
        "${PLATFORM_INSTANCE_DATA_ROOT:?required}/knowledge/database"
    )
    assert integration["networks"]["data-contract"] == {
        "external": True,
        "name": "${V6_DATA_CONTRACT_NETWORK:?V6_DATA_CONTRACT_NETWORK is required}",
    }
    assert integration["services"]["api"]["environment"]["KNOWLEDGE_DATA_URL"] == (
        "http://data-api:8000"
    )
