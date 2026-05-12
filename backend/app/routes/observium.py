from __future__ import annotations

from fastapi import APIRouter

from app.schemas.observium import ExportZabbixToObserviumPayload, ObserviumDeviceCreate
from app.services import observium_service


router = APIRouter()


@router.get("/api/observium/devices")
async def observium_devices(limit: int = 500, search: str = "", archived: str = "exclude"):
    return await observium_service.list_devices(limit=limit, search=search, archived=archived)


@router.get("/api/observium/devices/{device_id}")
async def observium_device(device_id: int):
    return await observium_service.get_device(device_id)


@router.get("/api/observium/devices/{device_id}/ports")
async def observium_device_ports(device_id: int):
    return await observium_service.get_device_ports(device_id)


@router.post("/api/observium/devices")
async def observium_create_device(body: ObserviumDeviceCreate):
    return await observium_service.create_device(body)


@router.patch("/api/observium/devices/{device_id}")
async def observium_update_device(device_id: int, body: dict):
    return await observium_service.update_device(device_id, body)


@router.delete("/api/observium/devices/{device_id}")
async def observium_delete_device(device_id: int):
    return await observium_service.delete_device(device_id)


@router.post("/api/observium/devices/{device_id}/refresh")
async def observium_refresh(device_id: int):
    return await observium_service.refresh(device_id)


@router.post("/api/exports/zabbix-to-observium")
async def export_zabbix_to_observium(body: ExportZabbixToObserviumPayload):
    return await observium_service.export_zabbix_hosts_to_observium(body)
