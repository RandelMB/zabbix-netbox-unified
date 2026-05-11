from __future__ import annotations

from fastapi import APIRouter

from app.services import legacy_runtime


router = APIRouter()


@router.get("/api/observium/devices")
async def observium_devices(limit: int = 500, search: str = "", archived: str = "exclude"):
    return await legacy_runtime.observium_devices(limit=limit, search=search, archived=archived)


@router.get("/api/observium/devices/{device_id}")
async def observium_device(device_id: int):
    return await legacy_runtime.observium_device(device_id)


@router.get("/api/observium/devices/{device_id}/ports")
async def observium_device_ports(device_id: int):
    return await legacy_runtime.observium_device_ports(device_id)


@router.post("/api/observium/devices")
async def observium_create_device(body: legacy_runtime.ObserviumDeviceCreate):
    return await legacy_runtime.observium_create_device(body)


@router.patch("/api/observium/devices/{device_id}")
async def observium_update_device(device_id: int, body: dict):
    return await legacy_runtime.observium_update_device(device_id, body)


@router.delete("/api/observium/devices/{device_id}")
async def observium_delete_device(device_id: int):
    return await legacy_runtime.observium_delete_device(device_id)


@router.post("/api/observium/devices/{device_id}/refresh")
async def observium_refresh(device_id: int):
    return await legacy_runtime.observium_refresh(device_id)


@router.post("/api/exports/zabbix-to-observium")
async def export_zabbix_to_observium(body: legacy_runtime.ExportZabbixToObserviumPayload):
    return await legacy_runtime.export_zabbix_to_observium(body)
