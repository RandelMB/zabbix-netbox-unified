from __future__ import annotations

import re
import urllib.parse
from typing import Any

from fastapi import HTTPException

from app.shared.text import compact_text


def zabbix_host_search_ui_url(host: dict[str, Any], base_url: str) -> str:
    interfaces = host.get("interfaces") or []
    preferred = next((item for item in interfaces if str(item.get("main")) == "1"), None) or (interfaces[0] if interfaces else None) or {}
    ip = compact_text(preferred.get("ip")) if str(preferred.get("useip", "1")) == "1" else ""
    dns = compact_text(preferred.get("dns")) if str(preferred.get("useip", "1")) != "1" else ""
    query = urllib.parse.urlencode(
        {
            "name": "" if ip else compact_text(host.get("host")),
            "ip": ip,
            "dns": dns,
            "port": compact_text(preferred.get("port")),
            "status": "-1",
            "evaltype": "0",
            "maintenance_status": "1",
            "filter_name": "",
            "filter_show_counter": "0",
            "filter_custom_time": "0",
            "sort": "name",
            "sortorder": "ASC",
            "show_suppressed": "0",
            "action": "host.view",
        }
    )
    return f"{base_url}/zabbix.php?{query}&tags%5B0%5D%5Btag%5D=&tags%5B0%5D%5Boperator%5D=0&tags%5B0%5D%5Bvalue%5D="


def first_non_empty(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def zabbix_macro_value(host: dict[str, Any], name: str | None) -> str | None:
    target = compact_text(name)
    if not target:
        return None
    for macro in host.get("macros", []):
        if compact_text(macro.get("macro")) == target:
            return compact_text(macro.get("value"))
    for macro in host.get("globalmacros", []):
        if compact_text(macro.get("macro")) == target:
            return compact_text(macro.get("value"))
    return None


def resolve_zabbix_macro_text(host: dict[str, Any], value: str | None) -> str | None:
    text = compact_text(value)
    if not text:
        return None
    if re.fullmatch(r"\{\$[^}]+\}", text):
        return zabbix_macro_value(host, text) or text
    return text


def map_security_level(raw: str | None) -> str:
    value = (raw or "").strip().lower()
    mapping = {
        "0": "nanp",
        "1": "anp",
        "2": "ap",
        "noauthnopriv": "nanp",
        "authnopriv": "anp",
        "authpriv": "ap",
        "nanp": "nanp",
        "anp": "anp",
        "ap": "ap",
    }
    return mapping.get(value, "ap")


def map_auth_protocol(raw: str | None) -> str | None:
    value = (raw or "").strip().lower()
    mapping = {"0": "MD5", "1": "SHA", "md5": "MD5", "sha": "SHA"}
    return mapping.get(value)


def map_priv_protocol(raw: str | None) -> str | None:
    value = (raw or "").strip().lower()
    mapping = {"0": "DES", "1": "AES", "des": "DES", "aes": "AES"}
    return mapping.get(value)


def normalize_zabbix_snmp(interface: dict[str, Any], host: dict[str, Any]) -> dict[str, Any] | None:
    details = interface.get("details") or {}
    version = str(details.get("version", "")).strip()
    community = resolve_zabbix_macro_text(host, details.get("community")) or zabbix_macro_value(host, "{$SNMP_COMMUNITY}")
    if version == "1" and community:
        return {"version": "v1", "community": community}
    if version == "2" and community:
        return {"version": "v2c", "community": community}
    if version == "3":
        security_name = first_non_empty(details, "securityname", "security_name")
        if not security_name:
            return None
        return {
            "version": "v3",
            "security_name": security_name,
            "security_level": map_security_level(first_non_empty(details, "securitylevel", "security_level")),
            "auth_protocol": map_auth_protocol(first_non_empty(details, "authprotocol", "auth_protocol")),
            "auth_password": first_non_empty(details, "authpassphrase", "auth_password", "authpass"),
            "priv_protocol": map_priv_protocol(first_non_empty(details, "privprotocol", "priv_protocol")),
            "priv_password": first_non_empty(details, "privpassphrase", "priv_password", "privpass"),
        }
    return None


def extract_zabbix_snmp_host(host: dict[str, Any]) -> dict[str, Any]:
    for interface in host.get("interfaces", []):
        if interface.get("type") != "2":
            continue
        address = interface.get("ip") if interface.get("useip") == "1" else interface.get("dns")
        if not address:
            continue
        snmp = normalize_zabbix_snmp(interface, host)
        if not snmp:
            continue
        return {
            "hostid": str(host["hostid"]),
            "host": host.get("host") or address,
            "name": host.get("name") or host.get("host") or address,
            "address": address,
            "port": int(interface.get("port") or 161),
            "interface": interface,
            "snmp": snmp,
            "inventory": host.get("inventory") or {},
        }
    raise HTTPException(400, f"Host {host.get('host')} has no usable SNMP interface")
