from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from fastapi import HTTPException

from app.schemas.interface_sync import NetBoxInterfaceSyncRunPayload
from app.shared.text import as_int, compact_text, normalize_key
from app.shared.vlan import interface_vlan_representation, merge_interface_vlan_tags, vlan_representation_tokens


@dataclass
class InterfaceSyncRuntime:
    clear_netbox_interface_dependencies: Callable[[int, list[dict[str, Any]]], Awaitable[dict[str, Any]]]
    correlated_sources_for_netbox_device: Callable[[int], Awaitable[dict[str, Any]]]
    description_ip_candidate: Callable[[dict[str, Any]], Optional[str]]
    ensure_netbox_interface_cable: Callable[[int, int], Awaitable[dict[str, Any]]]
    ensure_netbox_interface_mac: Callable[[int, str], Awaitable[dict[str, Any]]]
    ensure_netbox_primary_ip4: Callable[..., Awaitable[dict[str, Any]]]
    ensure_vlan_tags: Callable[[list[Any]], Awaitable[list[int]]]
    extract_main_zabbix_ip: Callable[[dict[str, Any]], Optional[str]]
    extract_observium_ipv4: Callable[[dict[str, Any]], Optional[str]]
    first_non_empty: Callable[..., Any]
    log_export: Callable[..., None]
    netbox_paginated: Callable[[str, Optional[dict[str, Any]]], Awaitable[list[dict[str, Any]]]]
    netbox_request: Callable[[str, str, Optional[dict[str, Any]]], Awaitable[Any]]
    observium_link_rows: Callable[[int], list[dict[str, Any]]]
    observium_port_rows: Callable[[int], list[dict[str, Any]]]
    resolve_interface_connection_candidate: Callable[..., Awaitable[Optional[dict[str, Any]]]]


def interface_match_keys(name: str) -> list[str]:
    keys: list[str] = []
    for candidate in (name, name.replace(" ", ""), name.replace("-", ""), name.replace("_", "")):
        normalized = normalize_key(candidate)
        if normalized and normalized not in keys:
            keys.append(normalized)
    return keys


def netbox_interface_summary(interface: dict[str, Any], parse_mac_address: Callable[[Any], str]) -> dict[str, Any]:
    primary_mac = interface.get("primary_mac_address") or {}
    lag = interface.get("lag") or {}
    return {
        "id": int(interface["id"]),
        "name": compact_text(interface.get("name")),
        "type": compact_text((interface.get("type") or {}).get("value") or interface.get("type")),
        "enabled": bool(interface.get("enabled", True)),
        "description": compact_text(interface.get("description")),
        "mac_address": parse_mac_address(interface.get("mac_address") or primary_mac.get("mac_address")),
        "mtu": as_int(interface.get("mtu")),
        "tags": interface.get("tags") or [],
        "lag_id": as_int(lag.get("id")),
        "lag_name": compact_text(lag.get("name")),
    }


def build_interface_sync_actions(
    netbox_interface: dict[str, Any],
    observium_port: dict[str, Any],
    parse_mac_address: Callable[[Any], str],
    lag_netbox_name: str = "",
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    if compact_text(netbox_interface.get("name")) != compact_text(observium_port.get("name")):
        actions.append({"field": "name", "current": netbox_interface.get("name"), "proposed": observium_port.get("name")})

    proposed_description = compact_text(observium_port.get("description"))
    if proposed_description and proposed_description != compact_text(netbox_interface.get("description")):
        actions.append({"field": "description", "current": netbox_interface.get("description"), "proposed": proposed_description})

    proposed_mac = parse_mac_address(observium_port.get("mac_address"))
    if proposed_mac and not observium_port.get("mac_sync_skipped") and proposed_mac != parse_mac_address(netbox_interface.get("mac_address")):
        actions.append({"field": "mac_address", "current": netbox_interface.get("mac_address"), "proposed": proposed_mac})

    if observium_port.get("enabled_candidate") is not None and bool(netbox_interface.get("enabled", True)) != bool(observium_port.get("enabled_candidate")):
        actions.append({"field": "enabled", "current": bool(netbox_interface.get("enabled", True)), "proposed": bool(observium_port.get("enabled_candidate"))})

    proposed_type = compact_text(observium_port.get("type"))
    if proposed_type and proposed_type != compact_text(netbox_interface.get("type")):
        actions.append({"field": "type", "current": netbox_interface.get("type"), "proposed": proposed_type})

    proposed_mtu = as_int(observium_port.get("mtu"))
    if proposed_mtu and proposed_mtu != as_int(netbox_interface.get("mtu")):
        actions.append({"field": "mtu", "current": netbox_interface.get("mtu"), "proposed": proposed_mtu})

    proposed_vlans = vlan_representation_tokens(observium_port.get("vlans") or [])
    if proposed_vlans:
        current_vlans = interface_vlan_representation(netbox_interface.get("tags") or [])
        if proposed_vlans != current_vlans:
            actions.append({"field": "vlan_tags", "current": current_vlans, "proposed": proposed_vlans})

    if lag_netbox_name and compact_text(netbox_interface.get("lag_name")) != compact_text(lag_netbox_name):
        actions.append({"field": "lag", "current": netbox_interface.get("lag_name"), "proposed": lag_netbox_name})

    return actions


def match_observium_ports_to_netbox(
    netbox_interfaces: list[dict[str, Any]],
    observium_ports: list[dict[str, Any]],
    parse_mac_address: Callable[[Any], str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_name: dict[str, list[dict[str, Any]]] = {}
    by_mac: dict[str, list[dict[str, Any]]] = {}
    for interface in netbox_interfaces:
        for key in interface_match_keys(compact_text(interface.get("name"))):
            by_name.setdefault(key, []).append(interface)
        mac = parse_mac_address(interface.get("mac_address"))
        if mac:
            by_mac.setdefault(mac, []).append(interface)

    used_ids: set[int] = set()
    matches: list[dict[str, Any]] = []

    for port in observium_ports:
        matched = None
        match_method = ""
        for candidate in port.get("name_candidates") or [port.get("name")]:
            candidates = [
                item
                for key in interface_match_keys(candidate)
                for item in by_name.get(key, [])
                if int(item["id"]) not in used_ids
            ]
            unique = {int(item["id"]): item for item in candidates}
            if len(unique) == 1:
                matched = next(iter(unique.values()))
                match_method = "name"
                break
        if matched is None and port.get("mac_address"):
            candidates = [item for item in by_mac.get(parse_mac_address(port["mac_address"]), []) if int(item["id"]) not in used_ids]
            if len(candidates) == 1:
                matched = candidates[0]
                match_method = "mac"

        if matched is None:
            matches.append(
                {
                    "status": "unmatched_observium",
                    "match_method": None,
                    "netbox": None,
                    "observium": port,
                    "actions": [{"field": "create_interface", "current": "", "proposed": port.get("name")}],
                }
            )
            continue

        used_ids.add(int(matched["id"]))
        matches.append(
            {
                "status": "matched",
                "match_method": match_method,
                "netbox": matched,
                "observium": port,
                "actions": build_interface_sync_actions(matched, port, parse_mac_address),
            }
        )

    for interface in netbox_interfaces:
        if int(interface["id"]) in used_ids:
            continue
        matches.append(
            {
                "status": "unmatched_netbox",
                "match_method": None,
                "netbox": interface,
                "observium": None,
                "actions": [],
            }
        )

    return matches, [item for item in matches if item["status"] == "matched"]


def pick_management_ip_candidate(
    device: dict[str, Any],
    sources: dict[str, Any],
    *,
    description_ip_candidate: Callable[[dict[str, Any]], Optional[str]],
    extract_main_zabbix_ip: Callable[[dict[str, Any]], Optional[str]],
    extract_observium_ipv4: Callable[[dict[str, Any]], Optional[str]],
) -> str:
    current_primary = compact_text(((device.get("primary_ip4") or {}).get("address")) or "")
    if current_primary:
        return current_primary
    description_ip = description_ip_candidate(device)
    if description_ip:
        return description_ip
    zabbix_ip = extract_main_zabbix_ip(sources.get("zabbix") or {})
    if zabbix_ip:
        return zabbix_ip
    return extract_observium_ipv4(sources.get("observium") or {}) or ""


async def build_preview(
    runtime: InterfaceSyncRuntime,
    device_id: int,
    parse_mac_address: Callable[[Any], str],
) -> dict[str, Any]:
    device_result = await runtime.netbox_request("GET", f"/dcim/devices/{device_id}/")
    device = device_result.get("result") or {}
    sources = await runtime.correlated_sources_for_netbox_device(device_id)
    observium_device = sources["observium"]
    if not observium_device:
        raise HTTPException(404, "No Observium device is linked to this NetBox device")

    netbox_interfaces = [
        netbox_interface_summary(item, parse_mac_address)
        for item in await runtime.netbox_paginated("/dcim/interfaces/", {"device_id": device_id, "limit": 500})
    ]
    observium_ports = runtime.observium_port_rows(int(observium_device["device_id"]))
    matches, matched = match_observium_ports_to_netbox(netbox_interfaces, observium_ports, parse_mac_address)
    link_rows = runtime.observium_link_rows(int(observium_device["device_id"]))
    link_rows_by_port_id: dict[int, list[dict[str, Any]]] = {}
    for row in link_rows:
        local_port_id = as_int(runtime.first_non_empty(row, "local_port_id", "port_id", "port_id_local")) or 0
        if local_port_id:
            link_rows_by_port_id.setdefault(local_port_id, []).append(row)
    observium_by_port_id = {int(item.get("port_id")): item for item in observium_ports if item.get("port_id")}
    match_by_port_id = {
        int(item["observium"]["port_id"]): item
        for item in matches
        if item.get("observium") and item["status"] == "matched"
    }

    for item in matches:
        observium_port = item.get("observium") or {}
        if not observium_port:
            continue
        lag_parent_name = ""
        parent_id = as_int(observium_port.get("lag_parent_port_id"))
        if parent_id and parent_id in match_by_port_id:
            lag_parent_name = compact_text((match_by_port_id[parent_id].get("netbox") or {}).get("name"))
        elif parent_id and parent_id in observium_by_port_id:
            lag_parent_name = compact_text(observium_by_port_id[parent_id].get("name"))
        item["lag_parent_name"] = lag_parent_name
        item["lag_member_names"] = observium_port.get("lag_member_names") or []
        if item["status"] == "matched":
            item["actions"] = build_interface_sync_actions(item["netbox"] or {}, observium_port, parse_mac_address, lag_parent_name)
        elif item["status"] == "unmatched_observium":
            proposed_type = observium_port.get("type") or "other"
            item["create_payload"] = {
                "device": int(device_id),
                "name": observium_port.get("name") or f"port-{observium_port.get('port_id')}",
                "type": proposed_type,
                "enabled": bool(observium_port.get("enabled_candidate")) if observium_port.get("enabled_candidate") is not None else True,
                "description": observium_port.get("description") or "",
                "mtu": as_int(observium_port.get("mtu")),
                "lag_name": lag_parent_name,
                "vlans": observium_port.get("vlans") or [],
            }

        connection = await runtime.resolve_interface_connection_candidate(
            local_interface=item.get("netbox"),
            local_observium_port=observium_port,
            link_rows=link_rows_by_port_id,
        )
        item["connection"] = connection
        if connection and item["status"] == "matched":
            remote_nb_iface = connection.get("remote_netbox_interface") or {}
            current_peer = connection.get("current_peer") or {}
            if remote_nb_iface.get("id") and as_int(current_peer.get("id")) != as_int(remote_nb_iface.get("id")):
                item["actions"].append(
                    {
                        "field": "connection",
                        "current": compact_text(" ".join(part for part in [current_peer.get("device_name"), current_peer.get("name")] if compact_text(part))),
                        "proposed": compact_text(
                            " ".join(
                                part
                                for part in [((connection.get("remote_netbox_device") or {}).get("name")), remote_nb_iface.get("name")]
                                if compact_text(part)
                            )
                        ),
                    }
                )

    ready = sum(1 for item in matched if item["actions"])
    up_to_date = sum(1 for item in matched if not item["actions"])
    unmatched_observium = sum(1 for item in matches if item["status"] == "unmatched_observium")
    unmatched_netbox = sum(1 for item in matches if item["status"] == "unmatched_netbox")

    return {
        "device": {"id": int(device["id"]), "name": device.get("name")},
        "correlation": sources["correlation"],
        "observium_device": {
            "device_id": str(observium_device.get("device_id")),
            "hostname": observium_device.get("hostname"),
            "sysName": observium_device.get("sysName"),
        },
        "summary": {
            "netbox_interfaces": len(netbox_interfaces),
            "observium_ports": len(observium_ports),
            "matched": len(matched),
            "ready": ready,
            "up_to_date": up_to_date,
            "unmatched_observium": unmatched_observium,
            "unmatched_netbox": unmatched_netbox,
            "creatable": unmatched_observium,
        },
        "interfaces": matches,
    }


async def apply_sync(
    runtime: InterfaceSyncRuntime,
    device_id: int,
    options: NetBoxInterfaceSyncRunPayload,
    parse_mac_address: Callable[[Any], str],
) -> dict[str, Any]:
    device_result = await runtime.netbox_request("GET", f"/dcim/devices/{device_id}/")
    device = device_result.get("result") or device_result.get("response") or {}
    sources = await runtime.correlated_sources_for_netbox_device(device_id)
    replacement_cleanup = None
    if options.replace_existing_interfaces:
        existing_interfaces = await runtime.netbox_paginated("/dcim/interfaces/", {"device_id": device_id, "limit": 500})
        replacement_cleanup = await runtime.clear_netbox_interface_dependencies(device_id, existing_interfaces)

    preview = await build_preview(runtime, device_id, parse_mac_address)
    results: list[dict[str, Any]] = []
    created_port_ids: set[int] = set()
    created_interfaces_by_name: dict[str, int] = {}
    create_missing_interfaces = options.create_missing_interfaces or options.replace_existing_interfaces

    create_items = [item for item in preview["interfaces"] if item["status"] == "unmatched_observium"]
    create_items.sort(key=lambda item: 0 if (item.get("observium", {}).get("lag_role") == "parent") else 1)

    for item in create_items:
        if not create_missing_interfaces:
            break
        create_payload = dict(item.get("create_payload") or {})
        if not create_payload:
            continue
        lag_name = compact_text(create_payload.pop("lag_name"))
        vlan_ids = [str(vlan) for vlan in create_payload.pop("vlans", []) if compact_text(vlan)]
        mtu = create_payload.get("mtu")
        if not mtu:
            create_payload.pop("mtu", None)
        try:
            created = await runtime.netbox_request("POST", "/dcim/interfaces/", create_payload)
            created_iface = created.get("result") or created.get("response") or {}
            iface_id = int(created_iface["id"])
            created_interfaces_by_name[normalize_key(create_payload.get("name"))] = iface_id
            patch_payload: dict[str, Any] = {}
            if options.sync_vlan_tags and vlan_ids:
                ensured_tag_ids = await runtime.ensure_vlan_tags(vlan_ids)
                patch_payload["tags"] = merge_interface_vlan_tags(created_iface.get("tags") or [], ensured_tag_ids)
            if options.sync_mac_addresses and item.get("observium", {}).get("mac_address"):
                await runtime.ensure_netbox_interface_mac(iface_id, item["observium"]["mac_address"])
            if options.sync_lag_members and lag_name:
                interfaces = await runtime.netbox_paginated("/dcim/interfaces/", {"device_id": device_id, "limit": 500})
                parent = next((iface for iface in interfaces if normalize_key(iface.get("name")) == normalize_key(lag_name)), None)
                if parent is None and created_interfaces_by_name.get(normalize_key(lag_name)):
                    parent = {"id": created_interfaces_by_name[normalize_key(lag_name)]}
                if parent:
                    patch_payload["lag"] = int(parent["id"])
            if patch_payload:
                await runtime.netbox_request("PATCH", f"/dcim/interfaces/{iface_id}/", patch_payload)
            connection = item.get("connection") or {}
            remote_interface = (connection.get("remote_netbox_interface") or {}) if isinstance(connection, dict) else {}
            if options.sync_connections and remote_interface.get("id"):
                try:
                    await runtime.ensure_netbox_interface_cable(iface_id, int(remote_interface["id"]))
                except HTTPException as exc:
                    results.append({"status": "error", "name": create_payload.get("name"), "payload": create_payload, "error": exc.detail, "phase": "cable"})
            if item.get("observium", {}).get("port_id"):
                created_port_ids.add(int(item["observium"]["port_id"]))
            results.append({"status": "created", "name": create_payload.get("name"), "netbox_interface_id": iface_id, "payload": create_payload})
        except HTTPException as exc:
            results.append({"status": "error", "name": create_payload.get("name"), "payload": create_payload, "error": exc.detail})

    for item in preview["interfaces"]:
        if item["status"] != "matched":
            if item["status"] == "unmatched_observium" and item.get("observium", {}).get("port_id") and int(item["observium"]["port_id"]) in created_port_ids:
                continue
            results.append({"status": "skipped", "reason": item["status"], "netbox": item.get("netbox"), "observium": item.get("observium")})
            continue
        if not item["actions"]:
            results.append({"status": "skipped", "reason": "up_to_date", "netbox": item.get("netbox"), "observium": item.get("observium")})
            continue

        netbox_interface = item["netbox"] or {}
        observium_port = item["observium"] or {}
        payload: dict[str, Any] = {}
        if options.rename_interfaces and any(action["field"] == "name" for action in item["actions"]):
            payload["name"] = observium_port["name"]
        if options.sync_descriptions and any(action["field"] == "description" for action in item["actions"]):
            payload["description"] = observium_port["description"]
        if options.sync_enabled_state and any(action["field"] == "enabled" for action in item["actions"]):
            payload["enabled"] = bool(observium_port["enabled_candidate"])
        if options.sync_type and any(action["field"] == "type" for action in item["actions"]):
            payload["type"] = observium_port["type"]
        if options.sync_mtu and any(action["field"] == "mtu" for action in item["actions"]):
            payload["mtu"] = as_int(observium_port["mtu"])
        if options.sync_vlan_tags and any(action["field"] == "vlan_tags" for action in item["actions"]):
            ensured_tag_ids = await runtime.ensure_vlan_tags(observium_port.get("vlans") or [])
            payload["tags"] = merge_interface_vlan_tags(netbox_interface.get("tags") or [], ensured_tag_ids)
        if options.sync_lag_members and any(action["field"] == "lag" for action in item["actions"]):
            parent_name = compact_text(item.get("lag_parent_name"))
            if parent_name:
                interfaces = await runtime.netbox_paginated("/dcim/interfaces/", {"device_id": device_id, "limit": 500})
                parent = next((iface for iface in interfaces if normalize_key(iface.get("name")) == normalize_key(parent_name)), None)
                if parent:
                    payload["lag"] = int(parent["id"])

        if not payload:
            results.append({"status": "skipped", "reason": "disabled_by_options", "netbox": netbox_interface, "observium": observium_port})
            continue

        try:
            if payload:
                updated = await runtime.netbox_request("PATCH", f"/dcim/interfaces/{int(netbox_interface['id'])}/", payload)
                updated_iface = updated.get("result") or updated.get("response") or {}
            else:
                updated_iface = netbox_interface
            mac_result = None
            if options.sync_mac_addresses and any(action["field"] == "mac_address" for action in item["actions"]):
                mac_result = await runtime.ensure_netbox_interface_mac(int(netbox_interface["id"]), observium_port["mac_address"])
            cable_result = None
            if options.sync_connections and any(action["field"] == "connection" for action in item["actions"]):
                remote_interface = ((item.get("connection") or {}).get("remote_netbox_interface") or {})
                if remote_interface.get("id"):
                    cable_result = await runtime.ensure_netbox_interface_cable(int(netbox_interface["id"]), int(remote_interface["id"]))
            results.append(
                {
                    "status": "updated",
                    "match_method": item.get("match_method"),
                    "netbox_interface_id": int(netbox_interface["id"]),
                    "name": updated_iface.get("name") or netbox_interface.get("name"),
                    "payload": payload,
                    "mac_result": mac_result,
                    "cable_result": cable_result,
                }
            )
        except HTTPException as exc:
            results.append(
                {
                    "status": "error",
                    "match_method": item.get("match_method"),
                    "netbox_interface_id": int(netbox_interface["id"]),
                    "name": netbox_interface.get("name"),
                    "payload": payload,
                    "error": exc.detail,
                }
            )

    primary_ip_result = None
    if options.reassign_primary_ip:
        candidate_ip = pick_management_ip_candidate(
            device,
            sources,
            description_ip_candidate=runtime.description_ip_candidate,
            extract_main_zabbix_ip=runtime.extract_main_zabbix_ip,
            extract_observium_ipv4=runtime.extract_observium_ipv4,
        )
        if compact_text(candidate_ip):
            try:
                primary_ip_result = await runtime.ensure_netbox_primary_ip4(
                    device,
                    candidate_ip,
                    preferred_interface_name=options.management_interface_name or "VLAN 200",
                )
            except HTTPException as exc:
                results.append(
                    {
                        "status": "error",
                        "phase": "primary_ip",
                        "name": preview["device"]["name"],
                        "payload": {"address": candidate_ip, "management_interface_name": options.management_interface_name or "VLAN 200"},
                        "error": exc.detail,
                    }
                )
        else:
            results.append({"status": "skipped", "reason": "missing_primary_ip_candidate", "name": preview["device"]["name"]})

    success_count = sum(1 for item in results if item["status"] == "updated")
    created_count = sum(1 for item in results if item["status"] == "created")
    error_count = sum(1 for item in results if item["status"] == "error")
    runtime.log_export(
        action="netbox-interface-sync",
        source="observium",
        target="netbox",
        status="error" if error_count else "ok",
        entity_id=str(device_id),
        entity_label=preview["device"]["name"],
        message=f"updated={success_count} created={created_count} errors={error_count}",
        payload={"options": options.dict(), "preview": preview},
        response=results,
    )
    return {
        "preview": await build_preview(runtime, device_id, parse_mac_address),
        "results": results,
        "options": options.dict(),
        "cleanup": replacement_cleanup,
        "primary_ip": primary_ip_result,
    }
