from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ArchivePayload(BaseModel):
    label: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class BulkArchivePayload(BaseModel):
    source: str
    ids: list[str]
    archive: bool = True
