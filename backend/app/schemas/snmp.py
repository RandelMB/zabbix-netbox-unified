from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class NetBoxSnmpProbePayload(BaseModel):
    ip: str
    snmp_version: str = "v2c"
    snmp_community: Optional[str] = None
    snmp_port: int = 161
    snmp_transport: str = "udp"
    snmp_authlevel: Optional[str] = None
    snmp_authname: Optional[str] = None
    snmp_authpass: Optional[str] = None
    snmp_authalgo: Optional[str] = None
    snmp_cryptopass: Optional[str] = None
    snmp_cryptoalgo: Optional[str] = None
    snmp_context: Optional[str] = None
    site_id: Optional[int] = None
    role_id: Optional[int] = None


class NetBoxSnmpImportPayload(NetBoxSnmpProbePayload):
    name: Optional[str] = None
    device_type_id: Optional[int] = None
    platform_id: Optional[int] = None
    serial: Optional[str] = None
    description: Optional[str] = None
