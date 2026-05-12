from __future__ import annotations

from fastapi import APIRouter

from app.schemas.discovery import DiscoveryLldpApplyPayload, DiscoveryLldpPreviewPayload
from app.services import discovery_service


router = APIRouter()


@router.post("/api/discovery/lldp/preview")
async def discovery_lldp_preview(body: DiscoveryLldpPreviewPayload):
    return {"status": "ok", "result": await discovery_service.build_preview(body)}


@router.post("/api/discovery/lldp/apply")
async def discovery_lldp_apply(body: DiscoveryLldpApplyPayload):
    return {"status": "ok", "result": await discovery_service.apply(body)}
