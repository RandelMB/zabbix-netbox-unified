from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class CredentialsPayload(BaseModel):
    zabbix_url: str | None = None
    zabbix_token: str | None = None
    zabbix_user: str | None = None
    zabbix_pass: str | None = None
    netbox_url: str | None = None
    netbox_token: str | None = None


class MappingPayload(BaseModel):
    root: dict[str, Any]
