from __future__ import annotations


from app.repositories.archive_repository import (
    archived_ids,
    clear_archive,
    list_archives,
    list_export_logs,
    set_archive,
)
from app.schemas.archive import ArchivePayload, BulkArchivePayload


def apply_archive_mode(source: str, items: list[dict], id_field: str, mode: str) -> list[dict]:
    archived = archived_ids(source)
    filtered: list[dict] = []
    for item in items:
        archived_flag = str(item.get(id_field)) in archived
        item["archived"] = archived_flag
        if mode == "exclude" and archived_flag:
            continue
        if mode == "only" and not archived_flag:
            continue
        filtered.append(item)
    return filtered


def get_archives(source: str) -> dict:
    return {"result": list_archives(source)}


def archive_device(source: str, external_id: str, body: ArchivePayload) -> dict:
    set_archive(source, external_id, body.label, body.details)
    return {"status": "ok", "source": source, "external_id": external_id}


def restore_archived_device(source: str, external_id: str) -> dict:
    clear_archive(source, external_id)
    return {"status": "ok", "source": source, "external_id": external_id}


def bulk_archive_devices(body: BulkArchivePayload) -> dict:
    for external_id in body.ids:
        if body.archive:
            set_archive(body.source, external_id, None, {})
        else:
            clear_archive(body.source, external_id)
    return {"status": "ok", "count": len(body.ids), "source": body.source, "archive": body.archive}


def get_export_logs(limit: int = 100) -> dict:
    return {"result": list_export_logs(limit)}
