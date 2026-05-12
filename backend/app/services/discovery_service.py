from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.domain.netbox_normalization import build_observium_platform_label, extract_main_zabbix_ip
from app.domain.observium_normalization import first_non_empty
from app.schemas.discovery import DiscoveryLldpApplyPayload, DiscoveryLldpPreviewPayload
from app.shared.networking import host_part, parse_mac_address
from app.shared.text import as_int, compact_text


async def resolve_source(body: DiscoveryLldpPreviewPayload | DiscoveryLldpApplyPayload) -> dict[str, Any]:
    from app.repositories.correlations_repository import find_correlation_by_item
    from app.services.observium_service import device as observium_device
    from app.services.observium_service import find_device_by_identity as observium_find_device_by_identity
    from app.services.zabbix_service import request as zabbix_request

    source = compact_text(body.source).lower()
    source_id = str(body.source_id).strip()
    if source not in {"observium", "zabbix"}:
        raise HTTPException(400, "LLDP source must be observium or zabbix")
    if not source_id:
        raise HTTPException(400, "source_id is required")
    zabbix_host = None
    observium = None
    if source == "observium":
        observium = observium_device(int(source_id))
        if not observium:
            raise HTTPException(404, "Observium source device was not found")
        from app.services.zabbix_service import find_host_by_identity
        zabbix_host = await find_host_by_identity(observium.get("sysName") or observium.get("hostname") or "", observium.get("ip") or "")
    else:
        zabbix_result = await zabbix_request(
            "host.get",
            {"output": "extend", "hostids": [source_id], "selectInterfaces": "extend", "selectInventory": "extend", "selectTags": "extend", "selectMacros": "extend"},
        )
        zabbix_host = (zabbix_result.get("result") or [None])[0]
        if not zabbix_host:
            raise HTTPException(404, "Zabbix source host was not found")
        observium = await observium_find_device_by_identity(zabbix_host.get("host") or zabbix_host.get("name") or "", extract_main_zabbix_ip(zabbix_host) or "")
        correlation = find_correlation_by_item("zabbix", source_id)
        if not observium and correlation and (correlation.get("items") or {}).get("observium"):
            observium = observium_device(int(correlation["items"]["observium"]["id"]))
    if not observium:
        raise HTTPException(404, "No Observium device could be resolved for the selected source")
    return {"source": source, "source_id": source_id, "zabbix": zabbix_host, "observium": observium}


async def build_neighbor_candidate(
    *,
    local_source: dict[str, Any],
    local_port: dict[str, Any] | None,
    link_row: dict[str, Any],
    remote_device: dict[str, Any] | None,
    remote_port: dict[str, Any] | None,
) -> dict[str, Any] | None:
    from app.services.netbox_service import find_device_by_identity
    from app.services.observium_service import find_device_by_identity as observium_find_device_by_identity
    from app.services.observium_service import ip_from_mac, link_neighbor_mac, link_neighbor_name
    from app.services.zabbix_service import find_host_by_identity

    remote_mac = link_neighbor_mac(link_row) or parse_mac_address((remote_port or {}).get("mac_address"))
    remote_ip = host_part((remote_device or {}).get("ip") or "")
    if not remote_ip and remote_mac:
        remote_ip = ip_from_mac(remote_mac)
    remote_name = compact_text((remote_device or {}).get("sysName")) or compact_text((remote_device or {}).get("hostname")) or compact_text((remote_port or {}).get("description")) or link_neighbor_name(link_row)
    if not remote_name and not remote_mac and not remote_ip:
        return None
    existing_netbox = await find_device_by_identity(remote_name, remote_ip, remote_mac)
    existing_zabbix = await find_host_by_identity(remote_name, remote_ip, remote_mac)
    existing_observium = remote_device or await observium_find_device_by_identity(remote_name, remote_ip, remote_mac)
    manufacturer_name = compact_text((existing_observium or {}).get("vendor")) or "Unknown Vendor"
    model_name = compact_text((existing_observium or {}).get("hardware")) or remote_name
    create_candidate = {
        "name": remote_name or (remote_ip or remote_mac or "lldp-neighbor"),
        "manufacturer_name": manufacturer_name,
        "model_name": model_name,
        "platform_name": build_observium_platform_label(existing_observium),
        "serial": compact_text((existing_observium or {}).get("serial")),
        "primary_ip4": f"{remote_ip}/32" if remote_ip else "",
        "description": build_observium_platform_label(existing_observium),
    }
    return {
        "id": f"{local_source['observium']['device_id']}:{(local_port or {}).get('port_id') or 0}:{remote_name or remote_mac or remote_ip}",
        "protocol": compact_text(first_non_empty(link_row, "protocol", "link_type")) or "LLDP/CDP",
        "local_port": local_port,
        "remote_port": remote_port,
        "neighbor": {"name": remote_name, "ip": remote_ip, "mac_address": remote_mac},
        "matches": {"netbox": existing_netbox, "zabbix": existing_zabbix, "observium": existing_observium},
        "status": "matched" if (existing_netbox or existing_zabbix or existing_observium) else "proposed_create",
        "create_candidate": create_candidate,
    }


async def build_preview(body: DiscoveryLldpPreviewPayload | DiscoveryLldpApplyPayload) -> dict[str, Any]:
    from app.services.observium_service import device as observium_device
    from app.services.observium_service import device_ports, link_rows

    source_ctx = await resolve_source(body)
    observium = source_ctx["observium"]
    ports = device_ports(int(observium["device_id"]))
    ports_by_id = {int(port["port_id"]): port for port in ports if port.get("port_id")}
    rows = link_rows(int(observium["device_id"]))
    proposals: list[dict[str, Any]] = []
    for row in rows:
        local_port_id = as_int(first_non_empty(row, "local_port_id", "port_id", "port_id_local")) or 0
        local_port = ports_by_id.get(local_port_id)
        remote_device_id = as_int(first_non_empty(row, "remote_device_id", "peer_device_id", "device_id_remote"))
        remote_port_id = as_int(first_non_empty(row, "remote_port_id", "peer_port_id", "port_id_remote"))
        remote_device = observium_device(remote_device_id) if remote_device_id else None
        remote_ports = device_ports(remote_device_id) if remote_device_id else []
        remote_port = next((item for item in remote_ports if int(item.get("port_id") or 0) == remote_port_id), None)
        candidate = await build_neighbor_candidate(local_source=source_ctx, local_port=local_port, link_row=row, remote_device=remote_device, remote_port=remote_port)
        if candidate:
            proposals.append(candidate)
    return {
        "source": {"platform": source_ctx["source"], "source_id": source_ctx["source_id"], "zabbix": source_ctx["zabbix"], "observium": source_ctx["observium"]},
        "summary": {"links": len(proposals), "matched": sum(1 for item in proposals if item["status"] == "matched"), "proposed_create": sum(1 for item in proposals if item["status"] == "proposed_create")},
        "proposals": proposals,
    }


async def apply(body: DiscoveryLldpApplyPayload) -> dict[str, Any]:
    from app.services.netbox_service import ensure_device_type, ensure_platform, ensure_primary_ip4, request as netbox_request

    preview = await build_preview(body)
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
        ensured_device_type = await ensure_device_type(candidate.get("model_name") or candidate.get("name") or "Unknown Model", candidate.get("manufacturer_name") or "Unknown Vendor")
        device_type = ensured_device_type.get("device_type") or {}
        payload = {"name": candidate.get("name"), "status": "active", "site": int(body.site_id), "role": int(body.role_id), "device_type": int(device_type["id"]), "serial": candidate.get("serial") or "", "description": candidate.get("description") or ""}
        try:
            created = await netbox_request("POST", "/dcim/devices/", payload)
            created_device = created.get("result") or created.get("response") or {}
            if candidate.get("platform_name"):
                ensured_platform = await ensure_platform(candidate["platform_name"])
                platform = ensured_platform.get("platform") or {}
                if platform.get("id"):
                    await netbox_request("PATCH", f"/dcim/devices/{int(created_device['id'])}/", {"platform": int(platform["id"])})
            if candidate.get("primary_ip4"):
                await ensure_primary_ip4(created_device, candidate["primary_ip4"])
            results.append({"status": "created", "id": item["id"], "device": created_device, "payload": payload, "device_type": device_type})
        except HTTPException as exc:
            results.append({"status": "error", "id": item["id"], "error": exc.detail, "payload": payload})
    return {"preview": await build_preview(body), "results": results}
