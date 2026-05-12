from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.domain.netbox_normalization import (
    build_enriched_comments,
    build_enriched_description,
    description_ip_candidate,
    extract_main_zabbix_ip,
    extract_observium_ipv4,
    pick_platform_name,
)
from app.repositories.archive_repository import archive_status
from app.repositories.correlations_repository import list_correlations
from app.shared.networking import is_blank
from app.shared.text import compact_text


async def build(device_id: int) -> dict[str, Any]:
    from app.services.netbox_service import find_location, find_platform, get_device
    from app.services.netbox_sync_service import correlated_sources_for_device
    from app.services.observium_service import serial_candidate_from_observium
    from app.services.zabbix_service import find_serial_item, serial_candidate_from_host

    device_result = await get_device(device_id)
    device = device_result.get("result") or {}
    sources = await correlated_sources_for_device(device_id)
    zabbix_host = sources["zabbix"]
    observium_device = sources["observium"]
    serial_item = await find_serial_item(str(zabbix_host["hostid"])) if zabbix_host else None
    platform_name = pick_platform_name(device, zabbix_host, observium_device)
    location_name = compact_text((observium_device or {}).get("location") or ((zabbix_host or {}).get("inventory") or {}).get("location"))
    zabbix_ip = extract_main_zabbix_ip(zabbix_host or {})
    observium_ip = extract_observium_ipv4(observium_device or {})
    description_ip = description_ip_candidate(device)
    serial_candidate = serial_candidate_from_host(zabbix_host, serial_item) or serial_candidate_from_observium(observium_device)
    suggestions: list[dict[str, Any]] = []
    if is_blank(device.get("platform")) and platform_name:
        existing_platform = await find_platform(platform_name)
        suggestions.append({"id": "platform_from_monitoring", "action_type": "set_platform", "field": "platform", "label": "Map platform from Observium/Zabbix", "current_value": (device.get("platform") or {}).get("display") or "", "proposed_value": platform_name, "source": "observium", "confidence": "medium", "requires_create": not bool(existing_platform), "target_section": "DCIM > Platforms"})
    if is_blank(device.get("location")) and location_name:
        existing_location = await find_location(location_name, (device.get("site") or {}).get("id"))
        suggestions.append({"id": "location_from_monitoring", "action_type": "set_location", "field": "location", "label": "Map location from SNMP location", "current_value": (device.get("location") or {}).get("display") or "", "proposed_value": location_name, "source": "observium", "confidence": "medium", "requires_create": not bool(existing_location), "target_section": "DCIM > Locations"})
    if is_blank(device.get("primary_ip4")) and zabbix_ip:
        suggestions.append({"id": "primary_ip4_from_zabbix", "action_type": "set_primary_ip4", "field": "primary_ip4", "label": "Set primary IPv4 from Zabbix", "current_value": (device.get("primary_ip4") or {}).get("address") or "", "proposed_value": zabbix_ip, "source": "zabbix", "confidence": "high", "requires_create": True, "target_section": "IPAM > IP Addresses"})
    elif is_blank(device.get("primary_ip4")) and observium_ip:
        suggestions.append({"id": "primary_ip4_from_observium", "action_type": "set_primary_ip4", "field": "primary_ip4", "label": "Set primary IPv4 from Observium", "current_value": (device.get("primary_ip4") or {}).get("address") or "", "proposed_value": observium_ip, "source": "observium", "confidence": "medium", "requires_create": True, "target_section": "IPAM > IP Addresses"})
    if description_ip and is_blank(device.get("primary_ip4")):
        suggestions.append({"id": "migrate_description_ip", "action_type": "migrate_description_ip", "field": "primary_ip4", "label": "Move IP stored in description to primary IPv4", "current_value": compact_text(device.get("description")), "proposed_value": description_ip, "source": "netbox", "confidence": "high", "requires_create": True, "target_section": "IPAM > IP Addresses"})
    enriched_description = build_enriched_description(device, zabbix_host, observium_device)
    if (is_blank(device.get("description")) or description_ip) and compact_text(enriched_description):
        suggestions.append({"id": "description_from_monitoring", "action_type": "set_description", "field": "description", "label": "Enrich description from monitoring data", "current_value": compact_text(device.get("description")), "proposed_value": enriched_description, "source": "observium", "confidence": "medium", "requires_create": False, "target_section": "Device field"})
    enriched_comments = build_enriched_comments(device, zabbix_host, observium_device)
    if is_blank(device.get("comments")) and compact_text(enriched_comments):
        suggestions.append({"id": "comments_from_monitoring", "action_type": "set_comments", "field": "comments", "label": "Generate comments from SNMP metadata", "current_value": compact_text(device.get("comments")), "proposed_value": enriched_comments, "source": "observium", "confidence": "medium", "requires_create": False, "target_section": "Device field"})
    if is_blank(device.get("serial")) and serial_candidate:
        suggestions.append({"id": "serial_from_zabbix", "action_type": "set_serial", "field": "serial", "label": "Sync serial from monitoring", "current_value": compact_text(device.get("serial")), "proposed_value": serial_candidate, "source": "zabbix", "confidence": "medium", "requires_create": False, "target_section": "Device field"})
    if is_blank(device.get("serial")) and zabbix_host and not serial_candidate:
        suggestions.append({"id": "ensure_zabbix_serial_item", "action_type": "ensure_zabbix_serial_item", "field": "serial", "label": "Create Zabbix SNMP item for entPhysicalSerialNum", "current_value": "", "proposed_value": ".1.3.6.1.2.1.47.1.1.1.1.11.1", "source": "zabbix", "confidence": "medium", "requires_create": False, "target_section": "Zabbix SNMP item", "note": "Value will populate after the next Zabbix poll if the device exposes entPhysicalSerialNum.1"})
    return {"device": device, "correlation": sources["correlation"], "sources": {"zabbix": {"hostid": str(zabbix_host.get("hostid")) if zabbix_host else None, "host": (zabbix_host or {}).get("host"), "name": (zabbix_host or {}).get("name"), "main_ip": zabbix_ip} if zabbix_host else None, "observium": {"device_id": str(observium_device.get("device_id")) if observium_device else None, "hostname": (observium_device or {}).get("hostname"), "sysName": (observium_device or {}).get("sysName"), "ip": observium_ip, "location": (observium_device or {}).get("location"), "os": (observium_device or {}).get("os"), "vendor": (observium_device or {}).get("vendor"), "hardware": (observium_device or {}).get("hardware"), "version": (observium_device or {}).get("version")} if observium_device else None}, "manual_fields": {"asset_tag": compact_text(device.get("asset_tag")), "serial": compact_text(device.get("serial"))}, "suggestions": suggestions}


async def apply(device_id: int, body: Any) -> dict[str, Any]:
    from app.services.netbox_service import ensure_location, ensure_platform, ensure_primary_ip4, request
    from app.services.netbox_sync_service import correlated_sources_for_device
    from app.services.zabbix_service import ensure_serial_item

    if not body.actions:
        raise HTTPException(400, "No enrichment actions were provided")
    device_result = await request("GET", f"/dcim/devices/{device_id}/")
    device = device_result.get("result") or {}
    sources = await correlated_sources_for_device(device_id)
    zabbix_host = sources["zabbix"]
    results: list[dict[str, Any]] = []
    for action in body.actions:
        action_type = action.action_type
        value = compact_text(action.value)
        try:
            if action_type == "set_platform":
                ensured = await ensure_platform(value)
                platform = ensured["platform"]
                updated = await request("PATCH", f"/dcim/devices/{device_id}/", {"platform": int(platform["id"])})
                results.append({"action_type": action_type, "status": "ok", "created": ensured["created"], "platform": platform, "device": updated.get("result")})
            elif action_type == "set_location":
                ensured = await ensure_location(value, (device.get("site") or {}).get("id"))
                location = ensured["location"]
                updated = await request("PATCH", f"/dcim/devices/{device_id}/", {"location": int(location["id"])})
                results.append({"action_type": action_type, "status": "ok", "created": ensured["created"], "location": location, "device": updated.get("result")})
            elif action_type == "set_primary_ip4":
                results.append({"action_type": action_type, "status": "ok", **(await ensure_primary_ip4(device, value))})
            elif action_type == "migrate_description_ip":
                applied = await ensure_primary_ip4(device, value or compact_text(device.get("description")))
                cleared = await request("PATCH", f"/dcim/devices/{device_id}/", {"description": ""})
                results.append({"action_type": action_type, "status": "ok", **applied, "description_cleared": True, "device": cleared.get("result")})
            elif action_type == "set_comments":
                updated = await request("PATCH", f"/dcim/devices/{device_id}/", {"comments": action.value or ""})
                results.append({"action_type": action_type, "status": "ok", "device": updated.get("result")})
            elif action_type == "set_description":
                updated = await request("PATCH", f"/dcim/devices/{device_id}/", {"description": action.value or ""})
                results.append({"action_type": action_type, "status": "ok", "device": updated.get("result")})
            elif action_type == "set_serial":
                updated = await request("PATCH", f"/dcim/devices/{device_id}/", {"serial": action.value or ""})
                results.append({"action_type": action_type, "status": "ok", "device": updated.get("result")})
            elif action_type == "set_asset_tag":
                updated = await request("PATCH", f"/dcim/devices/{device_id}/", {"asset_tag": action.value or ""})
                results.append({"action_type": action_type, "status": "ok", "device": updated.get("result")})
            elif action_type == "ensure_zabbix_serial_item":
                if not zabbix_host:
                    raise HTTPException(400, "No correlated Zabbix host is available")
                results.append({"action_type": action_type, "status": "ok", **(await ensure_serial_item(str(zabbix_host['hostid'])))})
            else:
                raise HTTPException(400, f"Unsupported enrichment action: {action_type}")
        except HTTPException as exc:
            results.append({"action_type": action_type, "status": "error", "error": exc.detail})
    refreshed = await build(device_id)
    return {"status": "ok", "results": results, "result": refreshed}


async def fix_primary_ip4_correlated(body: Any) -> dict[str, Any]:
    from app.services.netbox_service import ensure_primary_ip4, request
    from app.services.zabbix_service import find_host

    results: list[dict[str, Any]] = []
    for group in list_correlations():
        items = group.get("items") or {}
        nb_item = items.get("netbox")
        zb_item = items.get("zabbix")
        if not nb_item or not zb_item or archive_status("netbox", nb_item["id"]):
            continue
        try:
            device_result = await request("GET", f"/dcim/devices/{int(nb_item['id'])}/")
            device = device_result.get("result") or {}
            host = await find_host(zb_item["id"])
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
            results.append({"device_id": nb_item["id"], "device": device.get("name"), "status": "updated", **(await ensure_primary_ip4(device, candidate_ip))})
        except HTTPException as exc:
            results.append({"device_id": nb_item["id"], "device": nb_item.get("label"), "status": "error", "error": exc.detail})
    return {"status": "ok", "count": len(results), "result": results}
