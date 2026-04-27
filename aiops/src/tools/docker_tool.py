"""
Docker/control helpers for ASHIA remediation actions.

This dedicated tool adapter aligns the runtime layout with the formal spec:
the remediation agent plans and risk-gates, while infrastructure mutation lives
behind a dedicated execution module.
"""

from __future__ import annotations

import os

import docker

CONTAINER_NAME_MAP = {
    "order-service": "ashia-order-service",
    "user-service": "ashia-user-service",
    "api-gateway": "ashia-api-gateway",
}


def restart_container(service: str) -> str:
    client = docker.from_env()
    cname = CONTAINER_NAME_MAP.get(service, service)
    container = client.containers.get(cname)
    container.restart(timeout=10)
    return f"Container {cname} restarted successfully"


def flush_cache() -> str:
    import redis as redis_lib

    redis_client = redis_lib.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"))
    redis_client.flushdb()
    return "Redis cache flushed successfully"


def _post_service_action(service: str, path: str, params: dict | None = None) -> str:
    import httpx

    kwargs = {"timeout": 5.0}
    if params is not None:
        kwargs["params"] = params
    response = httpx.post(f"{_service_url(service)}{path}", **kwargs)
    raise_for_status = getattr(response, "raise_for_status", None)
    if callable(raise_for_status):
        raise_for_status()
    return response.text


def scale_service(service: str, replicas: int) -> str:
    response_text = _post_service_action(service, "/fault/scale", {"replicas": replicas})
    return f"Scale-up executed for {service}: {response_text}"


def reset_memory_fault(service: str) -> str:
    _post_service_action(service, "/fault/reset")
    return f"Memory fault reset on {service}"


def config_patch_service(service: str, parameters: dict) -> str:
    patch_params = {"mode": parameters.get("mode", "stabilize")}
    if service == "user-service":
        patch_params = {"max_connections": int(parameters.get("max_connections", 50))}
    response_text = _post_service_action(service, "/fault/config-patch", patch_params)
    return f"Config patch executed for {service}: {response_text}"


def reset_db_connection_fault() -> str:
    _post_service_action("user-service", "/fault/reset")
    return "DB connection fault reset"


def rollback_service(service: str, target_version: str) -> str:
    response_text = _post_service_action(
        service, "/fault/rollback", {"target_version": target_version}
    )
    return f"Rollback requested for {service}: {response_text}"


def _service_url(service: str) -> str:
    service_url_map = {
        "order-service": os.getenv("ORDER_SERVICE_URL", "http://localhost:8002"),
        "user-service": os.getenv("USER_SERVICE_URL", "http://localhost:8001"),
        "api-gateway": os.getenv("API_GATEWAY_URL", "http://localhost:8000"),
    }
    return service_url_map.get(service, os.getenv("ORDER_SERVICE_URL", "http://localhost:8002"))
