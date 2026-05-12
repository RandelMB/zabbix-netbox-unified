from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.services import zabbix_service


router = APIRouter()


@router.get("/api/zabbix/hosts")
async def zabbix_hosts(limit: int = 500, archived: str = "exclude"):
    return await zabbix_service.hosts(limit=limit, archived=archived)


@router.get("/api/zabbix/hosts/{host_id}")
async def zabbix_host(host_id: str):
    return await zabbix_service.host(host_id)


@router.post("/api/zabbix/hosts")
async def zabbix_create_host(body: dict[str, Any]):
    return await zabbix_service.create_host(body)


@router.patch("/api/zabbix/hosts/{host_id}")
async def zabbix_update_host(host_id: str, body: dict[str, Any]):
    return await zabbix_service.update_host(host_id, body)


@router.get("/api/zabbix/interfaces/{host_id}")
async def zabbix_interfaces(host_id: str):
    return await zabbix_service.interfaces(host_id)


@router.post("/api/zabbix/interfaces")
async def zabbix_create_interface(body: dict[str, Any]):
    return await zabbix_service.create_interface(body)


@router.patch("/api/zabbix/interfaces/{iface_id}")
async def zabbix_update_interface(iface_id: str, body: dict[str, Any]):
    return await zabbix_service.update_interface(iface_id, body)


@router.delete("/api/zabbix/interfaces/{iface_id}")
async def zabbix_delete_interface(iface_id: str):
    return await zabbix_service.delete_interface(iface_id)


@router.get("/api/zabbix/groups")
async def zabbix_groups():
    return await zabbix_service.groups()
