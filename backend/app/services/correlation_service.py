from __future__ import annotations

from fastapi import HTTPException

from app.repositories.correlations_repository import (
    find_correlation_by_item,
    list_correlations,
    remove_correlation_item,
    save_correlation,
)
from app.schemas.correlations import CorrelationLinkPayload


def list_all() -> dict:
    return {"result": list_correlations()}


def find_match(source: str, external_id: str) -> dict:
    return {"result": find_correlation_by_item(source, external_id)}


def link(body: CorrelationLinkPayload) -> dict:
    return {"status": "ok", "result": save_correlation(body)}


def unlink(group_id: int, source: str) -> dict:
    if source not in {"zabbix", "netbox", "observium"}:
        raise HTTPException(400, "Invalid source")
    return remove_correlation_item(group_id, source)
