from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.core.settings import cfg, runtime_creds
from app.domain.netbox_normalization import extract_main_zabbix_ip
from app.domain.zabbix_mapping import extract_zabbix_snmp_host, zabbix_host_search_ui_url
from app.integrations.zabbix_api import ZabbixApiClient
from app.repositories.archive_repository import archive_status
from app.services.archive_service import apply_archive_mode
from app.services.settings_service import get_zabbix_url
from app.shared.networking import host_part, parse_mac_address
from app.shared.text import compact_text, normalize_key


_zabbix_auth_token: str | None = None


def _client() -> ZabbixApiClient:
    token = runtime_creds.get("zabbix_token", cfg.ZABBIX_TOKEN)
    return ZabbixApiClient(
        base_url=get_zabbix_url(),
        tls_verify=cfg.ZABBIX_TLS_VERIFY,
        token=token,
        username=runtime_creds.get("zabbix_user", cfg.ZABBIX_USER),
        password=runtime_creds.get("zabbix_pass", cfg.ZABBIX_PASS),
    )


async def authenticate() -> str:
    global _zabbix_auth_token
    token = runtime_creds.get("zabbix_token", cfg.ZABBIX_TOKEN)
    if token:
        _zabbix_auth_token = token
        return token
    _zabbix_auth_token = await _client().authenticate()
    return _zabbix_auth_token


async def request(method: str, params: dict[str, Any]) -> Any:
    auth = await authenticate()
    return await _client().request(method, params, auth=auth)


async def global_macros() -> list[dict[str, Any]]:
    result = await request("usermacro.get", {"output": "extend", "globalmacro": True})
    return result.get("result") or []


async def host_items(host_id: str) -> list[dict[str, Any]]:
    result = await request(
        "item.get",
        {
            "output": ["itemid", "hostid", "name", "key_", "snmp_oid", "lastvalue", "value_type", "status", "state", "error"],
            "hostids": [host_id],
            "filter": {"status": 0},
        },
    )
    return result.get("result") or []


async def find_serial_item(host_id: str) -> dict[str, Any] | None:
    candidates = await host_items(host_id)
    for item in candidates:
        key_name = compact_text(item.get("key_")).lower()
        item_name = compact_text(item.get("name")).lower()
        snmp_oid = compact_text(item.get("snmp_oid"))
        if "entphysicalserialnum" in key_name or "entphysicalserialnum" in item_name or ".1.3.6.1.2.1.47.1.1.1.1.11.1" in snmp_oid:
            return item
    return None


async def ensure_serial_item(host_id: str) -> dict[str, Any]:
    existing = await find_serial_item(host_id)
    if existing:
        return {"status": "exists", "item": existing}
    host_result = await request("host.get", {"output": ["hostid"], "hostids": [host_id], "selectInterfaces": "extend"})
    if not host_result.get("result"):
        raise HTTPException(404, "Zabbix host not found")
    host = host_result["result"][0]
    interface = next((item for item in host.get("interfaces", []) if str(item.get("type")) == "2"), None)
    if not interface:
        raise HTTPException(400, "No SNMP interface available on the correlated Zabbix host")
    created = await request(
        "item.create",
        {
            "hostid": host_id,
            "interfaceid": interface["interfaceid"],
            "type": 20,
            "value_type": 4,
            "name": "SNMP entPhysicalSerialNum",
            "key_": "snmp.entPhysicalSerialNum",
            "snmp_oid": ".1.3.6.1.2.1.47.1.1.1.1.11.1",
            "delay": "1h",
        },
    )
    return {"status": "created", "result": created.get("result")}


def serial_candidate_from_host(host: dict[str, Any] | None, serial_item: dict[str, Any] | None) -> str | None:
    inventory = (host or {}).get("inventory") or {}
    for key in ("serialno_a", "serialno_b", "serialno"):
        candidate = compact_text(inventory.get(key))
        if candidate:
            return candidate
    latest = compact_text((serial_item or {}).get("lastvalue"))
    return latest or None


async def hosts(limit: int = 500, archived: str = "exclude") -> dict[str, Any]:
    global_macros_value = await global_macros()
    result = await request(
        "host.get",
        {
            "output": "extend",
            "selectInterfaces": "extend",
            "selectInventory": "extend",
            "selectTags": "extend",
            "selectGroups": "extend",
            "selectMacros": "extend",
            "limit": limit,
        },
    )
    records = result["result"] or []
    for record in records:
        record["globalmacros"] = global_macros_value
        record["ui_url"] = zabbix_host_search_ui_url(record, get_zabbix_url())
    return {**result, "result": apply_archive_mode("zabbix", records, "hostid", archived)}


async def host(host_id: str) -> dict[str, Any]:
    global_macros_value = await global_macros()
    result = await request(
        "host.get",
        {
            "output": "extend",
            "hostids": [host_id],
            "selectInterfaces": "extend",
            "selectInventory": "extend",
            "selectTags": "extend",
            "selectGroups": "extend",
            "selectMacros": "extend",
        },
    )
    if result["result"]:
        result["result"][0]["globalmacros"] = global_macros_value
        result["result"][0]["archived"] = archive_status("zabbix", host_id)
        result["result"][0]["ui_url"] = zabbix_host_search_ui_url(result["result"][0], get_zabbix_url())
    return result


async def create_host(body: dict[str, Any]) -> dict[str, Any]:
    return await request("host.create", body)


async def update_host(host_id: str, body: dict[str, Any]) -> dict[str, Any]:
    payload = dict(body)
    payload["hostid"] = host_id
    return await request("host.update", payload)


async def interfaces(host_id: str) -> dict[str, Any]:
    return await request("hostinterface.get", {"output": "extend", "hostids": [host_id]})


async def create_interface(body: dict[str, Any]) -> dict[str, Any]:
    return await request("hostinterface.create", body)


async def update_interface(interface_id: str, body: dict[str, Any]) -> dict[str, Any]:
    payload = dict(body)
    payload["interfaceid"] = interface_id
    return await request("hostinterface.update", payload)


async def delete_interface(interface_id: str) -> dict[str, Any]:
    return await request("hostinterface.delete", [interface_id])


async def groups() -> dict[str, Any]:
    return await request("hostgroup.get", {"output": "extend"})


async def find_host_by_identity(name: str, ip_value: str = "", mac_address: str = "") -> dict[str, Any] | None:
    records = (await hosts(limit=1000, archived="all")).get("result") or []
    target_name = normalize_key(name)
    target_ip = host_part(ip_value) if ip_value else ""
    target_mac = parse_mac_address(mac_address)
    for candidate in records:
        if target_name and normalize_key(candidate.get("host")) == target_name:
            return candidate
        host_ip = host_part(extract_main_zabbix_ip(candidate) or "")
        if target_ip and host_ip == target_ip:
            return candidate
        inventory = candidate.get("inventory") or {}
        for key in ("macaddress_a", "macaddress_b", "macaddress_c"):
            if target_mac and parse_mac_address(inventory.get(key)) == target_mac:
                return candidate
    return None


__all__ = [
    "create_host",
    "create_interface",
    "delete_interface",
    "ensure_serial_item",
    "extract_zabbix_snmp_host",
    "find_host",
    "find_host_by_identity",
    "find_serial_item",
    "global_macros",
    "groups",
    "host",
    "hosts",
    "interfaces",
    "request",
    "serial_candidate_from_host",
    "update_host",
    "update_interface",
]


async def find_host(host_id: str) -> dict[str, Any] | None:
    result = await host(host_id)
    items = result.get("result") or []
    return items[0] if items else None
