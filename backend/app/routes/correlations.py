from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.repositories.correlations_repository import (
    find_correlation_by_item,
    list_correlations,
    remove_correlation_item,
    save_correlation,
)
from app.schemas.correlations import CorrelationLinkPayload


router = APIRouter()


@router.get("/api/correlations")
async def correlations():
    return {"result": list_correlations()}


@router.get("/api/correlations/match")
async def correlation_match(source: str, external_id: str):
    return {"result": find_correlation_by_item(source, external_id)}


@router.post("/api/correlations/link")
async def correlation_link(body: CorrelationLinkPayload):
    return {"status": "ok", "result": save_correlation(body)}


@router.delete("/api/correlations/{group_id}/{source}")
async def correlation_unlink(group_id: int, source: str):
    if source not in {"zabbix", "netbox", "observium"}:
        raise HTTPException(400, "Invalid source")
    return remove_correlation_item(group_id, source)
