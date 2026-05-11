from __future__ import annotations

import ipaddress
from typing import Any

import pymysql
from app.core.settings import cfg, observium_web_password, runtime_creds
from app.integrations.netbox_api import NetBoxApiClient
from app.integrations.observium_runtime import ObserviumRuntime
from app.integrations.observium_web import ObserviumWebClient
from app.integrations.zabbix_api import ZabbixApiClient


DEFAULT_MAPPING = {
    "zabbix_to_netbox": {
        "host": "name",
        "name": "display",
        "description": "comments",
        "interfaces[0].ip": "primary_ip4.address",
    },
    "netbox_to_zabbix": {
        "name": "host",
        "primary_ip4.address": "interfaces[0].ip",
        "comments": "description",
    },
    "zabbix_to_observium": {
        "interfaces[0].ip": "hostname",
        "interfaces[0].details.community": "snmp_community",
        "interfaces[0].details.version": "snmp_version",
    },
}

_mapping = DEFAULT_MAPPING.copy()


def is_ip_address(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except Exception:
        return False


def get_zabbix_url() -> str:
    return runtime_creds.get("zabbix_url", cfg.ZABBIX_URL).rstrip("/")


def get_netbox_url() -> str:
    return runtime_creds.get("netbox_url", cfg.NETBOX_URL).rstrip("/")


def get_netbox_token() -> str:
    return runtime_creds.get("netbox_token", cfg.NETBOX_TOKEN)


def get_observium_base_url() -> str:
    return cfg.OBSERVIUM_BASE_URL.rstrip("/")


def observium_web_client() -> ObserviumWebClient:
    return ObserviumWebClient(
        base_url=get_observium_base_url(),
        username=cfg.OBSERVIUM_WEB_USER,
        password=observium_web_password(),
    )


def get_observium_db_config() -> dict[str, Any]:
    return {
        "host": cfg.OBSERVIUM_DB_HOST,
        "port": cfg.OBSERVIUM_DB_PORT,
        "user": cfg.OBSERVIUM_DB_USER,
        "password": cfg.OBSERVIUM_DB_PASSWORD,
        "database": cfg.OBSERVIUM_DB_NAME,
        "cursorclass": pymysql.cursors.DictCursor,
        "autocommit": True,
    }


def observium_runtime() -> ObserviumRuntime:
    from app.core.application import logger

    return ObserviumRuntime(
        db_config=get_observium_db_config(),
        container_name=cfg.OBSERVIUM_CONTAINER,
        logger=logger,
    )


def set_runtime_credentials(payload: dict[str, Any]) -> dict[str, Any]:
    runtime_creds.clear()
    runtime_creds.update(payload)
    return {"status": "ok", "fields": list(runtime_creds.keys())}


def get_runtime_credentials() -> dict[str, Any]:
    return {
        "zabbix_url": runtime_creds.get("zabbix_url", cfg.ZABBIX_URL),
        "zabbix_token": "***" if (runtime_creds.get("zabbix_token") or cfg.ZABBIX_TOKEN) else "",
        "zabbix_user": runtime_creds.get("zabbix_user", cfg.ZABBIX_USER),
        "netbox_url": runtime_creds.get("netbox_url", cfg.NETBOX_URL),
        "netbox_token": "***" if (runtime_creds.get("netbox_token") or cfg.NETBOX_TOKEN) else "",
        "observium_base_url": get_observium_base_url(),
    }


def get_mapping() -> dict[str, Any]:
    return _mapping


def set_mapping(body: dict[str, Any]) -> dict[str, Any]:
    _mapping.clear()
    _mapping.update(body)
    return {"status": "ok", "mapping": _mapping}


def get_settings_snapshot() -> dict[str, Any]:
    return {
        "credentials": get_runtime_credentials(),
        "mapping": get_mapping(),
        "tls": {
            "outbound_verify": cfg.OUTBOUND_TLS_VERIFY,
            "zabbix_verify": cfg.ZABBIX_TLS_VERIFY,
            "netbox_verify": cfg.NETBOX_TLS_VERIFY,
            "observium_web_verify": cfg.OBSERVIUM_WEB_TLS_VERIFY,
        },
        "secret_provider": cfg.SECRET_PROVIDER,
    }


async def check_zabbix_connection() -> dict[str, Any]:
    try:
        client = ZabbixApiClient(
            base_url=get_zabbix_url(),
            tls_verify=cfg.ZABBIX_TLS_VERIFY,
            token=runtime_creds.get("zabbix_token", cfg.ZABBIX_TOKEN),
            username=runtime_creds.get("zabbix_user", cfg.ZABBIX_USER),
            password=runtime_creds.get("zabbix_pass", cfg.ZABBIX_PASS),
        )
        auth = await client.authenticate()
        result = await client.request("apiinfo.version", {}, auth=auth)
        return {"ok": True, "version": result["result"]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def check_netbox_connection() -> dict[str, Any]:
    try:
        client = NetBoxApiClient(
            base_url=get_netbox_url(),
            token=get_netbox_token(),
            tls_verify=cfg.NETBOX_TLS_VERIFY,
        )
        result = await client.request("GET", "/status/")
        return {"ok": True, "version": result["result"].get("netbox-version", "?")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


async def check_observium_connection() -> dict[str, Any]:
    try:
        row = observium_runtime().db_query("SELECT COUNT(*) AS total FROM devices", fetch="one")
        return {"ok": True, "base_url": get_observium_base_url(), "devices": row["total"]}
    except Exception as exc:
        client = observium_web_client()
        if client.configured():
            try:
                devices = client.device_entities()
                return {"ok": True, "base_url": get_observium_base_url(), "devices": len(devices), "mode": "web_session"}
            except Exception as web_exc:
                return {"ok": False, "error": str(web_exc), "fallback_error": str(exc)}
        return {"ok": False, "error": str(exc)}
