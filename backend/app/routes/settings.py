from __future__ import annotations

from fastapi import APIRouter

from app.schemas.settings import CredentialsPayload
from app.services.settings_service import (
    check_netbox_connection,
    check_observium_connection,
    check_zabbix_connection,
    get_mapping,
    get_runtime_credentials,
    get_settings_snapshot,
    set_mapping,
    set_runtime_credentials,
)


router = APIRouter()


@router.post("/api/credentials")
async def set_credentials(creds: CredentialsPayload):
    return set_runtime_credentials(creds.model_dump(exclude_none=True))


@router.get("/api/credentials")
async def get_credentials():
    return get_runtime_credentials()


@router.get("/api/settings")
async def get_settings():
    return get_settings_snapshot()


@router.get("/api/check/zabbix")
async def check_zabbix():
    return await check_zabbix_connection()


@router.get("/api/check/netbox")
async def check_netbox():
    return await check_netbox_connection()


@router.get("/api/check/observium")
async def check_observium():
    return await check_observium_connection()


@router.get("/api/mapping")
async def read_mapping():
    return get_mapping()


@router.put("/api/mapping")
async def write_mapping(body: dict):
    return set_mapping(body)
