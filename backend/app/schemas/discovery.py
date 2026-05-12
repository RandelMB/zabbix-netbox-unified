from __future__ import annotations

from pydantic import BaseModel, Field


class DiscoveryLldpPreviewPayload(BaseModel):
    source: str
    source_id: str


class DiscoveryLldpApplyPayload(BaseModel):
    source: str
    source_id: str
    proposal_ids: list[str] = Field(default_factory=list)
    site_id: int | None = None
    role_id: int | None = None
