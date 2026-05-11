from __future__ import annotations

from fastapi import APIRouter

from app.services import legacy_runtime


router = APIRouter()


@router.post("/api/discovery/lldp/preview")
async def discovery_lldp_preview(body: legacy_runtime.DiscoveryLldpPreviewPayload):
    return await legacy_runtime.discovery_lldp_preview(body)


@router.post("/api/discovery/lldp/apply")
async def discovery_lldp_apply(body: legacy_runtime.DiscoveryLldpApplyPayload):
    return await legacy_runtime.discovery_lldp_apply(body)
