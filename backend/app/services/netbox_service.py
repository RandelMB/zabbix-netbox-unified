from __future__ import annotations

import ipaddress
from typing import Any

from fastapi import HTTPException

from app.core.settings import cfg
from app.integrations.netbox_api import NetBoxApiClient
from app.repositories.archive_repository import archive_status
from app.services.archive_service import apply_archive_mode
from app.services.settings_service import get_netbox_token, get_netbox_url
from app.services.topology_export_service import fetch_all_netbox_devices as fetch_all_netbox_devices_service
from app.shared.networking import host_part, normalize_ip_value, parse_mac_address
from app.shared.text import as_int, compact_text, normalize_key, slugify_text
from app.shared.vlan import vlan_representation_tokens, vlan_tag_name_and_slug


def _client() -> NetBoxApiClient:
    return NetBoxApiClient(
        base_url=get_netbox_url(),
        token=get_netbox_token(),
        tls_verify=cfg.NETBOX_TLS_VERIFY,
    )


async def request(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    return await _client().request(method, path, body)


async def paginated(path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    query = dict(params or {})
    query.setdefault("limit", 500)
    result = await request("GET", path, query)
    payload = result.get("result")
    if isinstance(payload, dict) and "results" in payload:
        return payload.get("results", [])
    if isinstance(payload, list):
        return payload
    return []


async def fetch_all_devices(archived: str = "exclude") -> list[dict[str, Any]]:
    return await fetch_all_netbox_devices_service(_client(), archived=archived)


async def find_platform(name: str) -> dict[str, Any] | None:
    target = normalize_key(name)
    for item in await paginated("/dcim/platforms/"):
        if normalize_key(item.get("name")) == target or normalize_key(item.get("display")) == target:
            return item
    return None


async def ensure_platform(name: str) -> dict[str, Any]:
    existing = await find_platform(name)
    if existing:
        return {"created": False, "platform": existing}
    created = await request("POST", "/dcim/platforms/", {"name": name, "slug": slugify_text(name)[:100]})
    return {"created": True, "platform": created.get("result") or created.get("response") or {}}


async def find_manufacturer(name: str) -> dict[str, Any] | None:
    target = normalize_key(name)
    if not target:
        return None
    for item in await paginated("/dcim/manufacturers/", {"limit": 500}):
        if normalize_key(item.get("name")) == target or normalize_key(item.get("display")) == target:
            return item
    return None


async def ensure_manufacturer(name: str) -> dict[str, Any]:
    existing = await find_manufacturer(name)
    if existing:
        return {"created": False, "manufacturer": existing}
    created = await request("POST", "/dcim/manufacturers/", {"name": name, "slug": slugify_text(name)[:100]})
    return {"created": True, "manufacturer": created.get("result") or created.get("response") or {}}


async def find_location(name: str, site_id: int | None) -> dict[str, Any] | None:
    params: dict[str, Any] = {}
    if site_id:
        params["site_id"] = site_id
    target = normalize_key(name)
    for item in await paginated("/dcim/locations/", params):
        if normalize_key(item.get("name")) == target or normalize_key(item.get("display")) == target:
            return item
    return None


async def ensure_location(name: str, site_id: int | None) -> dict[str, Any]:
    existing = await find_location(name, site_id)
    if existing:
        return {"created": False, "location": existing}
    if not site_id:
        raise HTTPException(400, "NetBox site is required to create a location")
    created = await request("POST", "/dcim/locations/", {"name": name, "slug": slugify_text(name)[:90], "site": int(site_id)})
    return {"created": True, "location": created.get("result") or created.get("response") or {}}


async def find_device_type_by_model(model: str) -> dict[str, Any] | None:
    target = normalize_key(model)
    if not target:
        return None
    for item in await paginated("/dcim/device-types/", {"limit": 500}):
        if normalize_key(item.get("model")) == target or normalize_key(item.get("display")) == target:
            return item
    return None


async def ensure_device_type(model: str, manufacturer_name: str) -> dict[str, Any]:
    existing = await find_device_type_by_model(model)
    if existing:
        return {"created": False, "device_type": existing}
    manufacturer_result = await ensure_manufacturer(manufacturer_name or "Unknown Vendor")
    manufacturer = manufacturer_result.get("manufacturer") or {}
    if not manufacturer.get("id"):
        raise HTTPException(400, "Manufacturer could not be created in NetBox")
    created = await request(
        "POST",
        "/dcim/device-types/",
        {"manufacturer": int(manufacturer["id"]), "model": model, "slug": slugify_text(model)[:100]},
    )
    return {
        "created": True,
        "manufacturer_created": manufacturer_result.get("created", False),
        "device_type": created.get("result") or created.get("response") or {},
        "manufacturer": manufacturer,
    }


async def choose_primary_interface(device_id: int) -> dict[str, Any]:
    interfaces = await paginated("/dcim/interfaces/", {"device_id": device_id})
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
    created = await request("POST", "/dcim/interfaces/", {"device": int(device_id), "name": "mgmt0", "type": "virtual", "enabled": True})
    return created.get("result") or created.get("response") or {}


async def choose_management_interface(device_id: int, preferred_name: str | None = None) -> dict[str, Any]:
    interfaces = await paginated("/dcim/interfaces/", {"device_id": device_id, "limit": 500})
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
    created = await request("POST", "/dcim/interfaces/", {"device": int(device_id), "name": created_name, "type": "virtual", "enabled": True})
    return created.get("result") or created.get("response") or {}


async def ip_assigned_device(ip_payload: dict[str, Any]) -> int | None:
    assigned_object = ip_payload.get("assigned_object") or {}
    interface_url = assigned_object.get("url")
    if interface_url:
        iface_result = await request("GET", interface_url.replace(f"{get_netbox_url()}/api", ""))
        iface = iface_result.get("result") or {}
        device = iface.get("device") or {}
        if device.get("id") is not None:
            return int(device["id"])
    return None


async def set_primary_ip(device_id: int, body: dict[str, Any]) -> dict[str, Any]:
    ip_id = body.get("ip_id")
    if ip_id in {None, ""}:
        raise HTTPException(400, "ip_id is required")
    ip_result = await request("GET", f"/ipam/ip-addresses/{int(ip_id)}/")
    ip_payload = ip_result.get("result") or {}
    address = str(ip_payload.get("address") or "").strip()
    if not address:
        raise HTTPException(400, "NetBox IP payload is missing address")
    try:
        ip_version = ipaddress.ip_interface(address).version
    except ValueError as exc:
        raise HTTPException(400, f"Invalid NetBox IP address: {address}") from exc
    primary_field = "primary_ip4" if ip_version == 4 else "primary_ip6"
    return await request("PATCH", f"/dcim/devices/{device_id}/", {primary_field: int(ip_id)})


async def ensure_primary_ip4(
    device: dict[str, Any],
    address: str,
    status: str = "active",
    preferred_interface_name: str | None = None,
) -> dict[str, Any]:
    normalized = normalize_ip_value(address)
    if not normalized:
        raise HTTPException(400, "IPv4 candidate is empty")
    chosen_interface = await choose_management_interface(int(device["id"]), preferred_interface_name) if preferred_interface_name else await choose_primary_interface(int(device["id"]))
    search_results = await paginated("/ipam/ip-addresses/", {"address": host_part(normalized), "limit": 50})
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
        await request(
            "PATCH",
            f"/ipam/ip-addresses/{int(reusable['id'])}/",
            {"address": normalized, "status": status, "assigned_object_type": "dcim.interface", "assigned_object_id": int(chosen_interface["id"])},
        )
        ip_id = int(reusable["id"])
        created = False
    else:
        created_result = await request(
            "POST",
            "/ipam/ip-addresses/",
            {"address": normalized, "status": status, "assigned_object_type": "dcim.interface", "assigned_object_id": int(chosen_interface["id"])},
        )
        created_ip = created_result.get("result") or created_result.get("response") or {}
        ip_id = int(created_ip["id"])
        created = True
    await set_primary_ip(int(device["id"]), {"ip_id": ip_id})
    return {"ip_id": ip_id, "address": normalized, "created": created, "interface": chosen_interface}


async def ensure_tag(name: str, slug: str) -> dict[str, Any]:
    existing = await paginated("/extras/tags/", {"slug": slug})
    if existing:
        return {"created": False, "tag": existing[0]}
    created = await request("POST", "/extras/tags/", {"name": name, "slug": slug, "color": "607d8b"})
    return {"created": True, "tag": created.get("result") or created.get("response") or {}}


async def ensure_vlan_tags(vlan_values: list[Any]) -> list[int]:
    ensured_tag_ids: list[int] = []
    for token in vlan_representation_tokens(vlan_values):
        name, slug = vlan_tag_name_and_slug(token)
        ensured = await ensure_tag(name, slug)
        tag = ensured.get("tag") or {}
        if tag.get("id") is not None:
            ensured_tag_ids.append(int(tag["id"]))
    return ensured_tag_ids


async def ensure_interface_mac(interface_id: int, address: str) -> dict[str, Any]:
    normalized = parse_mac_address(address)
    if not normalized:
        raise HTTPException(400, "MAC candidate is empty")
    existing = await paginated("/dcim/mac-addresses/", {"assigned_object_type": "dcim.interface", "assigned_object_id": interface_id, "limit": 100})
    current = next((item for item in existing if parse_mac_address(item.get("mac_address")) == normalized), None)
    created = False
    if current is None:
        created_result = await request("POST", "/dcim/mac-addresses/", {"mac_address": normalized, "assigned_object_type": "dcim.interface", "assigned_object_id": interface_id})
        current = created_result.get("result") or created_result.get("response") or {}
        created = True
    if current.get("id") is not None:
        await request("PATCH", f"/dcim/interfaces/{interface_id}/", {"primary_mac_address": int(current["id"])})
    return {"created": created, "mac": current}


async def interface_connected_peer(interface_id: int) -> dict[str, Any] | None:
    detail_result = await request("GET", f"/dcim/interfaces/{interface_id}/")
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


async def ensure_interface_cable(interface_a_id: int, interface_b_id: int) -> dict[str, Any]:
    if interface_a_id == interface_b_id:
        raise HTTPException(400, "Cannot cable an interface to itself")
    peer_a = await interface_connected_peer(interface_a_id)
    peer_b = await interface_connected_peer(interface_b_id)
    if peer_a and as_int(peer_a.get("id")) == int(interface_b_id) and peer_a.get("cable"):
        return {"created": False, "cable": peer_a["cable"]}
    if peer_a and as_int(peer_a.get("id")) not in {None, int(interface_b_id)}:
        raise HTTPException(409, f"Interface {interface_a_id} is already connected to another endpoint")
    if peer_b and as_int(peer_b.get("id")) not in {None, int(interface_a_id)}:
        raise HTTPException(409, f"Interface {interface_b_id} is already connected to another endpoint")
    created = await request(
        "POST",
        "/dcim/cables/",
        {"a_terminations": [{"object_type": "dcim.interface", "object_id": int(interface_a_id)}], "b_terminations": [{"object_type": "dcim.interface", "object_id": int(interface_b_id)}], "status": "connected"},
    )
    return {"created": True, "cable": created.get("result") or created.get("response") or {}}


async def clear_interface_dependencies(device_id: int, interfaces: list[dict[str, Any]]) -> dict[str, Any]:
    interface_ids = {int(item["id"]) for item in interfaces if item.get("id") is not None}
    if not interface_ids:
        return {"interfaces_deleted": 0, "ips_unassigned": 0, "cables_deleted": 0, "macs_deleted": 0}
    device_result = await request("GET", f"/dcim/devices/{device_id}/")
    device = device_result.get("result") or device_result.get("response") or {}
    primary_clear = {}
    for field in ("primary_ip4", "primary_ip6"):
        primary_ip = device.get(field) or {}
        if as_int(primary_ip.get("id")) is not None:
            primary_clear[field] = None
    if primary_clear:
        await request("PATCH", f"/dcim/devices/{device_id}/", primary_clear)
    ips = await paginated("/ipam/ip-addresses/", {"device_id": device_id, "limit": 500})
    ips_unassigned = 0
    for ip_item in ips:
        assigned_object = ip_item.get("assigned_object") or {}
        if as_int(assigned_object.get("id")) not in interface_ids:
            continue
        await request("PATCH", f"/ipam/ip-addresses/{int(ip_item['id'])}/", {"assigned_object_type": None, "assigned_object_id": None})
        ips_unassigned += 1
    cables_deleted = 0
    macs_deleted = 0
    interfaces_deleted = 0
    for interface in interfaces:
        iface_id = int(interface["id"])
        detail_result = await request("GET", f"/dcim/interfaces/{iface_id}/")
        detail = detail_result.get("result") or detail_result.get("response") or {}
        cable = detail.get("cable") or {}
        cable_id = as_int(cable.get("id"))
        if cable_id:
            await request("DELETE", f"/dcim/cables/{cable_id}/")
            cables_deleted += 1
        macs = await paginated("/dcim/mac-addresses/", {"assigned_object_type": "dcim.interface", "assigned_object_id": iface_id, "limit": 100})
        for mac in macs:
            if mac.get("id") is None:
                continue
            await request("DELETE", f"/dcim/mac-addresses/{int(mac['id'])}/")
            macs_deleted += 1
        await request("DELETE", f"/dcim/interfaces/{iface_id}/")
        interfaces_deleted += 1
    return {"interfaces_deleted": interfaces_deleted, "ips_unassigned": ips_unassigned, "cables_deleted": cables_deleted, "macs_deleted": macs_deleted}


async def find_device_by_mac(mac_address: str) -> dict[str, Any] | None:
    normalized = parse_mac_address(mac_address)
    if not normalized:
        return None
    mac_rows = await paginated("/dcim/mac-addresses/", {"mac_address": normalized, "limit": 50})
    for row in mac_rows:
        assigned = row.get("assigned_object") or {}
        device = assigned.get("device") or {}
        if device.get("id") is not None:
            device_result = await request("GET", f"/dcim/devices/{int(device['id'])}/")
            return device_result.get("result") or None
    return None


async def find_device_by_identity(name: str, ip_value: str = "", mac_address: str = "") -> dict[str, Any] | None:
    target_name = normalize_key(name)
    target_ip = host_part(ip_value) if ip_value else ""
    if mac_address:
        by_mac = await find_device_by_mac(mac_address)
        if by_mac:
            return by_mac
    for item in await fetch_all_devices(archived="all"):
        if target_name and normalize_key(item.get("name")) == target_name:
            return item
        item_ip = host_part((item.get("primary_ip4") or {}).get("address") or "")
        if target_ip and item_ip == target_ip:
            return item
    return None


async def list_devices_route(limit: int = 500, offset: int = 0, archived: str = "exclude") -> dict[str, Any]:
    result = await request("GET", "/dcim/devices/", {"limit": limit, "offset": offset})
    items = result["result"].get("results", [])
    result["result"]["results"] = apply_archive_mode("netbox", items, "id", archived)
    return result


async def get_device(device_id: int) -> dict[str, Any]:
    result = await request("GET", f"/dcim/devices/{device_id}/")
    if result.get("result"):
        result["result"]["archived"] = archive_status("netbox", str(device_id))
    return result


async def create_device(body: dict[str, Any]) -> dict[str, Any]:
    return await request("POST", "/dcim/devices/", body)


async def update_device(device_id: int, body: dict[str, Any]) -> dict[str, Any]:
    return await request("PATCH", f"/dcim/devices/{device_id}/", body)


async def device_interfaces(device_id: int) -> dict[str, Any]:
    return await request("GET", "/dcim/interfaces/", {"device_id": device_id, "limit": 200})


async def get_interface(interface_id: int) -> dict[str, Any]:
    return await request("GET", f"/dcim/interfaces/{interface_id}/")


async def create_interface(body: dict[str, Any]) -> dict[str, Any]:
    return await request("POST", "/dcim/interfaces/", body)


async def update_interface(interface_id: int, body: dict[str, Any]) -> dict[str, Any]:
    payload = dict(body)
    mac_address = parse_mac_address(payload.pop("mac_address", ""))
    result = await request("PATCH", f"/dcim/interfaces/{interface_id}/", payload) if payload else None
    if mac_address:
        mac_result = await ensure_interface_mac(interface_id, mac_address)
        detail = await request("GET", f"/dcim/interfaces/{interface_id}/")
        return {"status": "ok", "request": result.get("request") if result else None, "response": detail.get("response") if detail else None, "result": detail.get("result") or detail.get("response") or {}, "mac_result": mac_result}
    if result is not None:
        return result
    detail = await request("GET", f"/dcim/interfaces/{interface_id}/")
    return {"status": "ok", "result": detail.get("result") or detail.get("response") or {}}


async def delete_interface(interface_id: int) -> dict[str, Any]:
    return await request("DELETE", f"/dcim/interfaces/{interface_id}/")


async def list_ips(device_id: int | None = None, address: str | None = None, limit: int = 200) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": limit}
    if device_id:
        params["device_id"] = device_id
    if address:
        params["address"] = address
    return await request("GET", "/ipam/ip-addresses/", params)


async def create_ip(body: dict[str, Any]) -> dict[str, Any]:
    return await request("POST", "/ipam/ip-addresses/", body)


async def update_ip(ip_id: int, body: dict[str, Any]) -> dict[str, Any]:
    return await request("PATCH", f"/ipam/ip-addresses/{ip_id}/", body)


async def delete_ip(ip_id: int) -> dict[str, Any]:
    return await request("DELETE", f"/ipam/ip-addresses/{ip_id}/")


async def device_types() -> dict[str, Any]:
    return await request("GET", "/dcim/device-types/", {"limit": 200})


async def platforms() -> dict[str, Any]:
    return await request("GET", "/dcim/platforms/", {"limit": 500})


async def create_platform(body: dict[str, Any]) -> dict[str, Any]:
    return await request("POST", "/dcim/platforms/", body)


async def sites() -> dict[str, Any]:
    return await request("GET", "/dcim/sites/", {"limit": 200})


async def locations(site_id_value: int | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": 500}
    if site_id_value:
        params["site_id"] = site_id_value
    return await request("GET", "/dcim/locations/", params)


async def create_location(body: dict[str, Any]) -> dict[str, Any]:
    return await request("POST", "/dcim/locations/", body)


async def device_roles() -> dict[str, Any]:
    return await request("GET", "/dcim/device-roles/", {"limit": 200})
