from __future__ import annotations

from fastapi import APIRouter

from app.repositories.archive_repository import clear_archive, list_archives, list_export_logs, set_archive
from app.schemas.archive import ArchivePayload, BulkArchivePayload


router = APIRouter()


@router.get("/api/archive/{source}")
async def get_archives(source: str):
    return {"result": list_archives(source)}


@router.post("/api/archive/{source}/{external_id}")
async def archive_device(source: str, external_id: str, body: ArchivePayload):
    set_archive(source, external_id, body.label, body.details)
    return {"status": "ok", "source": source, "external_id": external_id}


@router.delete("/api/archive/{source}/{external_id}")
async def restore_archived_device(source: str, external_id: str):
    clear_archive(source, external_id)
    return {"status": "ok", "source": source, "external_id": external_id}


@router.post("/api/archive/bulk")
async def bulk_archive_devices(body: BulkArchivePayload):
    for external_id in body.ids:
        if body.archive:
            set_archive(body.source, external_id, None, {})
        else:
            clear_archive(body.source, external_id)
    return {"status": "ok", "count": len(body.ids), "source": body.source, "archive": body.archive}


@router.get("/api/export-logs")
async def get_export_logs(limit: int = 100):
    return {"result": list_export_logs(limit)}
