import json
import ipaddress
import re
import urllib.parse
import subprocess
import asyncio
from typing import Any, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field
from app.core.settings import cfg, runtime_creds
from app.integrations.netbox_api import NetBoxApiClient
from app.integrations.zabbix_api import ZabbixApiClient
from app.repositories.app_db import app_db, init_app_db
from app.repositories.archive_repository import archive_status, log_export
from app.repositories.correlations_repository import find_correlation_by_item, list_correlations
from app.repositories.sync_profiles_repository import (
    SYNC_FIELDS,
    SYNC_SOURCE_DEFAULTS,
    get_netbox_sync_profile,
    save_netbox_sync_profile,
)
from app.schemas.correlations import CorrelationLinkPayload
from app.schemas.interface_sync import NetBoxInterfaceSyncRunPayload
from app.schemas.snmp import (
    NetBoxSnmpImportPayload as NetBoxSnmpImportPayloadSchema,
    NetBoxSnmpProbePayload as NetBoxSnmpProbePayloadSchema,
)
from app.services.archive_service import apply_archive_mode
from app.services.settings_service import (
    get_netbox_token,
    get_netbox_url,
    get_observium_base_url,
    get_zabbix_url,
    is_ip_address,
    observium_runtime,
    observium_web_client,
)
from app.services.interface_sync import (
    InterfaceSyncRuntime,
    apply_sync as apply_interface_sync_service,
    build_interface_sync_actions,
    build_preview as build_interface_sync_preview_service,
    match_observium_ports_to_netbox,
    netbox_interface_summary,
    pick_management_ip_candidate,
)
from app.services.snmp_discovery import (
    SnmpDiscoveryRuntime,
    build_netbox_snmp_probe as build_netbox_snmp_probe_service,
    import_netbox_device_from_snmp as import_netbox_device_from_snmp_service,
)
from app.services.sync_locks import interface_sync_lock
from app.services.topology_export_service import fetch_all_netbox_devices as fetch_all_netbox_devices_service
from app.shared.text import as_int, compact_text, normalize_key, slugify_text
from app.shared.vlan import (
    interface_vlan_representation,
    is_vlan_tag_slug,
    merge_interface_vlan_tags,
    summarize_vlan_ids,
    vlan_representation_tokens,
    vlan_tag_name_and_slug,
    vlan_token_sort_key,
)

_zabbix_auth_token: Optional[str] = None
_observium_device_columns_cache: Optional[set[str]] = None
_observium_table_columns_cache: dict[str, set[str]] = {}

class ObserviumDeviceCreate(BaseModel):
    hostname: str
    snmp_version: str = "v2c"
    snmp_community: Optional[str] = None
    snmp_port: int = 161
    snmp_transport: str = "udp"
    snmp_authlevel: Optional[str] = None
    snmp_authname: Optional[str] = None
    snmp_authpass: Optional[str] = None
    snmp_authalgo: Optional[str] = None
    snmp_cryptopass: Optional[str] = None
    snmp_cryptoalgo: Optional[str] = None
    snmp_context: Optional[str] = None
    label: Optional[str] = None
    location: Optional[str] = None
    purpose: Optional[str] = None
    skip_icmp: bool = False
    run_discovery: bool = True
    run_poller: bool = False


class ExportZabbixToObserviumPayload(BaseModel):
    hostids: list[str]
    run_discovery: bool = True
    run_poller: bool = False
    update_existing: bool = True

class NetBoxEnrichmentActionPayload(BaseModel):
    action_type: str
    value: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class NetBoxEnrichmentApplyPayload(BaseModel):
    actions: list[NetBoxEnrichmentActionPayload] = Field(default_factory=list)


class NetBoxPrimaryIpFixPayload(BaseModel):
    dry_run: bool = False


class NetBoxSyncProfilePayload(BaseModel):
    enabled: bool = False
    field_sources: dict[str, str] = Field(default_factory=dict)


class NetBoxLldpApplyPayload(BaseModel):
    proposal_ids: list[str] = Field(default_factory=list)
    role_id: Optional[int] = None
    site_id: Optional[int] = None


class DiscoveryLldpPreviewPayload(BaseModel):
    source: str
    source_id: str


class DiscoveryLldpApplyPayload(BaseModel):
    source: str
    source_id: str
    proposal_ids: list[str] = Field(default_factory=list)
    site_id: Optional[int] = None
    role_id: Optional[int] = None


def on_startup() -> None:
    init_app_db()




def zabbix_host_search_ui_url(host: dict[str, Any]) -> str:
    base = get_zabbix_url()
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
    return f"{base}/zabbix.php?{query}&tags%5B0%5D%5Btag%5D=&tags%5B0%5D%5Boperator%5D=0&tags%5B0%5D%5Bvalue%5D="


def observium_db_query(query: str, params: tuple[Any, ...] = (), fetch: str = "all") -> Any:
    return observium_runtime().db_query(query, params=params, fetch=fetch)


def observium_exec(args: list[str]) -> dict[str, Any]:
    result = observium_runtime().exec(args, fail_on_error=True)
    return {"command": result["command"], "output": result["output"]}


def observium_exec_result(args: list[str]) -> dict[str, Any]:
    return observium_runtime().exec(args, fail_on_error=False)


def is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, dict, set, tuple)):
        return len(value) == 0
    return False


def normalize_ip_value(address: str) -> str:
    trimmed = compact_text(address)
    if not trimmed:
        return ""
    if "/" in trimmed:
        return trimmed
    return f"{trimmed}/128" if ":" in trimmed else f"{trimmed}/32"


def host_part(address: str) -> str:
    return compact_text(address).split("/")[0]


def ipv4_candidate(value: Any) -> Optional[str]:
    raw = compact_text(value)
    if not raw:
        return None
    try:
        parsed = ipaddress.ip_interface(raw if "/" in raw else f"{raw}/32")
    except ValueError:
        return None
    if parsed.version != 4:
        return None
    return str(parsed)


def description_ip_candidate(device: dict[str, Any]) -> Optional[str]:
    return ipv4_candidate(device.get("description"))


def extract_main_zabbix_ip(host: dict[str, Any]) -> Optional[str]:
    interfaces = host.get("interfaces") or []
    preferred = next((item for item in interfaces if str(item.get("main")) == "1"), None) or (interfaces[0] if interfaces else None)
    if not preferred:
        return None
    raw = preferred.get("ip") if str(preferred.get("useip", "1")) == "1" else preferred.get("dns")
    return ipv4_candidate(raw)


def extract_observium_ipv4(device: dict[str, Any]) -> Optional[str]:
    return ipv4_candidate(device.get("ip"))


def build_enriched_comments(device: dict[str, Any], zabbix_host: Optional[dict[str, Any]], observium_device: Optional[dict[str, Any]]) -> str:
    lines: list[str] = []
    sys_name = compact_text((observium_device or {}).get("sysName"))
    location = compact_text((observium_device or {}).get("location") or ((zabbix_host or {}).get("inventory") or {}).get("location"))
    version = compact_text((observium_device or {}).get("version") or (zabbix_host or {}).get("vendor_version"))
    os_name = compact_text((observium_device or {}).get("os"))
    hardware = compact_text((observium_device or {}).get("hardware"))

    if sys_name:
        lines.append(f"sysName: {sys_name}")
    if location:
        lines.append(f"SNMP location: {location}")
    if hardware or os_name or version:
        parts = [part for part in [hardware, os_name, version] if part]
        lines.append("Platform detail: " + " | ".join(parts))

    current = compact_text(device.get("comments"))
    if current:
        lines.insert(0, current)
    return "\n".join(dict.fromkeys(line for line in lines if line))


def build_enriched_description(device: dict[str, Any], zabbix_host: Optional[dict[str, Any]], observium_device: Optional[dict[str, Any]]) -> str:
    hardware = compact_text((observium_device or {}).get("hardware"))
    version = compact_text((observium_device or {}).get("version"))
    os_name = compact_text((observium_device or {}).get("os"))
    visible_name = compact_text((zabbix_host or {}).get("name"))
    parts = [part for part in [hardware, os_name, version] if part]
    if parts:
        return " | ".join(parts)
    if visible_name and visible_name != compact_text((zabbix_host or {}).get("host")):
        return visible_name
    return compact_text(device.get("description"))


def build_os_version_label(os_name: Optional[str], version: Optional[str]) -> str:
    os_value = compact_text(os_name)
    version_value = compact_text(version)
    return compact_text(" ".join(part for part in (os_value, version_value) if part))


def snmp_discovery_runtime() -> SnmpDiscoveryRuntime:
    return SnmpDiscoveryRuntime(
        observium_exec_result=observium_exec_result,
        observium_find_device_by_identity=observium_find_device_by_identity,
        zabbix_find_host_by_identity=zabbix_find_host_by_identity,
        netbox_find_device_by_identity=netbox_find_device_by_identity,
        netbox_find_device_type_by_model=netbox_find_device_type_by_model,
        ensure_netbox_device_type=ensure_netbox_device_type,
        ensure_netbox_platform=ensure_netbox_platform,
        ensure_netbox_primary_ip4=ensure_netbox_primary_ip4,
        netbox_request=netbox_request,
        normalize_ip_value=normalize_ip_value,
        build_os_version_label=build_os_version_label,
    )


def build_zabbix_platform_label(host: Optional[dict[str, Any]]) -> str:
    inventory = (host or {}).get("inventory") or {}
    return build_os_version_label(
        inventory.get("os_full") or inventory.get("os") or (host or {}).get("vendor_name"),
        (host or {}).get("vendor_version") or inventory.get("os_short") or inventory.get("software_full") or inventory.get("software"),
    )


def build_observium_platform_label(device: Optional[dict[str, Any]]) -> str:
    return build_os_version_label((device or {}).get("os"), (device or {}).get("version"))


def pick_platform_name(device: dict[str, Any], zabbix_host: Optional[dict[str, Any]], observium_device: Optional[dict[str, Any]]) -> Optional[str]:
    return (
        build_observium_platform_label(observium_device)
        or build_zabbix_platform_label(zabbix_host)
        or compact_text((device.get("platform") or {}).get("display") or (device.get("platform") or {}).get("name"))
        or None
    )


async def netbox_paginated(path: str, params: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    query = dict(params or {})
    query.setdefault("limit", 500)
    result = await netbox_request("GET", path, query)
    payload = result.get("result")
    if isinstance(payload, dict) and "results" in payload:
        return payload.get("results", [])
    if isinstance(payload, list):
        return payload
    return []


async def netbox_find_platform(name: str) -> Optional[dict[str, Any]]:
    target = normalize_key(name)
    for item in await netbox_paginated("/dcim/platforms/"):
        if normalize_key(item.get("name")) == target or normalize_key(item.get("display")) == target:
            return item
    return None


async def ensure_netbox_platform(name: str) -> dict[str, Any]:
    existing = await netbox_find_platform(name)
    if existing:
        return {"created": False, "platform": existing}
    created = await netbox_request("POST", "/dcim/platforms/", {"name": name, "slug": slugify_text(name)[:100]})
    return {"created": True, "platform": created.get("result") or created.get("response") or {}}


async def netbox_find_manufacturer(name: str) -> Optional[dict[str, Any]]:
    target = normalize_key(name)
    if not target:
        return None
    for item in await netbox_paginated("/dcim/manufacturers/", {"limit": 500}):
        if normalize_key(item.get("name")) == target or normalize_key(item.get("display")) == target:
            return item
    return None


async def ensure_netbox_manufacturer(name: str) -> dict[str, Any]:
    existing = await netbox_find_manufacturer(name)
    if existing:
        return {"created": False, "manufacturer": existing}
    created = await netbox_request("POST", "/dcim/manufacturers/", {"name": name, "slug": slugify_text(name)[:100]})
    return {"created": True, "manufacturer": created.get("result") or created.get("response") or {}}


async def netbox_find_location(name: str, site_id: Optional[int]) -> Optional[dict[str, Any]]:
    params: dict[str, Any] = {}
    if site_id:
        params["site_id"] = site_id
    target = normalize_key(name)
    for item in await netbox_paginated("/dcim/locations/", params):
        if normalize_key(item.get("name")) == target or normalize_key(item.get("display")) == target:
            return item
    return None


async def ensure_netbox_location(name: str, site_id: Optional[int]) -> dict[str, Any]:
    existing = await netbox_find_location(name, site_id)
    if existing:
        return {"created": False, "location": existing}
    if not site_id:
        raise HTTPException(400, "NetBox site is required to create a location")
    created = await netbox_request(
        "POST",
        "/dcim/locations/",
        {"name": name, "slug": slugify_text(name)[:90], "site": int(site_id)},
    )
    return {"created": True, "location": created.get("result") or created.get("response") or {}}


def observium_table_columns(table: str) -> set[str]:
    cached = _observium_table_columns_cache.get(table)
    if cached is not None:
        return cached
    try:
        rows = observium_db_query(
            """
            SELECT COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
            """,
            (cfg.OBSERVIUM_DB_NAME, table),
        )
    except Exception:
        rows = []
    columns = {compact_text(row.get("COLUMN_NAME")) for row in rows or [] if compact_text(row.get("COLUMN_NAME"))}
    _observium_table_columns_cache[table] = columns
    return columns


def observium_table_exists(table: str) -> bool:
    return bool(observium_table_columns(table))


def parse_mac_address(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        raw_bytes = bytes(value)
        if len(raw_bytes) == 6:
            return ":".join(f"{part:02X}" for part in raw_bytes)
        try:
            value = raw_bytes.decode("utf-8", "ignore")
        except Exception:
            value = raw_bytes.hex()
    raw = compact_text(value).replace("-", ":").replace(".", "").upper()
    if not raw:
        return ""
    if "." in compact_text(value):
        chunks = [raw[i:i + 4] for i in range(0, len(raw), 4)]
        raw = ":".join(chunk[:2] + ":" + chunk[2:] for chunk in chunks if len(chunk) == 4)
    if ":" not in raw:
        compact = "".join(char for char in raw if char.isalnum())
        if len(compact) == 12:
            raw = ":".join(compact[i:i + 2] for i in range(0, 12, 2))
    parts = [part.zfill(2) for part in raw.split(":") if part]
    if len(parts) != 6 or any(len(part) != 2 for part in parts):
        return ""
    return ":".join(parts)


def humanize_speed(speed_bps: Optional[int]) -> str:
    if not speed_bps or speed_bps <= 0:
        return ""
    units = ["bps", "Kbps", "Mbps", "Gbps", "Tbps"]
    value = float(speed_bps)
    unit = units[0]
    for candidate in units:
        unit = candidate
        if value < 1000 or candidate == units[-1]:
            break
        value /= 1000.0
    return f"{value:.0f} {unit}" if value >= 100 else f"{value:.1f} {unit}"


def port_speed_bps(row: dict[str, Any]) -> Optional[int]:
    speed = as_int(first_non_empty(row, "ifSpeed", "if_speed"))
    if speed:
        return speed
    high_speed = as_int(first_non_empty(row, "ifHighSpeed", "if_high_speed"))
    if high_speed:
        return high_speed * 1_000_000
    return None


def normalize_port_name_text(value: Any) -> str:
    text = compact_text(value)
    if not text:
        return ""
    lowered = text.lower()
    patterns = (
        (r"^port\s+(\d+)$", lambda m: f"port{m.group(1)}"),
        (r"^gigabitethernet\s*([0-9/]+)$", lambda m: f"gi{m.group(1)}"),
        (r"^gi(?:gabitethernet)?\s*([0-9/]+)$", lambda m: f"gi{m.group(1)}"),
        (r"^tengigabitethernet\s*([0-9/]+)$", lambda m: f"te{m.group(1)}"),
        (r"^te\s*([0-9/]+)$", lambda m: f"te{m.group(1)}"),
        (r"^ethernet\s*([0-9/]+)$", lambda m: f"ethernet {m.group(1)}"),
        (r"^eth\s*([0-9/]+)$", lambda m: f"eth {m.group(1)}"),
        (r"^fastethernet\s*([0-9/]+)$", lambda m: f"fa{m.group(1)}"),
        (r"^fa\s*([0-9/]+)$", lambda m: f"fa{m.group(1)}"),
        (r"^port-channel\s*([0-9/]+)$", lambda m: f"port-channel{m.group(1)}"),
        (r"^po\s*([0-9/]+)$", lambda m: f"po{m.group(1)}"),
        (r"^ae\s*([0-9/]+)$", lambda m: f"ae{m.group(1)}"),
    )
    for pattern, formatter in patterns:
        match = re.match(pattern, lowered)
        if match:
            return formatter(match)
    if re.match(r"^(port\d+|gi[0-9/]+|te[0-9/]+|fa[0-9/]+|po[0-9/]+|ae[0-9/]+|eth ?[0-9/]+|ethernet ?[0-9/]+)$", lowered):
        return lowered
    return text


def looks_like_short_port_name(value: Any) -> bool:
    text = normalize_port_name_text(value)
    if not text:
        return False
    lowered = text.lower()
    return bool(
        re.match(r"^(port\d+|gi[0-9/]+|te[0-9/]+|fa[0-9/]+|po[0-9/]+|ae[0-9/]+|eth ?[0-9/]+|ethernet ?[0-9/]+|vlan ?\d+|lo\d+)$", lowered)
        or "mlag" in lowered
        or "lag" in lowered
        or "port-channel" in lowered
        or lowered.startswith("_")
    )


def first_non_empty_normalized(*values: Any) -> str:
    for value in values:
        text = normalize_port_name_text(value)
        if text:
            return text
    return ""


def pick_observium_port_name(row: dict[str, Any]) -> str:
    candidates = [
        row.get("port_label_short"),
        row.get("ifName"),
        row.get("port_label"),
        row.get("port_label_base"),
        row.get("label"),
        row.get("ifDescr"),
        row.get("port_descr"),
    ]
    for candidate in candidates:
        if looks_like_short_port_name(candidate):
            return compact_text(candidate)
    return compact_text(first_non_empty_normalized(*candidates)) or compact_text(row.get("ifIndex")) or "unnamed"


def pick_observium_port_description(row: dict[str, Any], port_name: str) -> str:
    vendor = compact_text(first_non_empty(row, "vendor", "device_vendor")).lower()
    port_number = compact_text(first_non_empty(row, "ifIndex", "if_index", "port_id"))
    generic_patterns = [
        rf"port\s*{re.escape(port_number)}$" if port_number else "",
        r"module\s*-\s*port\s*\d+$",
        r"^vlan\s*#?\d+$",
    ]
    for candidate in (
        row.get("ifAlias"),
        row.get("port_descr"),
    ):
        text = compact_text(candidate)
        lowered = text.lower()
        if not text or text == compact_text(port_name):
            continue
        if vendor and vendor in lowered and re.search(r"port\s+\d+$", lowered):
            continue
        if any(pattern and re.search(pattern, lowered) for pattern in generic_patterns):
            continue
        if looks_like_short_port_name(text):
            continue
        if text:
            return text
    return ""


def is_lag_name(value: Any) -> bool:
    text = compact_text(value).lower()
    return any(token in text for token in ("mlag", "port-channel", "lag", "ae", "bond")) or text.startswith("_")


def lag_name_candidate(row: dict[str, Any]) -> str:
    for candidate in (row.get("ifName"), row.get("ifDescr"), row.get("port_label"), row.get("ifAlias")):
        text = compact_text(candidate)
        if text and is_lag_name(text):
            return text
    return ""


def find_table_column(columns: set[str], *candidates: str) -> str:
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return ""


def pick_observium_vlan_id(row: dict[str, Any]) -> Optional[str]:
    for key in ("vlan_vlan", "vlan", "vlan_id", "ifVlan", "if_vlan", "access_vlan"):
        value = compact_text(row.get(key))
        if value and value not in {"0", "None"}:
            return value
    return None


def netbox_interface_type_candidate(row: dict[str, Any]) -> str:
    raw_type = compact_text(first_non_empty(row, "ifType", "if_type")).lower()
    speed = port_speed_bps(row) or 0
    mapping = {
        "softwareloopback": "virtual",
        "propvirtual": "virtual",
        "l2vlan": "virtual",
        "bridge": "bridge",
        "ieee8023adlag": "lag",
    }
    if raw_type in mapping:
        return mapping[raw_type]
    if raw_type == "ethernetcsmacd":
        if speed >= 100_000_000_000:
            return "100gbase-x-qsfp28"
        if speed >= 40_000_000_000:
            return "40gbase-x-qsfpp"
        if speed >= 25_000_000_000:
            return "25gbase-x-sfp28"
        if speed >= 10_000_000_000:
            return "10gbase-x-sfpp"
        if speed >= 5_000_000_000:
            return "5gbase-t"
        if speed >= 2_500_000_000:
            return "2.5gbase-t"
        if speed >= 1_000_000_000:
            return "1000base-t"
        if speed >= 100_000_000:
            return "100base-tx"
        if speed >= 10_000_000:
            return "10base-t"
        return "other"
    return ""


def summarize_oper_state(row: dict[str, Any]) -> dict[str, Any]:
    oper = compact_text(first_non_empty(row, "ifOperStatus", "if_oper_status"))
    admin = compact_text(first_non_empty(row, "ifAdminStatus", "if_admin_status"))
    speed_bps = port_speed_bps(row)
    return {
        "oper_status": oper,
        "admin_status": admin,
        "speed_bps": speed_bps,
        "speed_label": humanize_speed(speed_bps),
        "type": compact_text(first_non_empty(row, "ifType", "if_type")),
        "mtu": as_int(first_non_empty(row, "ifMtu", "if_mtu")),
    }


def normalize_observium_port(row: dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    port_name = pick_observium_port_name(data)
    state = summarize_oper_state(data)
    candidates = []
    for candidate in (
        data.get("port_label_short"),
        data.get("ifDescr"),
        data.get("ifName"),
        data.get("port_label"),
        data.get("port_label_base"),
        data.get("label"),
        data.get("port_descr"),
    ):
        text = compact_text(candidate)
        if text and text not in candidates:
            candidates.append(text)
    return {
        "port_id": as_int(data.get("port_id")) or 0,
        "device_id": as_int(data.get("device_id")) or 0,
        "ifIndex": as_int(data.get("ifIndex") or data.get("if_index")),
        "name": port_name,
        "name_candidates": candidates or [port_name],
        "description": pick_observium_port_description(data, port_name),
        "mac_address": parse_mac_address(first_non_empty(
            data,
            "ifPhysAddress",
            "ifPhysAddress_hex",
            "ifPhysAddress_text",
            "if_phys_address",
            "phys_address",
            "mac_address",
        )),
        "type": netbox_interface_type_candidate(data),
        "raw_type": state["type"],
        "admin_status": state["admin_status"],
        "oper_status": state["oper_status"],
        "enabled_candidate": state["admin_status"].lower() not in {"down", "disabled", "admin down"} if state["admin_status"] else None,
        "speed_bps": state["speed_bps"],
        "speed_label": state["speed_label"],
        "mtu": state["mtu"],
        "vlans": [],
        "lag_parent_port_id": None,
        "lag_parent_name": "",
        "lag_member_port_ids": [],
        "lag_member_names": [],
        "lag_role": "standalone",
        "lag_name": lag_name_candidate(data),
        "raw": data,
    }


def vlan_tag_slug(vlan_id: str) -> str:
    return slugify_text(f"vlan-{vlan_id}")[:100]


async def ensure_netbox_tag(name: str, slug: str) -> dict[str, Any]:
    existing = await netbox_paginated("/extras/tags/", {"slug": slug})
    if existing:
        return {"created": False, "tag": existing[0]}
    created = await netbox_request(
        "POST",
        "/extras/tags/",
        {"name": name, "slug": slug, "color": "607d8b"},
    )
    return {"created": True, "tag": created.get("result") or created.get("response") or {}}


def observium_port_stack_rows(port_ids: list[int]) -> list[dict[str, Any]]:
    if not port_ids or not observium_table_exists("ports_stack"):
        return []
    columns = observium_table_columns("ports_stack")
    high_col = find_table_column(columns, "port_id_high", "high_port_id", "higher_port_id")
    low_col = find_table_column(columns, "port_id_low", "low_port_id", "lower_port_id")
    if not high_col or not low_col:
        return []
    placeholders = ", ".join(["%s"] * len(port_ids))
    try:
        return observium_db_query(
            f"SELECT * FROM ports_stack WHERE {high_col} IN ({placeholders}) OR {low_col} IN ({placeholders})",
            tuple(port_ids + port_ids),
        )
    except Exception:
        return []


def apply_observium_lag_relationships(ports: list[dict[str, Any]]) -> None:
    port_map = {int(port["port_id"]): port for port in ports if port.get("port_id")}
    rows = observium_port_stack_rows(list(port_map.keys()))
    if rows:
        columns = observium_table_columns("ports_stack")
        high_col = find_table_column(columns, "port_id_high", "high_port_id", "higher_port_id")
        low_col = find_table_column(columns, "port_id_low", "low_port_id", "lower_port_id")
        for row in rows:
            high_id = as_int(row.get(high_col))
            low_id = as_int(row.get(low_col))
            if not high_id or not low_id or high_id not in port_map or low_id not in port_map:
                continue
            parent = port_map[high_id]
            member = port_map[low_id]
            if low_id not in parent["lag_member_port_ids"]:
                parent["lag_member_port_ids"].append(low_id)
            member["lag_parent_port_id"] = high_id

    for port in ports:
        if port.get("type") == "lag" or is_lag_name(port.get("name")) or is_lag_name(port.get("lag_name")):
            if port["lag_role"] == "standalone":
                port["lag_role"] = "parent"

    for port in ports:
        parent_id = as_int(port.get("lag_parent_port_id"))
        if parent_id and parent_id in port_map:
            parent = port_map[parent_id]
            port["lag_parent_name"] = parent.get("name") or parent.get("lag_name") or ""
            port["lag_role"] = "member"
            if int(port["port_id"]) not in parent["lag_member_port_ids"]:
                parent["lag_member_port_ids"].append(int(port["port_id"]))

    for port in ports:
        member_names = []
        for member_id in port.get("lag_member_port_ids") or []:
            member = port_map.get(member_id)
            if member:
                member_names.append(member.get("name") or "")
        port["lag_member_names"] = [name for name in member_names if name]


def netbox_site_id(device: dict[str, Any]) -> Optional[int]:
    site = device.get("site") or {}
    return as_int(site.get("id") if isinstance(site, dict) else site)


def observium_port_rows(device_id: int) -> list[dict[str, Any]]:
    try:
        if not observium_table_exists("ports"):
            raise RuntimeError("Observium DB ports table unavailable")
        rows = observium_db_query(
            """
            SELECT *
            FROM ports
            WHERE device_id = %s
            ORDER BY COALESCE(ifIndex, 0), COALESCE(port_id, 0)
            """,
            (device_id,),
        )
        ports = [normalize_observium_port(row) for row in rows or [] if not as_int(dict(row).get("deleted"))]
        if not ports:
            raise RuntimeError("Observium DB returned no ports")
        if observium_table_exists("ports_vlans"):
            port_ids = [port["port_id"] for port in ports if port.get("port_id")]
            if port_ids:
                placeholders = ", ".join(["%s"] * len(port_ids))
                try:
                    vlan_rows = observium_db_query(f"SELECT * FROM ports_vlans WHERE port_id IN ({placeholders})", tuple(port_ids))
                except Exception:
                    vlan_rows = []
                vlan_map: dict[int, list[str]] = {}
                for row in vlan_rows or []:
                    port_id = as_int(dict(row).get("port_id"))
                    vlan_id = pick_observium_vlan_id(dict(row))
                    if port_id and vlan_id:
                        vlan_map.setdefault(port_id, [])
                        if vlan_id not in vlan_map[port_id]:
                            vlan_map[port_id].append(vlan_id)
                for port in ports:
                    direct_vlan = pick_observium_vlan_id(port.get("raw") or {})
                    vlans = list(vlan_map.get(port["port_id"], []))
                    if direct_vlan and direct_vlan not in vlans:
                        vlans.append(direct_vlan)
                    port["vlans"] = vlans
        apply_observium_lag_relationships(ports)
    except Exception:
        client = observium_web_client()
        if not client.configured():
            raise
        ports = client.device_ports(device_id)

    mac_counts: dict[str, int] = {}
    for port in ports:
        mac = parse_mac_address(port.get("mac_address"))
        if mac:
            mac_counts[mac] = mac_counts.get(mac, 0) + 1
    for port in ports:
        mac = parse_mac_address(port.get("mac_address"))
        if mac and mac_counts.get(mac, 0) > 1:
            port["mac_sync_skipped"] = "duplicate_device_mac"
    return ports


async def ensure_vlan_tags(vlan_values: list[Any]) -> list[int]:
    ensured_tag_ids: list[int] = []
    for token in vlan_representation_tokens(vlan_values):
        name, slug = vlan_tag_name_and_slug(token)
        ensured = await ensure_netbox_tag(name, slug)
        tag = ensured.get("tag") or {}
        if tag.get("id") is not None:
            ensured_tag_ids.append(int(tag["id"]))
    return ensured_tag_ids


async def netbox_interface_connected_peer(interface_id: int) -> Optional[dict[str, Any]]:
    detail_result = await netbox_request("GET", f"/dcim/interfaces/{interface_id}/")
    detail = detail_result.get("result") or detail_result.get("response") or {}
    peers = detail.get("connected_endpoints") or detail.get("link_peers") or []
    if not peers:
        peer = detail.get("connected_endpoint")
        if peer:
            peers = [peer]
    for peer in peers or []:
        if compact_text(peer.get("url")) or peer.get("id") is not None:
            return {
                "id": as_int(peer.get("id")),
                "name": compact_text(peer.get("name") or peer.get("display")),
                "device_name": compact_text(((peer.get("device") or {}).get("name")) if isinstance(peer.get("device"), dict) else ""),
                "cable": detail.get("cable"),
            }
    return None


async def find_netbox_interface_by_observium_port(device_id: int, observium_port: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not device_id or not observium_port:
        return None
    interfaces = [netbox_interface_summary(item) for item in await netbox_paginated("/dcim/interfaces/", {"device_id": device_id, "limit": 500})]
    by_name: dict[str, list[dict[str, Any]]] = {}
    by_mac: dict[str, list[dict[str, Any]]] = {}
    for interface in interfaces:
        for key in interface_match_keys(interface.get("name") or ""):
            by_name.setdefault(key, []).append(interface)
        mac = parse_mac_address(interface.get("mac_address"))
        if mac:
            by_mac.setdefault(mac, []).append(interface)
    for candidate in observium_port.get("name_candidates") or [observium_port.get("name")]:
        matches = []
        for key in interface_match_keys(candidate):
            matches.extend(by_name.get(key, []))
        unique = {int(item["id"]): item for item in matches}
        if len(unique) == 1:
            return next(iter(unique.values()))
    mac = parse_mac_address(observium_port.get("mac_address"))
    if mac:
        matches = by_mac.get(mac, [])
        if len(matches) == 1:
            return matches[0]
    return None


async def resolve_interface_connection_candidate(
    *,
    local_interface: Optional[dict[str, Any]],
    local_observium_port: Optional[dict[str, Any]],
    link_rows: dict[int, list[dict[str, Any]]],
) -> Optional[dict[str, Any]]:
    if not local_observium_port:
        return None
    port_id = as_int(local_observium_port.get("port_id"))
    if not port_id:
        return None
    rows = link_rows.get(port_id) or []
    if not rows:
        return None
    row = rows[0]
    remote_device_id = as_int(first_non_empty(row, "remote_device_id", "peer_device_id", "device_id_remote"))
    remote_port_id = as_int(first_non_empty(row, "remote_port_id", "peer_port_id", "port_id_remote"))
    remote_device = observium_device_row(remote_device_id) if remote_device_id else None
    remote_port = None
    if remote_device_id:
        remote_port_rows = observium_port_rows(remote_device_id)
        remote_port = next((item for item in remote_port_rows if int(item.get("port_id") or 0) == remote_port_id), None)
    remote_mac = observium_link_neighbor_mac(row) or parse_mac_address((remote_port or {}).get("mac_address"))
    remote_ip = host_part((remote_device or {}).get("ip") or "") or (observium_ip_from_mac(remote_mac) if remote_mac else "")
    remote_name = (
        compact_text((remote_device or {}).get("sysName"))
        or compact_text((remote_device or {}).get("hostname"))
        or observium_link_neighbor_name(row)
        or compact_text((remote_port or {}).get("description"))
    )
    remote_netbox_device = await netbox_find_device_by_identity(remote_name, remote_ip, remote_mac)
    remote_netbox_interface = None
    if remote_netbox_device and remote_port:
        remote_netbox_interface = await find_netbox_interface_by_observium_port(int(remote_netbox_device["id"]), remote_port)
    current_peer = await netbox_interface_connected_peer(int(local_interface["id"])) if local_interface and local_interface.get("id") is not None else None
    return {
        "protocol": compact_text(first_non_empty(row, "protocol", "link_type")) or "LLDP/CDP",
        "remote_device": remote_device,
        "remote_port": remote_port,
        "remote_ip": remote_ip,
        "remote_mac": remote_mac,
        "remote_name": remote_name,
        "remote_netbox_device": remote_netbox_device,
        "remote_netbox_interface": remote_netbox_interface,
        "current_peer": current_peer,
    }


async def ensure_netbox_interface_cable(interface_a_id: int, interface_b_id: int) -> dict[str, Any]:
    if interface_a_id == interface_b_id:
        raise HTTPException(400, "Cannot cable an interface to itself")
    peer_a = await netbox_interface_connected_peer(interface_a_id)
    peer_b = await netbox_interface_connected_peer(interface_b_id)
    if peer_a and as_int(peer_a.get("id")) == int(interface_b_id) and peer_a.get("cable"):
        return {"created": False, "cable": peer_a["cable"]}
    if peer_a and as_int(peer_a.get("id")) not in {None, int(interface_b_id)}:
        raise HTTPException(409, f"Interface {interface_a_id} is already connected to another endpoint")
    if peer_b and as_int(peer_b.get("id")) not in {None, int(interface_a_id)}:
        raise HTTPException(409, f"Interface {interface_b_id} is already connected to another endpoint")
    created = await netbox_request(
        "POST",
        "/dcim/cables/",
        {
            "a_terminations": [{"object_type": "dcim.interface", "object_id": int(interface_a_id)}],
            "b_terminations": [{"object_type": "dcim.interface", "object_id": int(interface_b_id)}],
            "status": "connected",
        },
    )
    return {"created": True, "cable": created.get("result") or created.get("response") or {}}


def interface_sync_runtime() -> InterfaceSyncRuntime:
    return InterfaceSyncRuntime(
        clear_netbox_interface_dependencies=clear_netbox_interface_dependencies,
        correlated_sources_for_netbox_device=correlated_sources_for_netbox_device,
        description_ip_candidate=description_ip_candidate,
        ensure_netbox_interface_cable=ensure_netbox_interface_cable,
        ensure_netbox_interface_mac=ensure_netbox_interface_mac,
        ensure_netbox_primary_ip4=ensure_netbox_primary_ip4,
        ensure_vlan_tags=ensure_vlan_tags,
        extract_main_zabbix_ip=extract_main_zabbix_ip,
        extract_observium_ipv4=extract_observium_ipv4,
        first_non_empty=first_non_empty,
        log_export=log_export,
        netbox_paginated=netbox_paginated,
        netbox_request=netbox_request,
        observium_link_rows=observium_link_rows,
        observium_port_rows=observium_port_rows,
        resolve_interface_connection_candidate=resolve_interface_connection_candidate,
    )


async def build_netbox_interface_sync_preview(device_id: int) -> dict[str, Any]:
    return await build_interface_sync_preview_service(interface_sync_runtime(), device_id, parse_mac_address)


async def ensure_netbox_interface_mac(interface_id: int, address: str) -> dict[str, Any]:
    normalized = parse_mac_address(address)
    if not normalized:
        raise HTTPException(400, "MAC candidate is empty")
    existing = await netbox_paginated(
        "/dcim/mac-addresses/",
        {"assigned_object_type": "dcim.interface", "assigned_object_id": interface_id, "limit": 100},
    )
    current = next((item for item in existing if parse_mac_address(item.get("mac_address")) == normalized), None)
    created = False
    if current is None:
        created_result = await netbox_request(
            "POST",
            "/dcim/mac-addresses/",
            {
                "mac_address": normalized,
                "assigned_object_type": "dcim.interface",
                "assigned_object_id": interface_id,
            },
        )
        current = created_result.get("result") or created_result.get("response") or {}
        created = True
    if current.get("id") is not None:
        await netbox_request("PATCH", f"/dcim/interfaces/{interface_id}/", {"primary_mac_address": int(current["id"])})
    return {"created": created, "mac": current}


async def apply_netbox_interface_sync(device_id: int, options: NetBoxInterfaceSyncRunPayload) -> dict[str, Any]:
    return await apply_interface_sync_service(interface_sync_runtime(), device_id, options, parse_mac_address)
async def zabbix_host_items(host_id: str) -> list[dict[str, Any]]:
    result = await zabbix_request(
        "item.get",
        {
            "output": ["itemid", "hostid", "name", "key_", "snmp_oid", "lastvalue", "value_type", "status", "state", "error"],
            "hostids": [host_id],
            "filter": {"status": 0},
        },
    )
    return result.get("result") or []


async def zabbix_global_macros() -> list[dict[str, Any]]:
    result = await zabbix_request("usermacro.get", {"output": "extend", "globalmacro": True})
    return result.get("result") or []


async def find_zabbix_serial_item(host_id: str) -> Optional[dict[str, Any]]:
    candidates = await zabbix_host_items(host_id)
    for item in candidates:
        key_name = compact_text(item.get("key_")).lower()
        item_name = compact_text(item.get("name")).lower()
        snmp_oid = compact_text(item.get("snmp_oid"))
        if "entphysicalserialnum" in key_name or "entphysicalserialnum" in item_name or ".1.3.6.1.2.1.47.1.1.1.1.11.1" in snmp_oid:
            return item
    return None


async def ensure_zabbix_serial_item(host_id: str) -> dict[str, Any]:
    existing = await find_zabbix_serial_item(host_id)
    if existing:
        return {"status": "exists", "item": existing}

    host_result = await zabbix_request("host.get", {"output": ["hostid"], "hostids": [host_id], "selectInterfaces": "extend"})
    if not host_result.get("result"):
        raise HTTPException(404, "Zabbix host not found")
    host = host_result["result"][0]
    interface = next((item for item in host.get("interfaces", []) if str(item.get("type")) == "2"), None)
    if not interface:
        raise HTTPException(400, "No SNMP interface available on the correlated Zabbix host")

    created = await zabbix_request(
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


def serial_candidate_from_zabbix(host: Optional[dict[str, Any]], serial_item: Optional[dict[str, Any]]) -> Optional[str]:
    inventory = (host or {}).get("inventory") or {}
    for key in ("serialno_a", "serialno_b", "serialno"):
        candidate = compact_text(inventory.get(key))
        if candidate:
            return candidate
    latest = compact_text((serial_item or {}).get("lastvalue"))
    if latest:
        return latest
    return None


def observium_device_columns() -> set[str]:
    global _observium_device_columns_cache
    if _observium_device_columns_cache is None:
        try:
            rows = observium_db_query("SHOW COLUMNS FROM devices")
            _observium_device_columns_cache = {str(row["Field"]) for row in rows}
        except Exception:
            _observium_device_columns_cache = set()
    return _observium_device_columns_cache


def serial_candidate_from_observium(device: Optional[dict[str, Any]]) -> Optional[str]:
    return compact_text((device or {}).get("serial")) or None


def observium_device_row(device_id: int) -> Optional[dict[str, Any]]:
    try:
        fields = [
            "device_id", "hostname", "sysName", "label", "ip",
            "snmp_version", "snmp_community", "snmp_port", "snmp_transport",
            "snmp_authlevel", "snmp_authname", "snmp_authalgo", "snmp_context",
            "status", "disabled", "`ignore`", "location", "purpose", "os", "vendor", "hardware", "version",
        ]
        if "serial" in observium_device_columns():
            fields.append("serial")
        row = observium_db_query(
            f"""
            SELECT
                {", ".join(fields)}
            FROM devices
            WHERE device_id = %s
            """,
            (device_id,),
            fetch="one",
        )
        if not row:
            return None
        return normalize_observium_device(row)
    except Exception:
        client = observium_web_client()
        if not client.configured():
            raise
        return normalize_observium_device(client.device_popup(device_id))


async def correlated_sources_for_netbox_device(device_id: int) -> dict[str, Any]:
    correlation = find_correlation_by_item("netbox", str(device_id))
    if not correlation:
        raise HTTPException(404, "No saved correlation exists for this NetBox device")

    items = correlation.get("items") or {}
    zabbix_host = None
    observium_device = None

    if items.get("zabbix"):
        global_macros = await zabbix_global_macros()
        zabbix_result = await zabbix_request(
            "host.get",
            {
                "output": "extend",
                "hostids": [items["zabbix"]["id"]],
                "selectInterfaces": "extend",
                "selectInventory": "extend",
                "selectTags": "extend",
                "selectMacros": "extend",
            },
        )
        zabbix_host = (zabbix_result.get("result") or [None])[0]
        if zabbix_host is not None:
            zabbix_host["globalmacros"] = global_macros

    if items.get("observium"):
        observium_device = observium_device_row(int(items["observium"]["id"]))

    return {"correlation": correlation, "zabbix": zabbix_host, "observium": observium_device}


async def choose_primary_interface(device_id: int) -> dict[str, Any]:
    interfaces = await netbox_paginated("/dcim/interfaces/", {"device_id": device_id})
    if interfaces:
        def score(item: dict[str, Any]) -> tuple[int, int]:
            name = compact_text(item.get("name")).lower()
            if "vlan200" in name or "vlan 200" in name:
                return (0, 0)
            if any(token in name for token in ("mgmt", "management", "oob")):
                return (1, 0)
            if "vlan1" in name or "vlan 1" in name:
                return (2, 0)
            if name.startswith("vlan "):
                return (3, 0)
            return (4, int(not item.get("enabled", True)))
        return sorted(interfaces, key=score)[0]

    created = await netbox_request(
        "POST",
        "/dcim/interfaces/",
        {"device": int(device_id), "name": "mgmt0", "type": "virtual", "enabled": True},
    )
    return created.get("result") or created.get("response") or {}


async def choose_management_interface(device_id: int, preferred_name: Optional[str] = None) -> dict[str, Any]:
    interfaces = await netbox_paginated("/dcim/interfaces/", {"device_id": device_id, "limit": 500})
    preferred = compact_text(preferred_name)
    if preferred:
        exact = next((item for item in interfaces if normalize_key(item.get("name")) == normalize_key(preferred)), None)
        if exact:
            return exact

    if interfaces:
        def score(item: dict[str, Any]) -> tuple[int, int]:
            name = compact_text(item.get("name")).lower()
            if preferred and normalize_key(name) == normalize_key(preferred):
                return (0, 0)
            if "vlan200" in name or "vlan 200" in name:
                return (1, 0)
            if any(token in name for token in ("mgmt", "management", "oob")):
                return (2, 0)
            if "vlan1" in name or "vlan 1" in name:
                return (3, 0)
            if name.startswith("vlan "):
                return (4, 0)
            return (5, int(not item.get("enabled", True)))
        return sorted(interfaces, key=score)[0]

    created_name = preferred or "VLAN 200"
    created = await netbox_request(
        "POST",
        "/dcim/interfaces/",
        {"device": int(device_id), "name": created_name, "type": "virtual", "enabled": True},
    )
    return created.get("result") or created.get("response") or {}


async def ip_assigned_device(ip_payload: dict[str, Any]) -> Optional[int]:
    assigned_object = ip_payload.get("assigned_object") or {}
    interface_url = assigned_object.get("url")
    if interface_url:
        iface_result = await netbox_request("GET", interface_url.replace(f"{get_netbox_url()}/api", ""))
        iface = iface_result.get("result") or {}
        device = iface.get("device") or {}
        if device.get("id") is not None:
            return int(device["id"])
    return None


async def ensure_netbox_primary_ip4(
    device: dict[str, Any],
    address: str,
    status: str = "active",
    preferred_interface_name: Optional[str] = None,
) -> dict[str, Any]:
    normalized = normalize_ip_value(address)
    if not normalized:
        raise HTTPException(400, "IPv4 candidate is empty")

    chosen_interface = await choose_management_interface(int(device["id"]), preferred_interface_name) if preferred_interface_name else await choose_primary_interface(int(device["id"]))
    search_results = await netbox_paginated("/ipam/ip-addresses/", {"address": host_part(normalized), "limit": 50})

    reusable = None
    conflict = None
    for candidate in search_results:
        if host_part(candidate.get("address", "")) != host_part(normalized):
            continue
        assigned_device_id = await ip_assigned_device(candidate) if candidate.get("assigned_object") else None
        if assigned_device_id in {None, int(device["id"])}:
            reusable = candidate
            break
        conflict = candidate

    if conflict and reusable is None:
        raise HTTPException(409, f"IP {host_part(normalized)} is already assigned to another NetBox device")

    if reusable:
        update_payload = {
            "address": normalized,
            "status": status,
            "assigned_object_type": "dcim.interface",
            "assigned_object_id": int(chosen_interface["id"]),
        }
        await netbox_request("PATCH", f"/ipam/ip-addresses/{int(reusable['id'])}/", update_payload)
        ip_id = int(reusable["id"])
        created = False
    else:
        created_result = await netbox_request(
            "POST",
            "/ipam/ip-addresses/",
            {
                "address": normalized,
                "status": status,
                "assigned_object_type": "dcim.interface",
                "assigned_object_id": int(chosen_interface["id"]),
            },
        )
        created_ip = created_result.get("result") or created_result.get("response") or {}
        ip_id = int(created_ip["id"])
        created = True

    await netbox_set_primary_ip(int(device["id"]), {"ip_id": ip_id})
    return {"ip_id": ip_id, "address": normalized, "created": created, "interface": chosen_interface}


async def clear_netbox_interface_dependencies(device_id: int, interfaces: list[dict[str, Any]]) -> dict[str, Any]:
    interface_ids = {int(item["id"]) for item in interfaces if item.get("id") is not None}
    if not interface_ids:
        return {"interfaces_deleted": 0, "ips_unassigned": 0, "cables_deleted": 0, "macs_deleted": 0}

    device_result = await netbox_request("GET", f"/dcim/devices/{device_id}/")
    device = device_result.get("result") or device_result.get("response") or {}
    primary_clear: dict[str, Any] = {}
    for field in ("primary_ip4", "primary_ip6"):
        primary_ip = device.get(field) or {}
        if as_int(primary_ip.get("id")) is not None:
            primary_clear[field] = None
    if primary_clear:
        await netbox_request("PATCH", f"/dcim/devices/{device_id}/", primary_clear)

    ips = await netbox_paginated("/ipam/ip-addresses/", {"device_id": device_id, "limit": 500})
    ips_unassigned = 0
    for ip_item in ips:
        assigned_object = ip_item.get("assigned_object") or {}
        if as_int(assigned_object.get("id")) not in interface_ids:
            continue
        await netbox_request(
            "PATCH",
            f"/ipam/ip-addresses/{int(ip_item['id'])}/",
            {"assigned_object_type": None, "assigned_object_id": None},
        )
        ips_unassigned += 1

    cables_deleted = 0
    macs_deleted = 0
    interfaces_deleted = 0
    for interface in interfaces:
        iface_id = int(interface["id"])
        detail_result = await netbox_request("GET", f"/dcim/interfaces/{iface_id}/")
        detail = detail_result.get("result") or detail_result.get("response") or {}
        cable = detail.get("cable") or {}
        cable_id = as_int(cable.get("id"))
        if cable_id:
            await netbox_request("DELETE", f"/dcim/cables/{cable_id}/")
            cables_deleted += 1
        macs = await netbox_paginated(
            "/dcim/mac-addresses/",
            {"assigned_object_type": "dcim.interface", "assigned_object_id": iface_id, "limit": 100},
        )
        for mac in macs:
            if mac.get("id") is None:
                continue
            await netbox_request("DELETE", f"/dcim/mac-addresses/{int(mac['id'])}/")
            macs_deleted += 1
        await netbox_request("DELETE", f"/dcim/interfaces/{iface_id}/")
        interfaces_deleted += 1

    return {
        "interfaces_deleted": interfaces_deleted,
        "ips_unassigned": ips_unassigned,
        "cables_deleted": cables_deleted,
        "macs_deleted": macs_deleted,
    }


async def netbox_find_device_type_by_model(model: str) -> Optional[dict[str, Any]]:
    target = normalize_key(model)
    if not target:
        return None
    for item in await netbox_paginated("/dcim/device-types/", {"limit": 500}):
        if normalize_key(item.get("model")) == target or normalize_key(item.get("display")) == target:
            return item
    return None


async def ensure_netbox_device_type(model: str, manufacturer_name: str) -> dict[str, Any]:
    existing = await netbox_find_device_type_by_model(model)
    if existing:
        return {"created": False, "device_type": existing}
    manufacturer_result = await ensure_netbox_manufacturer(manufacturer_name or "Unknown Vendor")
    manufacturer = manufacturer_result.get("manufacturer") or {}
    if not manufacturer.get("id"):
        raise HTTPException(400, "Manufacturer could not be created in NetBox")
    created = await netbox_request(
        "POST",
        "/dcim/device-types/",
        {
            "manufacturer": int(manufacturer["id"]),
            "model": model,
            "slug": slugify_text(model)[:100],
        },
    )
    return {
        "created": True,
        "manufacturer_created": manufacturer_result.get("created", False),
        "device_type": created.get("result") or created.get("response") or {},
        "manufacturer": manufacturer,
    }


async def netbox_find_device_by_mac(mac_address: str) -> Optional[dict[str, Any]]:
    normalized = parse_mac_address(mac_address)
    if not normalized:
        return None
    mac_rows = await netbox_paginated("/dcim/mac-addresses/", {"mac_address": normalized, "limit": 50})
    for row in mac_rows:
        assigned = row.get("assigned_object") or {}
        device = assigned.get("device") or {}
        if device.get("id") is not None:
            device_result = await netbox_request("GET", f"/dcim/devices/{int(device['id'])}/")
            return device_result.get("result") or None
    return None


async def netbox_find_device_by_identity(name: str, ip_value: str = "", mac_address: str = "") -> Optional[dict[str, Any]]:
    target_name = normalize_key(name)
    target_ip = host_part(ip_value) if ip_value else ""
    if mac_address:
        by_mac = await netbox_find_device_by_mac(mac_address)
        if by_mac:
            return by_mac
    for item in await fetch_all_netbox_devices(archived="all"):
        if target_name and normalize_key(item.get("name")) == target_name:
            return item
        item_ip = host_part((item.get("primary_ip4") or {}).get("address") or "")
        if target_ip and item_ip == target_ip:
            return item
    return None


async def zabbix_find_host_by_identity(name: str, ip_value: str = "", mac_address: str = "") -> Optional[dict[str, Any]]:
    hosts = (await zabbix_hosts(limit=1000, archived="all")).get("result") or []
    target_name = normalize_key(name)
    target_ip = host_part(ip_value) if ip_value else ""
    target_mac = parse_mac_address(mac_address)
    for host in hosts:
        if target_name and normalize_key(host.get("host")) == target_name:
            return host
        host_ip = host_part(extract_main_zabbix_ip(host) or "")
        if target_ip and host_ip == target_ip:
            return host
        inventory = host.get("inventory") or {}
        for key in ("macaddress_a", "macaddress_b", "macaddress_c"):
            if target_mac and parse_mac_address(inventory.get(key)) == target_mac:
                return host
    return None


def observium_find_device_by_mac(mac_address: str) -> Optional[dict[str, Any]]:
    normalized = parse_mac_address(mac_address)
    if not normalized or not observium_table_exists("ports"):
        return None
    try:
        row = observium_db_query(
            """
            SELECT device_id
            FROM ports
            WHERE REPLACE(UPPER(COALESCE(ifPhysAddress, '')), '-', '') = REPLACE(%s, ':', '')
            LIMIT 1
            """,
            (normalized,),
            fetch="one",
        )
    except Exception:
        row = None
    if not row:
        return None
    return observium_device_row(int(row["device_id"]))


async def observium_find_device_by_identity(name: str, ip_value: str = "", mac_address: str = "") -> Optional[dict[str, Any]]:
    target_name = normalize_key(name)
    target_ip = host_part(ip_value) if ip_value else ""
    if mac_address:
        by_mac = observium_find_device_by_mac(mac_address)
        if by_mac:
            return by_mac
    rows = observium_db_query("SELECT device_id FROM devices ORDER BY device_id DESC")
    for row in rows or []:
        device = observium_device_row(int(row["device_id"]))
        if not device:
            continue
        if target_name and (normalize_key(device.get("hostname")) == target_name or normalize_key(device.get("sysName")) == target_name):
            return device
        if target_ip and host_part(device.get("ip") or "") == target_ip:
            return device
    return None


def observium_link_rows(local_device_id: int) -> list[dict[str, Any]]:
    try:
        if observium_table_exists("links"):
            columns = observium_table_columns("links")
            local_device_col = find_table_column(columns, "local_device_id", "device_id", "device_id_local")
            local_port_col = find_table_column(columns, "local_port_id", "port_id", "port_id_local")
            if not local_device_col or not local_port_col:
                raise RuntimeError("Observium links table missing required columns")
            return observium_db_query(
                f"SELECT * FROM links WHERE {local_device_col} = %s",
                (local_device_id,),
            )

        if observium_table_exists("neighbours"):
            columns = observium_table_columns("neighbours")
            local_device_col = find_table_column(columns, "device_id", "local_device_id", "device_id_local")
            local_port_col = find_table_column(columns, "port_id", "local_port_id", "port_id_local")
            if not local_device_col or not local_port_col:
                raise RuntimeError("Observium neighbours table missing required columns")
            active_col = find_table_column(columns, "active")
            where_clauses = [f"{local_device_col} = %s"]
            params: list[Any] = [local_device_id]
            if active_col:
                where_clauses.append(f"{active_col} = %s")
                params.append(1)
            rows = observium_db_query(
                f"SELECT * FROM neighbours WHERE {' AND '.join(where_clauses)}",
                tuple(params),
            )
            normalized_rows: list[dict[str, Any]] = []
            for row in rows or []:
                item = dict(row)
                item.setdefault("local_device_id", item.get(local_device_col))
                item.setdefault("local_port_id", item.get(local_port_col))
                item.setdefault("remote_device_id", item.get("remote_device_id"))
                item.setdefault("remote_port_id", item.get("remote_port_id"))
                item.setdefault("protocol", item.get("protocol") or "lldp")
                normalized_rows.append(item)
            return normalized_rows
    except Exception:
        client = observium_web_client()
        if client.configured():
            return client.device_links(local_device_id)
    return []


def observium_mac_ip_rows(mac_address: str) -> list[dict[str, Any]]:
    normalized = parse_mac_address(mac_address)
    if not normalized:
        return []
    queries: list[tuple[str, tuple[Any, ...]]] = []
    if observium_table_exists("ipv4_mac"):
        queries.append(
            (
                """
                SELECT *
                FROM ipv4_mac
                WHERE REPLACE(UPPER(COALESCE(mac_address, '')), '-', '') = REPLACE(%s, ':', '')
                """,
                (normalized,),
            )
        )
    if observium_table_exists("arp_table"):
        queries.append(
            (
                """
                SELECT *
                FROM arp_table
                WHERE REPLACE(UPPER(COALESCE(mac_address, '')), '-', '') = REPLACE(%s, ':', '')
                """,
                (normalized,),
            )
        )
    rows: list[dict[str, Any]] = []
    for query, params in queries:
        try:
            rows.extend(observium_db_query(query, params) or [])
        except Exception:
            continue
    return rows


def observium_ip_from_mac(mac_address: str) -> str:
    for row in observium_mac_ip_rows(mac_address):
        for key in ("ipv4_address", "ip_address", "address", "ip"):
            candidate = compact_text(row.get(key))
            if candidate:
                return host_part(candidate)
    return ""


def observium_link_neighbor_mac(row: dict[str, Any]) -> str:
    for key in ("remote_ifPhysAddress", "remote_mac", "peer_mac", "remote_port_mac", "lldpRemChassisId"):
        value = parse_mac_address(row.get(key))
        if value:
            return value
    return ""


def observium_link_neighbor_name(row: dict[str, Any]) -> str:
    for key in ("remote_hostname", "remote_sysName", "remote_name", "lldpRemSysName", "remote_device"):
        value = compact_text(row.get(key))
        if value:
            return value
    return ""


async def resolve_lldp_source(body: DiscoveryLldpPreviewPayload | DiscoveryLldpApplyPayload) -> dict[str, Any]:
    source = compact_text(body.source).lower()
    source_id = str(body.source_id).strip()
    if source not in {"observium", "zabbix"}:
        raise HTTPException(400, "LLDP source must be observium or zabbix")
    if not source_id:
        raise HTTPException(400, "source_id is required")

    zabbix_host = None
    observium_device = None

    if source == "observium":
        observium_device = observium_device_row(int(source_id))
        if not observium_device:
            raise HTTPException(404, "Observium source device was not found")
        zabbix_host = await zabbix_find_host_by_identity(
            observium_device.get("sysName") or observium_device.get("hostname") or "",
            observium_device.get("ip") or "",
        )
    else:
        zabbix_result = await zabbix_request(
            "host.get",
            {
                "output": "extend",
                "hostids": [source_id],
                "selectInterfaces": "extend",
                "selectInventory": "extend",
                "selectTags": "extend",
                "selectMacros": "extend",
            },
        )
        zabbix_host = (zabbix_result.get("result") or [None])[0]
        if not zabbix_host:
            raise HTTPException(404, "Zabbix source host was not found")
        observium_device = await observium_find_device_by_identity(
            zabbix_host.get("host") or zabbix_host.get("name") or "",
            extract_main_zabbix_ip(zabbix_host) or "",
        )
        correlation = find_correlation_by_item("zabbix", source_id)
        if not observium_device and correlation and (correlation.get("items") or {}).get("observium"):
            observium_device = observium_device_row(int(correlation["items"]["observium"]["id"]))

    if not observium_device:
        raise HTTPException(404, "No Observium device could be resolved for the selected source")

    return {"source": source, "source_id": source_id, "zabbix": zabbix_host, "observium": observium_device}


async def build_lldp_neighbor_candidate(
    *,
    local_source: dict[str, Any],
    local_port: Optional[dict[str, Any]],
    link_row: dict[str, Any],
    remote_device: Optional[dict[str, Any]],
    remote_port: Optional[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    remote_mac = observium_link_neighbor_mac(link_row) or parse_mac_address((remote_port or {}).get("mac_address"))
    remote_ip = ""
    if remote_device:
        remote_ip = host_part(remote_device.get("ip") or "")
    if not remote_ip and remote_mac:
        remote_ip = observium_ip_from_mac(remote_mac)
    remote_name = (
        compact_text((remote_device or {}).get("sysName"))
        or compact_text((remote_device or {}).get("hostname"))
        or compact_text((remote_port or {}).get("description"))
        or observium_link_neighbor_name(link_row)
    )
    if not remote_name and not remote_mac and not remote_ip:
        return None

    existing_netbox = await netbox_find_device_by_identity(remote_name, remote_ip, remote_mac)
    existing_zabbix = await zabbix_find_host_by_identity(remote_name, remote_ip, remote_mac)
    existing_observium = remote_device or await observium_find_device_by_identity(remote_name, remote_ip, remote_mac)

    manufacturer_name = compact_text((existing_observium or {}).get("vendor")) or "Unknown Vendor"
    model_name = compact_text((existing_observium or {}).get("hardware")) or remote_name
    create_candidate = {
        "name": remote_name or (remote_ip or remote_mac or "lldp-neighbor"),
        "manufacturer_name": manufacturer_name,
        "model_name": model_name,
        "platform_name": build_observium_platform_label(existing_observium),
        "serial": compact_text((existing_observium or {}).get("serial")),
        "primary_ip4": normalize_ip_value(remote_ip) if remote_ip else "",
        "description": build_observium_platform_label(existing_observium),
    }
    return {
        "id": f"{local_source['observium']['device_id']}:{(local_port or {}).get('port_id') or 0}:{remote_name or remote_mac or remote_ip}",
        "protocol": compact_text(first_non_empty(link_row, "protocol", "link_type")) or "LLDP/CDP",
        "local_port": local_port,
        "remote_port": remote_port,
        "neighbor": {
            "name": remote_name,
            "ip": remote_ip,
            "mac_address": remote_mac,
        },
        "matches": {
            "netbox": existing_netbox,
            "zabbix": existing_zabbix,
            "observium": existing_observium,
        },
        "status": "matched" if (existing_netbox or existing_zabbix or existing_observium) else "proposed_create",
        "create_candidate": create_candidate,
    }


async def build_lldp_discovery_preview(body: DiscoveryLldpPreviewPayload | DiscoveryLldpApplyPayload) -> dict[str, Any]:
    source_ctx = await resolve_lldp_source(body)
    observium_device = source_ctx["observium"]
    ports = observium_port_rows(int(observium_device["device_id"]))
    ports_by_id = {int(port["port_id"]): port for port in ports if port.get("port_id")}
    rows = observium_link_rows(int(observium_device["device_id"]))
    proposals: list[dict[str, Any]] = []
    for row in rows:
        local_port_id = as_int(first_non_empty(row, "local_port_id", "port_id", "port_id_local")) or 0
        local_port = ports_by_id.get(local_port_id)
        remote_device_id = as_int(first_non_empty(row, "remote_device_id", "peer_device_id", "device_id_remote"))
        remote_port_id = as_int(first_non_empty(row, "remote_port_id", "peer_port_id", "port_id_remote"))
        remote_device = observium_device_row(remote_device_id) if remote_device_id else None
        remote_ports = observium_port_rows(remote_device_id) if remote_device_id else []
        remote_port = next((item for item in remote_ports if int(item.get("port_id") or 0) == remote_port_id), None)
        candidate = await build_lldp_neighbor_candidate(
            local_source=source_ctx,
            local_port=local_port,
            link_row=row,
            remote_device=remote_device,
            remote_port=remote_port,
        )
        if candidate:
            proposals.append(candidate)
    return {
        "source": {
            "platform": source_ctx["source"],
            "source_id": source_ctx["source_id"],
            "zabbix": source_ctx["zabbix"],
            "observium": source_ctx["observium"],
        },
        "summary": {
            "links": len(proposals),
            "matched": sum(1 for item in proposals if item["status"] == "matched"),
            "proposed_create": sum(1 for item in proposals if item["status"] == "proposed_create"),
        },
        "proposals": proposals,
    }


async def apply_lldp_discovery(body: DiscoveryLldpApplyPayload) -> dict[str, Any]:
    preview = await build_lldp_discovery_preview(body)
    selected = set(body.proposal_ids or [])
    results: list[dict[str, Any]] = []
    for item in preview["proposals"]:
        if selected and item["id"] not in selected:
            continue
        if item["status"] != "proposed_create":
            results.append({"status": "skipped", "id": item["id"], "reason": item["status"]})
            continue
        if not body.site_id or not body.role_id:
            results.append({"status": "error", "id": item["id"], "error": "Site and role are required to create LLDP proposals in NetBox"})
            continue
        candidate = item.get("create_candidate") or {}
        ensured_device_type = await ensure_netbox_device_type(candidate.get("model_name") or candidate.get("name") or "Unknown Model", candidate.get("manufacturer_name") or "Unknown Vendor")
        device_type = ensured_device_type.get("device_type") or {}
        payload = {
            "name": candidate.get("name"),
            "status": "active",
            "site": int(body.site_id),
            "role": int(body.role_id),
            "device_type": int(device_type["id"]),
            "serial": candidate.get("serial") or "",
            "description": candidate.get("description") or "",
        }
        try:
            created = await netbox_request("POST", "/dcim/devices/", payload)
            created_device = created.get("result") or created.get("response") or {}
            if candidate.get("platform_name"):
                ensured_platform = await ensure_netbox_platform(candidate["platform_name"])
                platform = ensured_platform.get("platform") or {}
                if platform.get("id"):
                    await netbox_request("PATCH", f"/dcim/devices/{int(created_device['id'])}/", {"platform": int(platform["id"])})
            if candidate.get("primary_ip4"):
                await ensure_netbox_primary_ip4(created_device, candidate["primary_ip4"])
            results.append({"status": "created", "id": item["id"], "device": created_device, "payload": payload, "device_type": device_type})
        except HTTPException as exc:
            results.append({"status": "error", "id": item["id"], "error": exc.detail, "payload": payload})
    return {"preview": await build_lldp_discovery_preview(body), "results": results}


async def build_netbox_enrichment(device_id: int) -> dict[str, Any]:
    device_result = await netbox_request("GET", f"/dcim/devices/{device_id}/")
    device = device_result.get("result") or {}
    sources = await correlated_sources_for_netbox_device(device_id)
    zabbix_host = sources["zabbix"]
    observium_device = sources["observium"]
    serial_item = await find_zabbix_serial_item(str(zabbix_host["hostid"])) if zabbix_host else None

    platform_name = pick_platform_name(device, zabbix_host, observium_device)
    location_name = compact_text((observium_device or {}).get("location") or ((zabbix_host or {}).get("inventory") or {}).get("location"))
    zabbix_ip = extract_main_zabbix_ip(zabbix_host or {})
    observium_ip = extract_observium_ipv4(observium_device or {})
    description_ip = description_ip_candidate(device)
    serial_candidate = serial_candidate_from_zabbix(zabbix_host, serial_item)

    suggestions: list[dict[str, Any]] = []

    if is_blank(device.get("platform")) and platform_name:
        existing_platform = await netbox_find_platform(platform_name)
        suggestions.append(
            {
                "id": "platform_from_monitoring",
                "action_type": "set_platform",
                "field": "platform",
                "label": "Map platform from Observium/Zabbix",
                "current_value": (device.get("platform") or {}).get("display") or "",
                "proposed_value": platform_name,
                "source": "observium",
                "confidence": "medium",
                "requires_create": not bool(existing_platform),
                "target_section": "DCIM > Platforms",
            }
        )

    if is_blank(device.get("location")) and location_name:
        existing_location = await netbox_find_location(location_name, (device.get("site") or {}).get("id"))
        suggestions.append(
            {
                "id": "location_from_monitoring",
                "action_type": "set_location",
                "field": "location",
                "label": "Map location from SNMP location",
                "current_value": (device.get("location") or {}).get("display") or "",
                "proposed_value": location_name,
                "source": "observium",
                "confidence": "medium",
                "requires_create": not bool(existing_location),
                "target_section": "DCIM > Locations",
            }
        )

    if is_blank(device.get("primary_ip4")) and zabbix_ip:
        suggestions.append(
            {
                "id": "primary_ip4_from_zabbix",
                "action_type": "set_primary_ip4",
                "field": "primary_ip4",
                "label": "Set primary IPv4 from Zabbix",
                "current_value": (device.get("primary_ip4") or {}).get("address") or "",
                "proposed_value": zabbix_ip,
                "source": "zabbix",
                "confidence": "high",
                "requires_create": True,
                "target_section": "IPAM > IP Addresses",
            }
        )
    elif is_blank(device.get("primary_ip4")) and observium_ip:
        suggestions.append(
            {
                "id": "primary_ip4_from_observium",
                "action_type": "set_primary_ip4",
                "field": "primary_ip4",
                "label": "Set primary IPv4 from Observium",
                "current_value": (device.get("primary_ip4") or {}).get("address") or "",
                "proposed_value": observium_ip,
                "source": "observium",
                "confidence": "medium",
                "requires_create": True,
                "target_section": "IPAM > IP Addresses",
            }
        )

    if description_ip and is_blank(device.get("primary_ip4")):
        suggestions.append(
            {
                "id": "migrate_description_ip",
                "action_type": "migrate_description_ip",
                "field": "primary_ip4",
                "label": "Move IP stored in description to primary IPv4",
                "current_value": compact_text(device.get("description")),
                "proposed_value": description_ip,
                "source": "netbox",
                "confidence": "high",
                "requires_create": True,
                "target_section": "IPAM > IP Addresses",
            }
        )

    enriched_description = build_enriched_description(device, zabbix_host, observium_device)
    if (is_blank(device.get("description")) or description_ip) and compact_text(enriched_description):
        suggestions.append(
            {
                "id": "description_from_monitoring",
                "action_type": "set_description",
                "field": "description",
                "label": "Enrich description from monitoring data",
                "current_value": compact_text(device.get("description")),
                "proposed_value": enriched_description,
                "source": "observium",
                "confidence": "medium",
                "requires_create": False,
                "target_section": "Device field",
            }
        )

    enriched_comments = build_enriched_comments(device, zabbix_host, observium_device)
    if is_blank(device.get("comments")) and compact_text(enriched_comments):
        suggestions.append(
            {
                "id": "comments_from_monitoring",
                "action_type": "set_comments",
                "field": "comments",
                "label": "Generate comments from SNMP metadata",
                "current_value": compact_text(device.get("comments")),
                "proposed_value": enriched_comments,
                "source": "observium",
                "confidence": "medium",
                "requires_create": False,
                "target_section": "Device field",
            }
        )

    if is_blank(device.get("serial")) and serial_candidate:
        suggestions.append(
            {
                "id": "serial_from_zabbix",
                "action_type": "set_serial",
                "field": "serial",
                "label": "Sync serial from Zabbix",
                "current_value": compact_text(device.get("serial")),
                "proposed_value": serial_candidate,
                "source": "zabbix",
                "confidence": "medium",
                "requires_create": False,
                "target_section": "Device field",
            }
        )

    if is_blank(device.get("serial")) and zabbix_host and not serial_candidate:
        suggestions.append(
            {
                "id": "ensure_zabbix_serial_item",
                "action_type": "ensure_zabbix_serial_item",
                "field": "serial",
                "label": "Create Zabbix SNMP item for entPhysicalSerialNum",
                "current_value": "",
                "proposed_value": ".1.3.6.1.2.1.47.1.1.1.1.11.1",
                "source": "zabbix",
                "confidence": "medium",
                "requires_create": False,
                "target_section": "Zabbix SNMP item",
                "note": "Value will populate after the next Zabbix poll if the device exposes entPhysicalSerialNum.1",
            }
        )

    return {
        "device": device,
        "correlation": sources["correlation"],
        "sources": {
            "zabbix": {
                "hostid": str(zabbix_host.get("hostid")) if zabbix_host else None,
                "host": (zabbix_host or {}).get("host"),
                "name": (zabbix_host or {}).get("name"),
                "main_ip": zabbix_ip,
            } if zabbix_host else None,
            "observium": {
                "device_id": str(observium_device.get("device_id")) if observium_device else None,
                "hostname": (observium_device or {}).get("hostname"),
                "sysName": (observium_device or {}).get("sysName"),
                "ip": observium_ip,
                "location": (observium_device or {}).get("location"),
                "os": (observium_device or {}).get("os"),
                "vendor": (observium_device or {}).get("vendor"),
                "hardware": (observium_device or {}).get("hardware"),
                "version": (observium_device or {}).get("version"),
            } if observium_device else None,
        },
        "manual_fields": {
            "asset_tag": compact_text(device.get("asset_tag")),
            "serial": compact_text(device.get("serial")),
        },
        "suggestions": suggestions,
    }


async def build_netbox_sync_preview(device_id: int) -> dict[str, Any]:
    device_result = await netbox_request("GET", f"/dcim/devices/{device_id}/")
    device = device_result.get("result") or {}
    sources = await correlated_sources_for_netbox_device(device_id)
    zabbix_host = sources["zabbix"]
    observium_device = sources["observium"]
    serial_item = await find_zabbix_serial_item(str(zabbix_host["hostid"])) if zabbix_host else None
    profile = get_netbox_sync_profile(device_id)

    candidates = {
        "primary_ip4": {
            "zabbix": extract_main_zabbix_ip(zabbix_host or {}),
            "observium": extract_observium_ipv4(observium_device or {}),
        },
        "serial": {
            "zabbix": serial_candidate_from_zabbix(zabbix_host, serial_item),
            "observium": serial_candidate_from_observium(observium_device),
        },
        "platform": {
            "zabbix": pick_platform_name(device, zabbix_host, None),
            "observium": pick_platform_name(device, None, observium_device),
        },
    }

    current_values = {
        "primary_ip4": compact_text((device.get("primary_ip4") or {}).get("address")),
        "serial": compact_text(device.get("serial")),
        "platform": compact_text((device.get("platform") or {}).get("display") or (device.get("platform") or {}).get("name")),
    }

    fields: dict[str, Any] = {}
    for field in SYNC_FIELDS:
        selected_source = profile["field_sources"].get(field) or SYNC_SOURCE_DEFAULTS[field]
        fields[field] = {
            "selected_source": selected_source,
            "current_value": current_values.get(field) or "",
            "candidates": {
                source: compact_text(value)
                for source, value in candidates[field].items()
            },
        }

    return {
        "device": device,
        "correlation": sources["correlation"],
        "profile": profile,
        "sources": {
            "zabbix": {
                "hostid": str(zabbix_host.get("hostid")) if zabbix_host else None,
                "host": (zabbix_host or {}).get("host"),
                "name": (zabbix_host or {}).get("name"),
            } if zabbix_host else None,
            "observium": {
                "device_id": str(observium_device.get("device_id")) if observium_device else None,
                "hostname": (observium_device or {}).get("hostname"),
                "sysName": (observium_device or {}).get("sysName"),
            } if observium_device else None,
        },
        "fields": fields,
    }


async def apply_netbox_sync(device_id: int, enabled: bool, field_sources: dict[str, str]) -> dict[str, Any]:
    profile = save_netbox_sync_profile(device_id, enabled, field_sources)
    preview = await build_netbox_sync_preview(device_id)
    device = preview["device"]
    results: list[dict[str, Any]] = []

    for field in SYNC_FIELDS:
        source = profile["field_sources"].get(field) or SYNC_SOURCE_DEFAULTS[field]
        if source not in {"zabbix", "observium"}:
            results.append({"field": field, "status": "skipped", "reason": "Invalid source"})
            continue
        value = compact_text(preview["fields"][field]["candidates"].get(source))
        if not value:
            results.append({"field": field, "status": "skipped", "reason": f"No value available from {source}"})
            continue
        try:
            if field == "primary_ip4":
                applied = await ensure_netbox_primary_ip4(device, value)
                results.append({"field": field, "status": "updated", "source": source, **applied})
            elif field == "serial":
                await netbox_request("PATCH", f"/dcim/devices/{device_id}/", {"serial": value})
                results.append({"field": field, "status": "updated", "source": source, "value": value})
            elif field == "platform":
                ensured = await ensure_netbox_platform(value)
                await netbox_request("PATCH", f"/dcim/devices/{device_id}/", {"platform": int(ensured["platform"]["id"])})
                results.append({"field": field, "status": "updated", "source": source, "value": value, "created": ensured["created"]})
        except HTTPException as exc:
            results.append({"field": field, "status": "error", "source": source, "error": exc.detail})

    return {"profile": profile, "results": results, "preview": await build_netbox_sync_preview(device_id)}


def first_non_empty(data: dict[str, Any], *keys: str) -> Optional[str]:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def zabbix_macro_value(host: dict[str, Any], name: Optional[str]) -> Optional[str]:
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


def resolve_zabbix_macro_text(host: dict[str, Any], value: Optional[str]) -> Optional[str]:
    text = compact_text(value)
    if not text:
        return None
    if re.fullmatch(r"\{\$[^}]+\}", text):
        return zabbix_macro_value(host, text) or text
    return text


def map_security_level(raw: Optional[str]) -> str:
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


def map_auth_protocol(raw: Optional[str]) -> Optional[str]:
    value = (raw or "").strip().lower()
    mapping = {"0": "MD5", "1": "SHA", "md5": "MD5", "sha": "SHA"}
    return mapping.get(value)


def map_priv_protocol(raw: Optional[str]) -> Optional[str]:
    value = (raw or "").strip().lower()
    mapping = {"0": "DES", "1": "AES", "des": "DES", "aes": "AES"}
    return mapping.get(value)


def normalize_zabbix_snmp(interface: dict[str, Any], host: dict[str, Any]) -> Optional[dict[str, Any]]:
    details = interface.get("details") or {}
    version = str(details.get("version", "")).strip()
    community = resolve_zabbix_macro_text(host, details.get("community"))
    if not community:
        community = zabbix_macro_value(host, "{$SNMP_COMMUNITY}")

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


def normalize_observium_device(row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    row["disabled"] = bool(row.get("disabled"))
    row["ignore"] = bool(row.get("ignore"))
    row["skip_icmp"] = bool(row.get("skip_icmp") or row.get("disable_icmp") or row.get("icmp_disable"))
    row["status"] = int(row.get("status") or 0)
    if get_observium_base_url():
        row["web_url"] = f"{get_observium_base_url()}/device/device={row['device_id']}/"
    return row


def observium_optional_icmp_column() -> Optional[str]:
    try:
        rows = observium_db_query(
            """
            SELECT COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'devices'
            """,
            (cfg.OBSERVIUM_DB_NAME,),
        )
    except Exception:
        return None
    available = {row.get("COLUMN_NAME") for row in rows or []}
    for candidate in ("skip_icmp", "disable_icmp", "icmp_disable"):
        if candidate in available:
            return candidate
    return None


def build_observium_add_command(device: ObserviumDeviceCreate) -> list[str]:
    base = ["php", "/opt/observium/add_device.php", device.hostname]
    if device.snmp_version == "v3":
        authlevel_map = {
            "noAuthNoPriv": "any",
            "authNoPriv": "anp",
            "authPriv": "ap",
        }
        authlevel = authlevel_map.get(device.snmp_authlevel or "", device.snmp_authlevel or "any")
        base.extend([authlevel, "v3", device.snmp_authname or ""])
        if authlevel in {"anp", "ap"}:
            base.append(device.snmp_authpass or "")
            base.append(device.snmp_authalgo or "SHA")
        if authlevel == "ap":
            base.append(device.snmp_cryptopass or "")
            base.append(device.snmp_cryptoalgo or "AES")
        base.extend([str(device.snmp_port), device.snmp_transport])
        if device.snmp_context:
            base.append(device.snmp_context)
        return base
    return base + [device.snmp_community or "", device.snmp_version, str(device.snmp_port), device.snmp_transport]


def observium_discovery_modules() -> list[str]:
    return [module.strip() for module in cfg.OBSERVIUM_DISCOVERY_MODULES.split(",") if module.strip()]


def build_observium_discovery_command(device_id: int) -> list[str]:
    command = ["php", "/opt/observium/discovery.php", "-h", str(device_id)]
    modules = observium_discovery_modules()
    if modules:
        command.extend(["-m", ",".join(modules)])
    return command


def run_observium_discovery(device_id: int) -> dict[str, Any]:
    result = observium_exec(build_observium_discovery_command(device_id))
    result["mode"] = "metadata-only"
    result["modules"] = observium_discovery_modules()
    return result


def run_observium_poller(device_id: int) -> dict[str, Any]:
    if not cfg.OBSERVIUM_ENABLE_POLLER:
        return {
            "status": "skipped",
            "reason": "poller_disabled_by_config",
            "device_id": device_id,
        }
    return observium_exec(["php", "/opt/observium/poller.php", "-h", str(device_id)])


def observium_refresh_device(device_id: int) -> dict[str, Any]:
    current = observium_db_query("SELECT hostname FROM devices WHERE device_id = %s", (device_id,), fetch="one")
    if not current:
        raise HTTPException(404, "Observium device not found")

    discovery = run_observium_discovery(device_id)
    poller = run_observium_poller(device_id)
    return {"device_id": device_id, "hostname": current["hostname"], "discovery": discovery, "poller": poller}


def observium_snmp_check(address: str, port: int, snmp: dict[str, Any]) -> dict[str, Any]:
    args = [
        "/opt/observium/scripts/local-sync/snmp_check.sh",
        address,
        snmp["version"],
        str(port),
        snmp.get("community", ""),
        snmp.get("security_name", ""),
        snmp.get("auth_protocol", ""),
        snmp.get("auth_password", ""),
        snmp.get("priv_protocol", ""),
        snmp.get("priv_password", ""),
        snmp.get("security_level", ""),
    ]
    return observium_exec(args)


async def zabbix_authenticate() -> str:
    global _zabbix_auth_token
    token = runtime_creds.get("zabbix_token", cfg.ZABBIX_TOKEN)
    if token:
        _zabbix_auth_token = token
        return token
    client = ZabbixApiClient(
        base_url=get_zabbix_url(),
        tls_verify=cfg.ZABBIX_TLS_VERIFY,
        token=token,
        username=runtime_creds.get("zabbix_user", cfg.ZABBIX_USER),
        password=runtime_creds.get("zabbix_pass", cfg.ZABBIX_PASS),
    )
    _zabbix_auth_token = await client.authenticate()
    return _zabbix_auth_token


async def zabbix_request(method: str, params: dict[str, Any]) -> Any:
    auth = await zabbix_authenticate()
    token = runtime_creds.get("zabbix_token", cfg.ZABBIX_TOKEN)
    client = ZabbixApiClient(
        base_url=get_zabbix_url(),
        tls_verify=cfg.ZABBIX_TLS_VERIFY,
        token=token,
        username=runtime_creds.get("zabbix_user", cfg.ZABBIX_USER),
        password=runtime_creds.get("zabbix_pass", cfg.ZABBIX_PASS),
    )
    return await client.request(method, params, auth=auth)


async def netbox_request(method: str, path: str, body: Optional[dict[str, Any]] = None) -> Any:
    client = NetBoxApiClient(
        base_url=get_netbox_url(),
        token=get_netbox_token(),
        tls_verify=cfg.NETBOX_TLS_VERIFY,
    )
    return await client.request(method, path, body)


async def fetch_all_netbox_devices(archived: str = "exclude") -> list[dict[str, Any]]:
    client = NetBoxApiClient(
        base_url=get_netbox_url(),
        token=get_netbox_token(),
        tls_verify=cfg.NETBOX_TLS_VERIFY,
    )
    return await fetch_all_netbox_devices_service(client, archived=archived)


async def zabbix_hosts(limit: int = 500, archived: str = "exclude"):
    global_macros = await zabbix_global_macros()
    result = await zabbix_request(
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
        record["globalmacros"] = global_macros
        record["ui_url"] = zabbix_host_search_ui_url(record)
    return {**result, "result": apply_archive_mode("zabbix", records, "hostid", archived)}


async def zabbix_host(host_id: str):
    global_macros = await zabbix_global_macros()
    result = await zabbix_request(
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
        result["result"][0]["globalmacros"] = global_macros
        result["result"][0]["archived"] = archive_status("zabbix", host_id)
        result["result"][0]["ui_url"] = zabbix_host_search_ui_url(result["result"][0])
    return result


async def zabbix_create_host(body: dict[str, Any]):
    return await zabbix_request("host.create", body)


async def zabbix_update_host(host_id: str, body: dict[str, Any]):
    body["hostid"] = host_id
    return await zabbix_request("host.update", body)


async def zabbix_interfaces(host_id: str):
    return await zabbix_request("hostinterface.get", {"output": "extend", "hostids": [host_id]})


async def zabbix_create_interface(body: dict[str, Any]):
    return await zabbix_request("hostinterface.create", body)


async def zabbix_update_interface(iface_id: str, body: dict[str, Any]):
    body["interfaceid"] = iface_id
    return await zabbix_request("hostinterface.update", body)


async def zabbix_delete_interface(iface_id: str):
    return await zabbix_request("hostinterface.delete", [iface_id])


async def zabbix_groups():
    return await zabbix_request("hostgroup.get", {"output": "extend"})


async def netbox_devices(limit: int = 500, offset: int = 0, archived: str = "exclude"):
    result = await netbox_request("GET", "/dcim/devices/", {"limit": limit, "offset": offset})
    items = result["result"].get("results", [])
    result["result"]["results"] = apply_archive_mode("netbox", items, "id", archived)
    return result


async def netbox_device(device_id: int):
    result = await netbox_request("GET", f"/dcim/devices/{device_id}/")
    if result.get("result"):
        result["result"]["archived"] = archive_status("netbox", str(device_id))
    return result


async def netbox_create_device(body: dict[str, Any]):
    return await netbox_request("POST", "/dcim/devices/", body)


async def netbox_update_device(device_id: int, body: dict[str, Any]):
    return await netbox_request("PATCH", f"/dcim/devices/{device_id}/", body)


async def netbox_set_primary_ip(device_id: int, body: dict[str, Any]):
    ip_id = body.get("ip_id")
    if ip_id in {None, ""}:
        raise HTTPException(400, "ip_id is required")

    ip_result = await netbox_request("GET", f"/ipam/ip-addresses/{int(ip_id)}/")
    ip_payload = ip_result.get("result") or {}
    address = str(ip_payload.get("address") or "").strip()
    if not address:
        raise HTTPException(400, "NetBox IP payload is missing address")

    try:
        ip_version = ipaddress.ip_interface(address).version
    except ValueError as exc:
        raise HTTPException(400, f"Invalid NetBox IP address: {address}") from exc

    primary_field = "primary_ip4" if ip_version == 4 else "primary_ip6"
    return await netbox_request("PATCH", f"/dcim/devices/{device_id}/", {primary_field: int(ip_id)})


async def netbox_device_interfaces(device_id: int):
    return await netbox_request("GET", "/dcim/interfaces/", {"device_id": device_id, "limit": 200})


async def netbox_interface(iface_id: int):
    return await netbox_request("GET", f"/dcim/interfaces/{iface_id}/")


async def netbox_create_interface(body: dict[str, Any]):
    return await netbox_request("POST", "/dcim/interfaces/", body)


async def netbox_update_interface(iface_id: int, body: dict[str, Any]):
    payload = dict(body)
    mac_address = parse_mac_address(payload.pop("mac_address", ""))
    result = None
    if payload:
        result = await netbox_request("PATCH", f"/dcim/interfaces/{iface_id}/", payload)
    if mac_address:
        mac_result = await ensure_netbox_interface_mac(iface_id, mac_address)
        detail = await netbox_request("GET", f"/dcim/interfaces/{iface_id}/")
        return {
            "status": "ok",
            "request": result.get("request") if result else None,
            "response": detail.get("response") if detail else None,
            "result": detail.get("result") or detail.get("response") or {},
            "mac_result": mac_result,
        }
    if result is not None:
        return result
    detail = await netbox_request("GET", f"/dcim/interfaces/{iface_id}/")
    return {"status": "ok", "result": detail.get("result") or detail.get("response") or {}}


async def netbox_delete_interface(iface_id: int):
    return await netbox_request("DELETE", f"/dcim/interfaces/{iface_id}/")


async def netbox_ips(device_id: Optional[int] = None, address: Optional[str] = None, limit: int = 200):
    params: dict[str, Any] = {"limit": limit}
    if device_id:
        params["device_id"] = device_id
    if address:
        params["address"] = address
    return await netbox_request("GET", "/ipam/ip-addresses/", params)


async def netbox_create_ip(body: dict[str, Any]):
    return await netbox_request("POST", "/ipam/ip-addresses/", body)


async def netbox_update_ip(ip_id: int, body: dict[str, Any]):
    return await netbox_request("PATCH", f"/ipam/ip-addresses/{ip_id}/", body)


async def netbox_delete_ip(ip_id: int):
    return await netbox_request("DELETE", f"/ipam/ip-addresses/{ip_id}/")


async def netbox_device_types():
    return await netbox_request("GET", "/dcim/device-types/", {"limit": 200})


async def netbox_platforms():
    return await netbox_request("GET", "/dcim/platforms/", {"limit": 500})


async def netbox_create_platform(body: dict[str, Any]):
    return await netbox_request("POST", "/dcim/platforms/", body)


async def netbox_sites():
    return await netbox_request("GET", "/dcim/sites/", {"limit": 200})


async def netbox_locations(site_id: Optional[int] = None):
    params: dict[str, Any] = {"limit": 500}
    if site_id:
        params["site_id"] = site_id
    return await netbox_request("GET", "/dcim/locations/", params)


async def netbox_create_location(body: dict[str, Any]):
    return await netbox_request("POST", "/dcim/locations/", body)


async def netbox_device_roles():
    return await netbox_request("GET", "/dcim/device-roles/", {"limit": 200})


async def netbox_device_sync(device_id: int):
    return {"status": "ok", "result": await build_netbox_sync_preview(device_id)}


async def netbox_update_sync_profile(device_id: int, body: NetBoxSyncProfilePayload):
    profile = save_netbox_sync_profile(device_id, body.enabled, body.field_sources)
    return {"status": "ok", "result": {"profile": profile, "preview": await build_netbox_sync_preview(device_id)}}


async def netbox_run_sync(device_id: int, body: NetBoxSyncProfilePayload):
    return {"status": "ok", "result": await apply_netbox_sync(device_id, body.enabled, body.field_sources)}


async def netbox_device_interface_sync(device_id: int):
    return {"status": "ok", "result": await build_netbox_interface_sync_preview(device_id)}


async def netbox_run_interface_sync(device_id: int, body: NetBoxInterfaceSyncRunPayload):
    lock = interface_sync_lock(device_id)
    if lock.locked():
        raise HTTPException(409, "A NetBox interface sync is already running for this device")
    async with lock:
        return {"status": "ok", "result": await apply_netbox_interface_sync(device_id, body)}


async def discovery_lldp_preview(body: DiscoveryLldpPreviewPayload):
    return {"status": "ok", "result": await build_lldp_discovery_preview(body)}


async def discovery_lldp_apply(body: DiscoveryLldpApplyPayload):
    return {"status": "ok", "result": await apply_lldp_discovery(body)}


async def netbox_snmp_discovery_preview(body: NetBoxSnmpProbePayloadSchema):
    return {"status": "ok", "result": await build_netbox_snmp_probe_service(snmp_discovery_runtime(), body)}


async def netbox_snmp_discovery_import(body: NetBoxSnmpImportPayloadSchema):
    return {"status": "ok", "result": await import_netbox_device_from_snmp_service(snmp_discovery_runtime(), body)}


async def netbox_device_enrichment(device_id: int):
    return {"status": "ok", "result": await build_netbox_enrichment(device_id)}


async def netbox_apply_enrichment(device_id: int, body: NetBoxEnrichmentApplyPayload):
    if not body.actions:
        raise HTTPException(400, "No enrichment actions were provided")

    device_result = await netbox_request("GET", f"/dcim/devices/{device_id}/")
    device = device_result.get("result") or {}
    sources = await correlated_sources_for_netbox_device(device_id)
    zabbix_host = sources["zabbix"]
    results: list[dict[str, Any]] = []

    for action in body.actions:
        action_type = action.action_type
        value = compact_text(action.value)
        try:
            if action_type == "set_platform":
                if not value:
                    raise HTTPException(400, "Platform value is required")
                ensured = await ensure_netbox_platform(value)
                platform = ensured["platform"]
                updated = await netbox_request("PATCH", f"/dcim/devices/{device_id}/", {"platform": int(platform["id"])})
                results.append({"action_type": action_type, "status": "ok", "created": ensured["created"], "platform": platform, "device": updated.get("result")})
            elif action_type == "set_location":
                if not value:
                    raise HTTPException(400, "Location value is required")
                ensured = await ensure_netbox_location(value, (device.get("site") or {}).get("id"))
                location = ensured["location"]
                updated = await netbox_request("PATCH", f"/dcim/devices/{device_id}/", {"location": int(location["id"])})
                results.append({"action_type": action_type, "status": "ok", "created": ensured["created"], "location": location, "device": updated.get("result")})
            elif action_type == "set_primary_ip4":
                applied = await ensure_netbox_primary_ip4(device, value)
                results.append({"action_type": action_type, "status": "ok", **applied})
            elif action_type == "migrate_description_ip":
                applied = await ensure_netbox_primary_ip4(device, value or compact_text(device.get("description")))
                cleared = await netbox_request("PATCH", f"/dcim/devices/{device_id}/", {"description": ""})
                results.append({"action_type": action_type, "status": "ok", **applied, "description_cleared": True, "device": cleared.get("result")})
            elif action_type == "set_comments":
                updated = await netbox_request("PATCH", f"/dcim/devices/{device_id}/", {"comments": action.value or ""})
                results.append({"action_type": action_type, "status": "ok", "device": updated.get("result")})
            elif action_type == "set_description":
                updated = await netbox_request("PATCH", f"/dcim/devices/{device_id}/", {"description": action.value or ""})
                results.append({"action_type": action_type, "status": "ok", "device": updated.get("result")})
            elif action_type == "set_serial":
                updated = await netbox_request("PATCH", f"/dcim/devices/{device_id}/", {"serial": action.value or ""})
                results.append({"action_type": action_type, "status": "ok", "device": updated.get("result")})
            elif action_type == "set_asset_tag":
                updated = await netbox_request("PATCH", f"/dcim/devices/{device_id}/", {"asset_tag": action.value or ""})
                results.append({"action_type": action_type, "status": "ok", "device": updated.get("result")})
            elif action_type == "ensure_zabbix_serial_item":
                if not zabbix_host:
                    raise HTTPException(400, "No correlated Zabbix host is available")
                ensured = await ensure_zabbix_serial_item(str(zabbix_host["hostid"]))
                results.append({"action_type": action_type, "status": "ok", **ensured})
            else:
                raise HTTPException(400, f"Unsupported enrichment action: {action_type}")
        except HTTPException as exc:
            results.append({"action_type": action_type, "status": "error", "error": exc.detail})

    refreshed = await build_netbox_enrichment(device_id)
    return {"status": "ok", "results": results, "result": refreshed}


async def netbox_fix_primary_ip4_correlated(body: NetBoxPrimaryIpFixPayload = NetBoxPrimaryIpFixPayload()):
    results: list[dict[str, Any]] = []
    for group in list_correlations():
        items = group.get("items") or {}
        nb_item = items.get("netbox")
        zb_item = items.get("zabbix")
        if not nb_item or not zb_item:
            continue
        if archive_status("netbox", nb_item["id"]):
            continue

        try:
            device_result = await netbox_request("GET", f"/dcim/devices/{int(nb_item['id'])}/")
            device = device_result.get("result") or {}
            zabbix_result = await zabbix_request(
                "host.get",
                {
                    "output": "extend",
                    "hostids": [zb_item["id"]],
                    "selectInterfaces": "extend",
                    "selectInventory": "extend",
                },
            )
            host = (zabbix_result.get("result") or [None])[0]
            if not host:
                results.append({"device_id": nb_item["id"], "device": nb_item.get("label"), "status": "skipped", "reason": "Missing correlated Zabbix host"})
                continue

            candidate_ip = extract_main_zabbix_ip(host)
            if not candidate_ip:
                results.append({"device_id": nb_item["id"], "device": device.get("name"), "status": "skipped", "reason": "No IPv4 on main Zabbix interface"})
                continue
            if device.get("primary_ip4"):
                results.append({"device_id": nb_item["id"], "device": device.get("name"), "status": "skipped", "reason": "Primary IPv4 already set", "current": device.get("primary_ip4", {}).get("address")})
                continue
            if body.dry_run:
                results.append({"device_id": nb_item["id"], "device": device.get("name"), "status": "planned", "proposed_ip": candidate_ip})
                continue

            applied = await ensure_netbox_primary_ip4(device, candidate_ip)
            results.append({"device_id": nb_item["id"], "device": device.get("name"), "status": "updated", **applied})
        except HTTPException as exc:
            results.append({"device_id": nb_item["id"], "device": nb_item.get("label"), "status": "error", "error": exc.detail})

    return {"status": "ok", "count": len(results), "result": results}


async def observium_devices(limit: int = 500, search: str = "", archived: str = "exclude"):
    try:
        query = """
            SELECT *
            FROM devices
        """
        params: list[Any] = []
        if search:
            query += " WHERE hostname LIKE %s OR sysName LIKE %s OR ip LIKE %s OR location LIKE %s"
            token = f"%{search}%"
            params.extend([token, token, token, token])
        query += " ORDER BY hostname ASC LIMIT %s"
        params.append(limit)
        rows = observium_db_query(query, tuple(params))
        items = [normalize_observium_device(row) for row in rows]
    except Exception:
        client = observium_web_client()
        if not client.configured():
            raise
        items = [normalize_observium_device(row) for row in client.device_entities()]
        if search:
            token = normalize_key(search)
            items = [item for item in items if token in normalize_key(item.get("hostname")) or token in normalize_key(item.get("sysName")) or token in normalize_key(item.get("ip"))]
        items = items[:limit]
    return {"result": apply_archive_mode("observium", items, "device_id", archived)}


async def observium_device(device_id: int):
    row = observium_device_row(device_id)
    if not row:
        raise HTTPException(404, "Observium device not found")
    result = normalize_observium_device(row)
    result["archived"] = archive_status("observium", str(device_id))
    return {"result": result}


async def observium_device_ports(device_id: int):
    row = observium_device_row(device_id)
    if not row:
        raise HTTPException(404, "Observium device not found")
    return {"result": observium_port_rows(device_id)}


async def observium_create_device(body: ObserviumDeviceCreate):
    add_result = observium_exec(build_observium_add_command(body))
    row = observium_db_query("SELECT device_id, hostname FROM devices WHERE hostname = %s", (body.hostname,), fetch="one")
    if not row:
        raise HTTPException(400, f"Observium add failed: {add_result['output']}")

    updates = {"label": body.label, "location": body.location, "purpose": body.purpose, "ip": body.hostname}
    updates = {key: value for key, value in updates.items() if value}
    icmp_column = observium_optional_icmp_column()
    if icmp_column:
        updates[icmp_column] = int(body.skip_icmp)
    if updates:
        await observium_update_device(row["device_id"], updates)

    refresh_result = None
    if body.run_discovery or body.run_poller:
        refresh_result = {}
        if body.run_discovery:
            refresh_result["discovery"] = run_observium_discovery(int(row["device_id"]))
        if body.run_poller:
            refresh_result["poller"] = run_observium_poller(int(row["device_id"]))
    return {"status": "ok", "device_id": row["device_id"], "hostname": row["hostname"], "add_result": add_result, "refresh_result": refresh_result}


async def observium_update_device(device_id: int, body: dict[str, Any]):
    current = observium_db_query("SELECT * FROM devices WHERE device_id = %s", (device_id,), fetch="one")
    if not current:
        raise HTTPException(404, "Observium device not found")

    rename_result = None
    if "hostname" in body and body["hostname"] and body["hostname"] != current["hostname"]:
        rename_result = observium_exec(["php", "/opt/observium/rename_device.php", "-p", current["hostname"], body["hostname"]])

    allowed_fields = {
        "label", "ip", "snmp_version", "snmp_community", "snmp_port", "snmp_transport",
        "snmp_authlevel", "snmp_authname", "snmp_authpass", "snmp_authalgo",
        "snmp_cryptopass", "snmp_cryptoalgo", "snmp_context", "location", "purpose",
        "disabled", "ignore", "status",
    }
    icmp_column = observium_optional_icmp_column()
    translated_body = dict(body)
    if "skip_icmp" in translated_body and icmp_column:
        translated_body[icmp_column] = int(bool(translated_body.pop("skip_icmp")))
    updates: list[str] = []
    params: list[Any] = []
    for field, value in translated_body.items():
        if field not in allowed_fields:
            if icmp_column and field == icmp_column:
                updates.append(f"`{field}` = %s")
                params.append(int(value))
            continue
        updates.append(f"`{field}` = %s")
        if field in {"disabled", "ignore", "status", "snmp_port"} and value is not None:
            params.append(int(value))
        else:
            params.append(value)

    if updates:
        params.append(device_id)
        observium_db_query(f"UPDATE devices SET {', '.join(updates)} WHERE device_id = %s", tuple(params), fetch="none")

    updated = observium_db_query("SELECT * FROM devices WHERE device_id = %s", (device_id,), fetch="one")
    hostname_changed = bool(body.get("hostname") and body["hostname"] != current["hostname"])
    if hostname_changed and updated and updated["hostname"] == current["hostname"] and not is_ip_address(str(body["hostname"])):
        updates = []
        params = []
        if not body.get("label"):
            updates.append("`label` = %s")
            params.append(body["hostname"])
        if updates:
            params.append(device_id)
            observium_db_query(f"UPDATE devices SET {', '.join(updates)} WHERE device_id = %s", tuple(params), fetch="none")
            updated = observium_db_query("SELECT * FROM devices WHERE device_id = %s", (device_id,), fetch="one")
    return {"status": "ok", "rename_result": rename_result, "result": normalize_observium_device(updated)}


async def observium_delete_device(device_id: int):
    current = observium_db_query("SELECT hostname FROM devices WHERE device_id = %s", (device_id,), fetch="one")
    if not current:
        raise HTTPException(404, "Observium device not found")
    result = observium_exec(["php", "/opt/observium/delete_device.php", current["hostname"]])
    return {"status": "ok", "hostname": current["hostname"], "result": result}


async def observium_refresh(device_id: int):
    return observium_refresh_device(device_id)


async def export_zabbix_to_observium(body: ExportZabbixToObserviumPayload):
    if not body.hostids:
        raise HTTPException(400, "hostids is required")

    zabbix_result = await zabbix_request(
        "host.get",
        {
            "output": "extend",
            "hostids": body.hostids,
            "selectInterfaces": "extend",
            "selectInventory": "extend",
            "selectMacros": "extend",
            "selectTags": "extend",
            "selectGroups": "extend",
        },
    )
    global_macros = await zabbix_global_macros()

    results: list[dict[str, Any]] = []
    for host in zabbix_result["result"] or []:
        host["globalmacros"] = global_macros
        payload_snapshot = {"hostid": host.get("hostid"), "host": host.get("host"), "name": host.get("name")}
        try:
            candidate = extract_zabbix_snmp_host(host)
            try:
                snmp_check = observium_snmp_check(candidate["address"], candidate["port"], candidate["snmp"])
            except HTTPException as exc:
                snmp_check = {"status": "error", "message": str(exc.detail)}
            existing = observium_db_query(
                "SELECT device_id, hostname FROM devices WHERE hostname = %s OR ip = %s LIMIT 1",
                (candidate["address"], candidate["address"]),
                fetch="one",
            )

            if existing and body.update_existing:
                update_payload = {
                    "hostname": candidate["address"],
                    "ip": candidate["address"],
                    "snmp_version": candidate["snmp"]["version"],
                    "snmp_community": candidate["snmp"].get("community"),
                    "snmp_port": candidate["port"],
                    "snmp_transport": "udp",
                    "location": candidate["inventory"].get("location") or "",
                    "purpose": candidate["inventory"].get("type_full") or "",
                    "label": candidate["name"],
                }
                updated = await observium_update_device(int(existing["device_id"]), update_payload)
                refresh = observium_refresh_device(int(existing["device_id"])) if (body.run_discovery or body.run_poller) else None
                result = {"status": "updated", "hostid": candidate["hostid"], "host": candidate["host"], "observium_device_id": existing["device_id"], "snmp_check": snmp_check, "update": updated, "refresh": refresh}
            elif existing:
                result = {"status": "skipped", "hostid": candidate["hostid"], "host": candidate["host"], "observium_device_id": existing["device_id"], "message": "Device already exists in Observium"}
            else:
                created = await observium_create_device(
                    ObserviumDeviceCreate(
                        hostname=candidate["address"],
                        snmp_version=candidate["snmp"]["version"],
                        snmp_community=candidate["snmp"].get("community"),
                        snmp_port=candidate["port"],
                        snmp_transport="udp",
                        snmp_authlevel=candidate["snmp"].get("security_level"),
                        snmp_authname=candidate["snmp"].get("security_name"),
                        snmp_authpass=candidate["snmp"].get("auth_password"),
                        snmp_authalgo=candidate["snmp"].get("auth_protocol"),
                        snmp_cryptopass=candidate["snmp"].get("priv_password"),
                        snmp_cryptoalgo=candidate["snmp"].get("priv_protocol"),
                        label=candidate["name"],
                        location=candidate["inventory"].get("location") or "",
                        purpose=candidate["inventory"].get("type_full") or "",
                        run_discovery=body.run_discovery,
                        run_poller=body.run_poller,
                    )
                )
                result = {"status": "created", "hostid": candidate["hostid"], "host": candidate["host"], "snmp_check": snmp_check, "create": created}

            log_export(action="manual_export", source="zabbix", target="observium", status=result["status"], entity_id=str(host.get("hostid")), entity_label=host.get("host"), message=f"Zabbix host {host.get('host')} exported to Observium ({result['status']})", payload=payload_snapshot, response=result)
            results.append(result)
        except Exception as exc:
            error_result = {"status": "error", "hostid": host.get("hostid"), "host": host.get("host"), "message": str(exc)}
            log_export(action="manual_export", source="zabbix", target="observium", status="error", entity_id=str(host.get("hostid")), entity_label=host.get("host"), message=str(exc), payload=payload_snapshot, response=error_result)
            results.append(error_result)

    return {"status": "ok", "count": len(results), "result": results}
