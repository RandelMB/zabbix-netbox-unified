from __future__ import annotations

from pydantic import BaseModel


class CorrelationItemPayload(BaseModel):
    id: str
    label: str | None = None


class CorrelationLinkPayload(BaseModel):
    label: str | None = None
    zabbix: CorrelationItemPayload | None = None
    netbox: CorrelationItemPayload | None = None
    observium: CorrelationItemPayload | None = None
