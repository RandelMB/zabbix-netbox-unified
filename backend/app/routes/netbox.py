from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.schemas.interface_sync import NetBoxInterfaceSyncRunPayload
from app.schemas.snmp import NetBoxSnmpImportPayload, NetBoxSnmpProbePayload
from app.services import legacy_runtime
from app.services.sync_locks import interface_sync_lock


router = APIRouter()


@router.get("/api/netbox/devices")
async def netbox_devices(limit: int = 500, offset: int = 0, archived: str = "exclude"):
    return await legacy_runtime.netbox_devices(limit=limit, offset=offset, archived=archived)


@router.get("/api/netbox/devices/{device_id}")
async def netbox_device(device_id: int):
    return await legacy_runtime.netbox_device(device_id)


@router.post("/api/netbox/devices")
async def netbox_create_device(body: dict[str, Any]):
    return await legacy_runtime.netbox_create_device(body)


@router.patch("/api/netbox/devices/{device_id}")
async def netbox_update_device(device_id: int, body: dict[str, Any]):
    return await legacy_runtime.netbox_update_device(device_id, body)


@router.post("/api/netbox/devices/{device_id}/primary-ip")
async def netbox_set_primary_ip(device_id: int, body: dict[str, Any]):
    return await legacy_runtime.netbox_set_primary_ip(device_id, body)


@router.get("/api/netbox/devices/{device_id}/interfaces")
async def netbox_device_interfaces(device_id: int):
    return await legacy_runtime.netbox_device_interfaces(device_id)


@router.get("/api/netbox/interfaces/{iface_id}")
async def netbox_interface(iface_id: int):
    return await legacy_runtime.netbox_interface(iface_id)


@router.post("/api/netbox/interfaces")
async def netbox_create_interface(body: dict[str, Any]):
    return await legacy_runtime.netbox_create_interface(body)


@router.patch("/api/netbox/interfaces/{iface_id}")
async def netbox_update_interface(iface_id: int, body: dict[str, Any]):
    return await legacy_runtime.netbox_update_interface(iface_id, body)


@router.delete("/api/netbox/interfaces/{iface_id}")
async def netbox_delete_interface(iface_id: int):
    return await legacy_runtime.netbox_delete_interface(iface_id)


@router.get("/api/netbox/ips")
async def netbox_ips(device_id: int | None = None, address: str | None = None, limit: int = 200):
    return await legacy_runtime.netbox_ips(device_id=device_id, address=address, limit=limit)


@router.post("/api/netbox/ips")
async def netbox_create_ip(body: dict[str, Any]):
    return await legacy_runtime.netbox_create_ip(body)


@router.patch("/api/netbox/ips/{ip_id}")
async def netbox_update_ip(ip_id: int, body: dict[str, Any]):
    return await legacy_runtime.netbox_update_ip(ip_id, body)


@router.delete("/api/netbox/ips/{ip_id}")
async def netbox_delete_ip(ip_id: int):
    return await legacy_runtime.netbox_delete_ip(ip_id)


@router.get("/api/netbox/device-types")
async def netbox_device_types():
    return await legacy_runtime.netbox_device_types()


@router.get("/api/netbox/platforms")
async def netbox_platforms():
    return await legacy_runtime.netbox_platforms()


@router.post("/api/netbox/platforms")
async def netbox_create_platform(body: dict[str, Any]):
    return await legacy_runtime.netbox_create_platform(body)


@router.get("/api/netbox/sites")
async def netbox_sites():
    return await legacy_runtime.netbox_sites()


@router.get("/api/netbox/locations")
async def netbox_locations(site_id: int | None = None):
    return await legacy_runtime.netbox_locations(site_id=site_id)


@router.post("/api/netbox/locations")
async def netbox_create_location(body: dict[str, Any]):
    return await legacy_runtime.netbox_create_location(body)


@router.get("/api/netbox/roles")
async def netbox_device_roles():
    return await legacy_runtime.netbox_device_roles()


@router.get("/api/netbox/devices/{device_id}/sync")
async def netbox_device_sync(device_id: int):
    return await legacy_runtime.netbox_device_sync(device_id)


@router.put("/api/netbox/devices/{device_id}/sync")
async def netbox_update_sync_profile(device_id: int, body: legacy_runtime.NetBoxSyncProfilePayload):
    return await legacy_runtime.netbox_update_sync_profile(device_id, body)


@router.post("/api/netbox/devices/{device_id}/sync/run")
async def netbox_run_sync(device_id: int, body: legacy_runtime.NetBoxSyncProfilePayload):
    return await legacy_runtime.netbox_run_sync(device_id, body)


@router.get("/api/netbox/devices/{device_id}/interface-sync")
async def netbox_device_interface_sync(device_id: int):
    return await legacy_runtime.netbox_device_interface_sync(device_id)


@router.post("/api/netbox/devices/{device_id}/interface-sync/run")
async def netbox_run_interface_sync(device_id: int, body: NetBoxInterfaceSyncRunPayload):
    lock = interface_sync_lock(device_id)
    if lock.locked():
        raise HTTPException(409, "A NetBox interface sync is already running for this device")
    async with lock:
        return {"status": "ok", "result": await legacy_runtime.apply_netbox_interface_sync(device_id, body)}


@router.post("/api/netbox/snmp-discovery/preview")
async def netbox_snmp_discovery_preview(body: NetBoxSnmpProbePayload):
    return await legacy_runtime.netbox_snmp_discovery_preview(body)


@router.post("/api/netbox/snmp-discovery/import")
async def netbox_snmp_discovery_import(body: NetBoxSnmpImportPayload):
    return await legacy_runtime.netbox_snmp_discovery_import(body)


@router.get("/api/netbox/devices/{device_id}/enrichment")
async def netbox_device_enrichment(device_id: int):
    return await legacy_runtime.netbox_device_enrichment(device_id)


@router.post("/api/netbox/devices/{device_id}/enrichment/apply")
async def netbox_apply_enrichment(device_id: int, body: legacy_runtime.NetBoxEnrichmentApplyPayload):
    return await legacy_runtime.netbox_apply_enrichment(device_id, body)


@router.post("/api/netbox/enrichment/fix-primary-ip4-correlated")
async def netbox_fix_primary_ip4_correlated(body: legacy_runtime.NetBoxPrimaryIpFixPayload = legacy_runtime.NetBoxPrimaryIpFixPayload()):
    return await legacy_runtime.netbox_fix_primary_ip4_correlated(body)
