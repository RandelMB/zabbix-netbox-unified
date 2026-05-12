from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.schemas.netbox import NetBoxEnrichmentApplyPayload, NetBoxPrimaryIpFixPayload, NetBoxSyncProfilePayload
from app.schemas.interface_sync import NetBoxInterfaceSyncRunPayload
from app.schemas.snmp import NetBoxSnmpImportPayload, NetBoxSnmpProbePayload
from app.services import netbox_enrichment_service, netbox_service, netbox_snmp_service, netbox_sync_service
from app.services.sync_locks import interface_sync_lock


router = APIRouter()


@router.get("/api/netbox/devices")
async def netbox_devices(limit: int = 500, offset: int = 0, archived: str = "exclude"):
    return await netbox_service.list_devices_route(limit=limit, offset=offset, archived=archived)


@router.get("/api/netbox/devices/{device_id}")
async def netbox_device(device_id: int):
    return await netbox_service.get_device(device_id)


@router.post("/api/netbox/devices")
async def netbox_create_device(body: dict[str, Any]):
    return await netbox_service.create_device(body)


@router.patch("/api/netbox/devices/{device_id}")
async def netbox_update_device(device_id: int, body: dict[str, Any]):
    return await netbox_service.update_device(device_id, body)


@router.post("/api/netbox/devices/{device_id}/primary-ip")
async def netbox_set_primary_ip(device_id: int, body: dict[str, Any]):
    return await netbox_service.set_primary_ip(device_id, body)


@router.get("/api/netbox/devices/{device_id}/interfaces")
async def netbox_device_interfaces(device_id: int):
    return await netbox_service.device_interfaces(device_id)


@router.get("/api/netbox/interfaces/{iface_id}")
async def netbox_interface(iface_id: int):
    return await netbox_service.get_interface(iface_id)


@router.post("/api/netbox/interfaces")
async def netbox_create_interface(body: dict[str, Any]):
    return await netbox_service.create_interface(body)


@router.patch("/api/netbox/interfaces/{iface_id}")
async def netbox_update_interface(iface_id: int, body: dict[str, Any]):
    return await netbox_service.update_interface(iface_id, body)


@router.delete("/api/netbox/interfaces/{iface_id}")
async def netbox_delete_interface(iface_id: int):
    return await netbox_service.delete_interface(iface_id)


@router.get("/api/netbox/ips")
async def netbox_ips(device_id: int | None = None, address: str | None = None, limit: int = 200):
    return await netbox_service.list_ips(device_id=device_id, address=address, limit=limit)


@router.post("/api/netbox/ips")
async def netbox_create_ip(body: dict[str, Any]):
    return await netbox_service.create_ip(body)


@router.patch("/api/netbox/ips/{ip_id}")
async def netbox_update_ip(ip_id: int, body: dict[str, Any]):
    return await netbox_service.update_ip(ip_id, body)


@router.delete("/api/netbox/ips/{ip_id}")
async def netbox_delete_ip(ip_id: int):
    return await netbox_service.delete_ip(ip_id)


@router.get("/api/netbox/device-types")
async def netbox_device_types():
    return await netbox_service.device_types()


@router.get("/api/netbox/platforms")
async def netbox_platforms():
    return await netbox_service.platforms()


@router.post("/api/netbox/platforms")
async def netbox_create_platform(body: dict[str, Any]):
    return await netbox_service.create_platform(body)


@router.get("/api/netbox/sites")
async def netbox_sites():
    return await netbox_service.sites()


@router.get("/api/netbox/locations")
async def netbox_locations(site_id: int | None = None):
    return await netbox_service.locations(site_id_value=site_id)


@router.post("/api/netbox/locations")
async def netbox_create_location(body: dict[str, Any]):
    return await netbox_service.create_location(body)


@router.get("/api/netbox/roles")
async def netbox_device_roles():
    return await netbox_service.device_roles()


@router.get("/api/netbox/devices/{device_id}/sync")
async def netbox_device_sync(device_id: int):
    return {"status": "ok", "result": await netbox_sync_service.build_sync_preview(device_id)}


@router.put("/api/netbox/devices/{device_id}/sync")
async def netbox_update_sync_profile(device_id: int, body: NetBoxSyncProfilePayload):
    return await netbox_sync_service.update_sync_profile(device_id, body.enabled, body.field_sources)


@router.post("/api/netbox/devices/{device_id}/sync/run")
async def netbox_run_sync(device_id: int, body: NetBoxSyncProfilePayload):
    return {"status": "ok", "result": await netbox_sync_service.apply_sync_profile(device_id, body.enabled, body.field_sources)}


@router.get("/api/netbox/devices/{device_id}/interface-sync")
async def netbox_device_interface_sync(device_id: int):
    return {"status": "ok", "result": await netbox_sync_service.build_interface_sync_preview(device_id)}


@router.post("/api/netbox/devices/{device_id}/interface-sync/run")
async def netbox_run_interface_sync(device_id: int, body: NetBoxInterfaceSyncRunPayload):
    lock = interface_sync_lock(device_id)
    if lock.locked():
        raise HTTPException(409, "A NetBox interface sync is already running for this device")
    async with lock:
        return {"status": "ok", "result": await netbox_sync_service.apply_interface_sync_for_device(device_id, body)}


@router.post("/api/netbox/snmp-discovery/preview")
async def netbox_snmp_discovery_preview(body: NetBoxSnmpProbePayload):
    return await netbox_snmp_service.preview(body)


@router.post("/api/netbox/snmp-discovery/import")
async def netbox_snmp_discovery_import(body: NetBoxSnmpImportPayload):
    return await netbox_snmp_service.import_device(body)


@router.get("/api/netbox/devices/{device_id}/enrichment")
async def netbox_device_enrichment(device_id: int):
    return {"status": "ok", "result": await netbox_enrichment_service.build(device_id)}


@router.post("/api/netbox/devices/{device_id}/enrichment/apply")
async def netbox_apply_enrichment(device_id: int, body: NetBoxEnrichmentApplyPayload):
    return await netbox_enrichment_service.apply(device_id, body)


@router.post("/api/netbox/enrichment/fix-primary-ip4-correlated")
async def netbox_fix_primary_ip4_correlated(body: NetBoxPrimaryIpFixPayload = NetBoxPrimaryIpFixPayload()):
    return await netbox_enrichment_service.fix_primary_ip4_correlated(body)
