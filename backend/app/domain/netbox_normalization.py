from __future__ import annotations

from typing import Any

from app.shared.networking import ipv4_candidate
from app.shared.text import compact_text


def description_ip_candidate(device: dict[str, Any]) -> str | None:
    return ipv4_candidate(device.get("description"))


def extract_main_zabbix_ip(host: dict[str, Any]) -> str | None:
    interfaces = host.get("interfaces") or []
    preferred = next((item for item in interfaces if str(item.get("main")) == "1"), None) or (interfaces[0] if interfaces else None)
    if not preferred:
        return None
    raw = preferred.get("ip") if str(preferred.get("useip", "1")) == "1" else preferred.get("dns")
    return ipv4_candidate(raw)


def extract_observium_ipv4(device: dict[str, Any]) -> str | None:
    return ipv4_candidate(device.get("ip"))


def build_os_version_label(os_name: str | None, version: str | None) -> str:
    os_value = compact_text(os_name)
    version_value = compact_text(version)
    return compact_text(" ".join(part for part in (os_value, version_value) if part))


def build_zabbix_platform_label(host: dict[str, Any] | None) -> str:
    inventory = (host or {}).get("inventory") or {}
    return build_os_version_label(
        inventory.get("os_full") or inventory.get("os") or (host or {}).get("vendor_name"),
        (host or {}).get("vendor_version") or inventory.get("os_short") or inventory.get("software_full") or inventory.get("software"),
    )


def build_observium_platform_label(device: dict[str, Any] | None) -> str:
    return build_os_version_label((device or {}).get("os"), (device or {}).get("version"))


def pick_platform_name(
    device: dict[str, Any],
    zabbix_host: dict[str, Any] | None,
    observium_device: dict[str, Any] | None,
) -> str | None:
    return (
        build_observium_platform_label(observium_device)
        or build_zabbix_platform_label(zabbix_host)
        or compact_text((device.get("platform") or {}).get("display") or (device.get("platform") or {}).get("name"))
        or None
    )


def build_enriched_comments(
    device: dict[str, Any],
    zabbix_host: dict[str, Any] | None,
    observium_device: dict[str, Any] | None,
) -> str:
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


def build_enriched_description(
    device: dict[str, Any],
    zabbix_host: dict[str, Any] | None,
    observium_device: dict[str, Any] | None,
) -> str:
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
