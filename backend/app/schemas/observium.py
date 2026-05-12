from __future__ import annotations

from pydantic import BaseModel


class ObserviumDeviceCreate(BaseModel):
    hostname: str
    snmp_version: str = "v2c"
    snmp_community: str | None = None
    snmp_port: int = 161
    snmp_transport: str = "udp"
    snmp_authlevel: str | None = None
    snmp_authname: str | None = None
    snmp_authpass: str | None = None
    snmp_authalgo: str | None = None
    snmp_cryptopass: str | None = None
    snmp_cryptoalgo: str | None = None
    snmp_context: str | None = None
    label: str | None = None
    location: str | None = None
    purpose: str | None = None
    skip_icmp: bool = False
    run_discovery: bool = True
    run_poller: bool = False


class ExportZabbixToObserviumPayload(BaseModel):
    hostids: list[str]
    run_discovery: bool = True
    run_poller: bool = False
    update_existing: bool = True
