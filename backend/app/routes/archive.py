from __future__ import annotations

from fastapi import APIRouter

from app.schemas.archive import ArchivePayload, BulkArchivePayload
from app.services.archive_service import (
    archive_device as archive_device_service,
    bulk_archive_devices as bulk_archive_devices_service,
    get_archives as get_archives_service,
    get_export_logs as get_export_logs_service,
    restore_archived_device as restore_archived_device_service,
)


router = APIRouter()


@router.get("/api/archive/{source}")
async def get_archives(source: str):
    return get_archives_service(source)


@router.post("/api/archive/{source}/{external_id}")
async def archive_device(source: str, external_id: str, body: ArchivePayload):
    return archive_device_service(source, external_id, body)


@router.delete("/api/archive/{source}/{external_id}")
async def restore_archived_device(source: str, external_id: str):
    return restore_archived_device_service(source, external_id)


@router.post("/api/archive/bulk")
async def bulk_archive_devices(body: BulkArchivePayload):
    return bulk_archive_devices_service(body)


@router.get("/api/export-logs")
async def get_export_logs(limit: int = 100):
    return get_export_logs_service(limit)
