from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from fastapi import HTTPException

from app.schemas.snmp import NetBoxSnmpImportPayload, NetBoxSnmpProbePayload
from app.shared.security import sanitize_payload
from app.shared.text import compact_text


SNMP_V3_AUTH_PROTOCOLS = {"MD5", "SHA", "SHA-224", "SHA-256", "SHA-384", "SHA-512"}
SNMP_V3_PRIV_PROTOCOLS = {"DES", "AES", "AES128", "AES192", "AES256"}
SNMP_V3_LEVELS = {
    "noauthnopriv": "noAuthNoPriv",
    "authnopriv": "authNoPriv",
    "authpriv": "authPriv",
}


@dataclass
class SnmpDiscoveryRuntime:
    observium_exec_result: Callable[[list[str]], dict[str, Any]]
    observium_find_device_by_identity: Callable[[str, str], Awaitable[dict[str, Any] | None]]
    zabbix_find_host_by_identity: Callable[[str, str], Awaitable[dict[str, Any] | None]]
    netbox_find_device_by_identity: Callable[[str, str], Awaitable[dict[str, Any] | None]]
    netbox_find_device_type_by_model: Callable[[str], Awaitable[dict[str, Any] | None]]
    ensure_netbox_device_type: Callable[[str, str], Awaitable[dict[str, Any]]]
    ensure_netbox_platform: Callable[[str], Awaitable[dict[str, Any]]]
    ensure_netbox_primary_ip4: Callable[[dict[str, Any], str], Awaitable[dict[str, Any]]]
    netbox_request: Callable[[str, str, dict[str, Any] | None], Awaitable[Any]]
    normalize_ip_value: Callable[[str], str]
    build_os_version_label: Callable[[str | None, str | None], str]


def parse_snmp_value(output: str) -> str:
    text = compact_text(output)
    return text.strip("\"'")


def _normalize_snmp_v3_level(value: str) -> str:
    normalized = compact_text(value).replace("_", "").replace("-", "").lower()
    if normalized not in SNMP_V3_LEVELS:
        raise HTTPException(400, "SNMP v3 auth level must be noAuthNoPriv, authNoPriv or authPriv")
    return SNMP_V3_LEVELS[normalized]


def validate_snmp_payload(payload: NetBoxSnmpProbePayload) -> None:
    version = compact_text(payload.snmp_version).lower()
    if not compact_text(payload.ip):
        raise HTTPException(400, "SNMP IP is required")
    if version not in {"v1", "v2c", "v3"}:
        raise HTTPException(400, "Unsupported SNMP version")

    if version != "v3":
        if not compact_text(payload.snmp_community):
            raise HTTPException(400, "SNMP community is required for v1/v2c")
        return

    level = _normalize_snmp_v3_level(payload.snmp_authlevel or "")
    if not compact_text(payload.snmp_authname):
        raise HTTPException(400, "SNMP v3 username is required")
    if level in {"authNoPriv", "authPriv"}:
        if not compact_text(payload.snmp_authpass):
            raise HTTPException(400, "SNMP v3 auth password is required")
        auth_algo = compact_text(payload.snmp_authalgo or "SHA").upper()
        if auth_algo not in SNMP_V3_AUTH_PROTOCOLS:
            raise HTTPException(400, "Unsupported SNMP v3 auth protocol")
    if level == "authPriv":
        if not compact_text(payload.snmp_cryptopass):
            raise HTTPException(400, "SNMP v3 privacy password is required")
        priv_algo = compact_text(payload.snmp_cryptoalgo or "AES").upper()
        if priv_algo not in SNMP_V3_PRIV_PROTOCOLS:
            raise HTTPException(400, "Unsupported SNMP v3 privacy protocol")


def snmp_exec_args(payload: NetBoxSnmpProbePayload, oid: str, walk: bool = False) -> list[str]:
    validate_snmp_payload(payload)

    command = "snmpwalk" if walk else "snmpget"
    version = compact_text(payload.snmp_version).lower()
    args = [command, "-Oqv", f"-{version}", "-t", "2", "-r", "1"]

    if version == "v3":
        level = _normalize_snmp_v3_level(payload.snmp_authlevel or "")
        args.extend(["-l", level, "-u", compact_text(payload.snmp_authname)])
        if level in {"authNoPriv", "authPriv"}:
            args.extend(["-a", compact_text(payload.snmp_authalgo or "SHA").upper(), "-A", payload.snmp_authpass or ""])
        if level == "authPriv":
            args.extend(["-x", compact_text(payload.snmp_cryptoalgo or "AES").upper(), "-X", payload.snmp_cryptopass or ""])
        if compact_text(payload.snmp_context):
            args.extend(["-n", compact_text(payload.snmp_context)])
        args.extend([payload.ip, oid])
        return args

    args.extend(["-c", payload.snmp_community or "public", payload.ip, oid])
    return args


def snmp_probe_value(runtime: SnmpDiscoveryRuntime, payload: NetBoxSnmpProbePayload, oid: str, walk: bool = False) -> str:
    result = runtime.observium_exec_result(snmp_exec_args(payload, oid, walk=walk))
    if result["exit_code"] != 0:
        return ""
    if walk:
        for line in result["output"].splitlines():
            value = parse_snmp_value(line)
            if value and "no such" not in value.lower():
                return value
        return ""
    value = parse_snmp_value(result["output"])
    return "" if "no such" in value.lower() else value


def infer_vendor_from_snmp(sys_descr: str, sys_object_id: str) -> str:
    signature = f"{sys_descr} {sys_object_id}".lower()
    if "forti" in signature:
        return "Fortinet"
    if "cisco" in signature:
        return "Cisco"
    if "allied" in signature:
        return "Allied Telesis"
    if "avaya" in signature or ".1.3.6.1.4.1.45." in signature or "ethernet routing switch" in signature:
        return "Extreme Networks"
    if "aruba" in signature or "procurve" in signature or "hpe" in signature:
        return "HPE Aruba"
    return ""


def infer_model_from_snmp(sys_descr: str) -> str:
    patterns = [
        r"(Ethernet Routing Switch\s+[A-Za-z0-9\-\+]+)",
        r"(FortiSwitch\s+[A-Za-z0-9\-]+)",
        r"(FortiGate\s+[A-Za-z0-9\-]+)",
        r"(C9[0-9A-Za-z\-]+)",
        r"(WS-C[0-9A-Za-z\-]+)",
        r"(AT-[A-Za-z0-9\-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, sys_descr, re.IGNORECASE)
        if match:
            return compact_text(match.group(1))
    return compact_text(sys_descr.split(",")[0])


def official_reference_url(vendor: str, model: str) -> str:
    query = urllib.parse.quote_plus(compact_text(f"{vendor} {model}"))
    vendor_key = compact_text(vendor).lower()
    if "forti" in vendor_key:
        return f"https://docs.fortinet.com/search?q={query}"
    if "cisco" in vendor_key:
        return f"https://www.cisco.com/c/en/us/search.html#q={query}"
    if "allied" in vendor_key:
        return f"https://www.alliedtelesis.com/us/en/search?search={query}"
    if "extreme" in vendor_key or "avaya" in vendor_key:
        return f"https://www.extremenetworks.com/search/?q={query}"
    return ""


def infer_version_from_snmp(sys_descr: str) -> str:
    text = compact_text(sys_descr)
    patterns = [
        r"Version\s+([0-9A-Za-z.\-]+)",
        r"SW:v([0-9A-Za-z.\-]+)",
        r"FW:\s*([0-9A-Za-z.\-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = compact_text(match.group(1))
            duplicate = re.match(r"^([0-9]+(?:\.[0-9A-Za-z]+)+)-\1(?:\b|$)", value)
            if duplicate:
                return duplicate.group(1)
            return value
    return ""


def infer_os_name_from_snmp(sys_descr: str, vendor: str) -> str:
    text = compact_text(sys_descr)
    lowered = text.lower()
    if "arubaos" in lowered:
        return "ArubaOS"
    if "ios software" in lowered or "ios xe" in lowered:
        return "Cisco IOS XE"
    if "fortios" in lowered:
        return "FortiOS"
    return compact_text(vendor)


async def build_netbox_snmp_probe(runtime: SnmpDiscoveryRuntime, payload: NetBoxSnmpProbePayload) -> dict[str, Any]:
    sys_name = snmp_probe_value(runtime, payload, ".1.3.6.1.2.1.1.5.0")
    sys_descr = snmp_probe_value(runtime, payload, ".1.3.6.1.2.1.1.1.0")
    sys_location = snmp_probe_value(runtime, payload, ".1.3.6.1.2.1.1.6.0")
    sys_object_id = snmp_probe_value(runtime, payload, ".1.3.6.1.2.1.1.2.0")
    serial = snmp_probe_value(runtime, payload, ".1.3.6.1.2.1.47.1.1.1.1.11.1", walk=True)
    vendor = infer_vendor_from_snmp(sys_descr, sys_object_id)
    model = infer_model_from_snmp(sys_descr)
    inferred_version = infer_version_from_snmp(sys_descr)
    inferred_os_name = infer_os_name_from_snmp(sys_descr, vendor)
    existing_observium = await runtime.observium_find_device_by_identity(sys_name or model, payload.ip)
    existing_zabbix = await runtime.zabbix_find_host_by_identity(sys_name or model, payload.ip)
    existing_netbox = await runtime.netbox_find_device_by_identity(sys_name or model, payload.ip)
    device_type_match = await runtime.netbox_find_device_type_by_model(model)
    if not serial:
        serial = (
            compact_text((existing_observium or {}).get("serial"))
            or compact_text(((existing_zabbix or {}).get("inventory") or {}).get("serialno_a"))
            or compact_text((existing_netbox or {}).get("serial"))
        )
    platform_label = runtime.build_os_version_label(
        (existing_observium or {}).get("os") or inferred_os_name,
        compact_text((existing_observium or {}).get("version")) or inferred_version,
    )
    description = runtime.build_os_version_label(
        (existing_observium or {}).get("os") or inferred_os_name,
        compact_text((existing_observium or {}).get("version")) or inferred_version,
    )
    return {
        "input": sanitize_payload(payload.model_dump(exclude_none=True)),
        "credential_mode": "snmpv3" if compact_text(payload.snmp_version).lower() == "v3" else "community",
        "extracted": {
            "sysName": sys_name,
            "sysDescr": sys_descr,
            "sysObjectID": sys_object_id,
            "location": sys_location,
            "vendor": vendor,
            "model": model,
            "serial": serial,
            "ip": payload.ip,
        },
        "matches": {
            "netbox": existing_netbox,
            "zabbix": existing_zabbix,
            "observium": existing_observium,
            "device_type": device_type_match,
        },
        "official_reference_url": official_reference_url(vendor, model),
        "proposed_netbox_fields": {
            "name": sys_name or model or payload.ip,
            "site": payload.site_id,
            "role": payload.role_id,
            "device_type": device_type_match.get("id") if device_type_match else None,
            "serial": serial,
            "description": description,
            "primary_ip4": runtime.normalize_ip_value(payload.ip),
            "platform_label": platform_label,
            "location": sys_location,
        },
    }


async def import_netbox_device_from_snmp(runtime: SnmpDiscoveryRuntime, body: NetBoxSnmpImportPayload) -> dict[str, Any]:
    probe = await build_netbox_snmp_probe(runtime, body)
    proposed = probe["proposed_netbox_fields"]
    if not body.site_id or not body.role_id:
        raise HTTPException(400, "site_id and role_id are required")
    device_type_id = int(body.device_type_id or proposed.get("device_type") or 0) or None
    ensured_device_type = None
    if not device_type_id:
        extracted = probe.get("extracted") or {}
        ensured_device_type = await runtime.ensure_netbox_device_type(
            compact_text(extracted.get("model")) or compact_text(body.name) or "Unknown Model",
            compact_text(extracted.get("vendor")) or "Unknown Vendor",
        )
        device_type = ensured_device_type.get("device_type") or {}
        device_type_id = int(device_type.get("id") or 0) or None
    if not device_type_id:
        raise HTTPException(400, "A NetBox device type could not be resolved or created")
    payload = {
        "name": compact_text(body.name or proposed.get("name") or body.ip),
        "status": "active",
        "site": int(body.site_id),
        "role": int(body.role_id),
        "device_type": int(device_type_id),
        "serial": compact_text(body.serial or proposed.get("serial")),
        "description": compact_text(body.description or proposed.get("description")),
    }
    created_device = await runtime.netbox_request("POST", "/dcim/devices/", payload)
    created_device = created_device.get("result") or created_device
    if body.platform_id:
        await runtime.netbox_request("PATCH", f"/dcim/devices/{int(created_device['id'])}/", {"platform": int(body.platform_id)})
    elif proposed.get("platform_label"):
        ensured_platform = await runtime.ensure_netbox_platform(compact_text(proposed["platform_label"]))
        platform = ensured_platform.get("platform") or {}
        if platform.get("id"):
            await runtime.netbox_request("PATCH", f"/dcim/devices/{int(created_device['id'])}/", {"platform": int(platform["id"])})
    if proposed.get("primary_ip4"):
        await runtime.ensure_netbox_primary_ip4(created_device, proposed["primary_ip4"])
    return {
        "probe": probe,
        "device": created_device,
        "device_type": ensured_device_type,
        "request": sanitize_payload(body.model_dump(exclude_none=True)),
    }
