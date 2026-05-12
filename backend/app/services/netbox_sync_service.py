from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.repositories.archive_repository import log_export
from app.repositories.correlations_repository import find_correlation_by_item
from app.repositories.sync_profiles_repository import (
    SYNC_FIELDS,
    SYNC_SOURCE_DEFAULTS,
    get_netbox_sync_profile,
    save_netbox_sync_profile,
)
from app.schemas.interface_sync import NetBoxInterfaceSyncRunPayload
from app.services.interface_sync import (
    InterfaceSyncRuntime,
    apply_sync,
    build_preview,
)
from app.shared.networking import host_part, parse_mac_address
from app.shared.text import as_int, compact_text


async def correlated_sources_for_device(device_id: int) -> dict[str, Any]:
    from app.services.observium_service import device as observium_device
    from app.services.zabbix_service import global_macros, request as zabbix_request

    correlation = find_correlation_by_item("netbox", str(device_id))
    if not correlation:
        raise HTTPException(404, "No saved correlation exists for this NetBox device")
    items = correlation.get("items") or {}
    zabbix_host = None
    observium = None
    if items.get("zabbix"):
        macros = await global_macros()
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
            zabbix_host["globalmacros"] = macros
    if items.get("observium"):
        observium = observium_device(int(items["observium"]["id"]))
    return {"correlation": correlation, "zabbix": zabbix_host, "observium": observium}


async def find_interface_by_observium_port(device_id: int, observium_port: dict[str, Any] | None) -> dict[str, Any] | None:
    from app.services.interface_sync import interface_match_keys, netbox_interface_summary
    from app.services.netbox_service import paginated

    if not device_id or not observium_port:
        return None
    interfaces = [netbox_interface_summary(item, parse_mac_address) for item in await paginated("/dcim/interfaces/", {"device_id": device_id, "limit": 500})]
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
    local_interface: dict[str, Any] | None,
    local_observium_port: dict[str, Any] | None,
    link_rows: dict[int, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    from app.domain.observium_normalization import first_non_empty
    from app.services.netbox_service import find_device_by_identity, interface_connected_peer
    from app.services.observium_service import device as observium_device
    from app.services.observium_service import device_ports, ip_from_mac, link_neighbor_mac, link_neighbor_name

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
    remote_device = observium_device(remote_device_id) if remote_device_id else None
    remote_port = None
    if remote_device_id:
        remote_port_rows = device_ports(remote_device_id)
        remote_port = next((item for item in remote_port_rows if int(item.get("port_id") or 0) == remote_port_id), None)
    remote_mac = link_neighbor_mac(row) or parse_mac_address((remote_port or {}).get("mac_address"))
    remote_ip = host_part((remote_device or {}).get("ip") or "") or (ip_from_mac(remote_mac) if remote_mac else "")
    remote_name = compact_text((remote_device or {}).get("sysName")) or compact_text((remote_device or {}).get("hostname")) or link_neighbor_name(row) or compact_text((remote_port or {}).get("description"))
    remote_netbox_device = await find_device_by_identity(remote_name, remote_ip, remote_mac)
    remote_netbox_interface = None
    if remote_netbox_device and remote_port:
        remote_netbox_interface = await find_interface_by_observium_port(int(remote_netbox_device["id"]), remote_port)
    current_peer = await interface_connected_peer(int(local_interface["id"])) if local_interface and local_interface.get("id") is not None else None
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


def _runtime() -> InterfaceSyncRuntime:
    from app.domain.netbox_normalization import description_ip_candidate, extract_main_zabbix_ip, extract_observium_ipv4
    from app.domain.observium_normalization import first_non_empty
    from app.services.netbox_service import (
        clear_interface_dependencies,
        ensure_interface_cable,
        ensure_interface_mac,
        ensure_primary_ip4,
        ensure_vlan_tags,
        paginated,
        request,
    )
    from app.services.observium_service import link_rows, device_ports

    return InterfaceSyncRuntime(
        clear_netbox_interface_dependencies=clear_interface_dependencies,
        correlated_sources_for_netbox_device=correlated_sources_for_device,
        description_ip_candidate=description_ip_candidate,
        ensure_netbox_interface_cable=ensure_interface_cable,
        ensure_netbox_interface_mac=ensure_interface_mac,
        ensure_netbox_primary_ip4=ensure_primary_ip4,
        ensure_vlan_tags=ensure_vlan_tags,
        extract_main_zabbix_ip=extract_main_zabbix_ip,
        extract_observium_ipv4=extract_observium_ipv4,
        first_non_empty=first_non_empty,
        log_export=log_export,
        netbox_paginated=paginated,
        netbox_request=request,
        observium_link_rows=link_rows,
        observium_port_rows=device_ports,
        resolve_interface_connection_candidate=resolve_interface_connection_candidate,
    )


async def build_interface_sync_preview(device_id: int) -> dict[str, Any]:
    return await build_preview(_runtime(), device_id, parse_mac_address)


async def apply_interface_sync_for_device(device_id: int, options: NetBoxInterfaceSyncRunPayload) -> dict[str, Any]:
    return await apply_sync(_runtime(), device_id, options, parse_mac_address)


async def build_sync_preview(device_id: int) -> dict[str, Any]:
    from app.domain.netbox_normalization import extract_main_zabbix_ip, extract_observium_ipv4, pick_platform_name
    from app.services.netbox_service import get_device
    from app.services.observium_service import serial_candidate_from_observium
    from app.services.zabbix_service import find_serial_item, serial_candidate_from_host

    device_result = await get_device(device_id)
    device = device_result.get("result") or {}
    sources = await correlated_sources_for_device(device_id)
    zabbix_host = sources["zabbix"]
    observium_device = sources["observium"]
    serial_item = await find_serial_item(str(zabbix_host["hostid"])) if zabbix_host else None
    profile = get_netbox_sync_profile(device_id)
    candidates = {
        "primary_ip4": {"zabbix": extract_main_zabbix_ip(zabbix_host or {}), "observium": extract_observium_ipv4(observium_device or {})},
        "serial": {"zabbix": serial_candidate_from_host(zabbix_host, serial_item), "observium": serial_candidate_from_observium(observium_device)},
        "platform": {"zabbix": pick_platform_name(device, zabbix_host, None), "observium": pick_platform_name(device, None, observium_device)},
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
            "candidates": {source: compact_text(value) for source, value in candidates[field].items()},
        }
    return {
        "device": device,
        "correlation": sources["correlation"],
        "profile": profile,
        "sources": {
            "zabbix": {"hostid": str(zabbix_host.get("hostid")) if zabbix_host else None, "host": (zabbix_host or {}).get("host"), "name": (zabbix_host or {}).get("name")} if zabbix_host else None,
            "observium": {"device_id": str(observium_device.get("device_id")) if observium_device else None, "hostname": (observium_device or {}).get("hostname"), "sysName": (observium_device or {}).get("sysName")} if observium_device else None,
        },
        "fields": fields,
    }


async def update_sync_profile(device_id: int, enabled: bool, field_sources: dict[str, str]) -> dict[str, Any]:
    profile = save_netbox_sync_profile(device_id, enabled, field_sources)
    return {"status": "ok", "result": {"profile": profile, "preview": await build_sync_preview(device_id)}}


async def apply_sync_profile(device_id: int, enabled: bool, field_sources: dict[str, str]) -> dict[str, Any]:
    from app.services.netbox_service import ensure_platform, ensure_primary_ip4, request

    profile = save_netbox_sync_profile(device_id, enabled, field_sources)
    preview = await build_sync_preview(device_id)
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
                results.append({"field": field, "status": "updated", "source": source, **(await ensure_primary_ip4(device, value))})
            elif field == "serial":
                await request("PATCH", f"/dcim/devices/{device_id}/", {"serial": value})
                results.append({"field": field, "status": "updated", "source": source, "value": value})
            elif field == "platform":
                ensured = await ensure_platform(value)
                await request("PATCH", f"/dcim/devices/{device_id}/", {"platform": int(ensured['platform']['id'])})
                results.append({"field": field, "status": "updated", "source": source, "value": value, "created": ensured["created"]})
        except HTTPException as exc:
            results.append({"field": field, "status": "error", "source": source, "error": exc.detail})
    return {"profile": profile, "results": results, "preview": await build_sync_preview(device_id)}
