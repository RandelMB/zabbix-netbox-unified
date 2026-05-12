from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class NetBoxEnrichmentActionPayload(BaseModel):
    action_type: str
    value: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class NetBoxEnrichmentApplyPayload(BaseModel):
    actions: list[NetBoxEnrichmentActionPayload] = Field(default_factory=list)


class NetBoxPrimaryIpFixPayload(BaseModel):
    dry_run: bool = False


class NetBoxSyncProfilePayload(BaseModel):
    enabled: bool = False
    field_sources: dict[str, str] = Field(default_factory=dict)
