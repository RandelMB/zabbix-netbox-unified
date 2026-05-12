from __future__ import annotations

from fastapi import APIRouter

from app.schemas.correlations import CorrelationLinkPayload
from app.services import correlation_service


router = APIRouter()


@router.get("/api/correlations")
async def correlations():
    return correlation_service.list_all()


@router.get("/api/correlations/match")
async def correlation_match(source: str, external_id: str):
    return correlation_service.find_match(source, external_id)


@router.post("/api/correlations/link")
async def correlation_link(body: CorrelationLinkPayload):
    return correlation_service.link(body)


@router.delete("/api/correlations/{group_id}/{source}")
async def correlation_unlink(group_id: int, source: str):
    return correlation_service.unlink(group_id, source)
