from __future__ import annotations

from app.domain.netbox_normalization import build_os_version_label
from app.services.snmp_discovery import SnmpDiscoveryRuntime, build_netbox_snmp_probe, import_netbox_device_from_snmp
from app.shared.networking import normalize_ip_value


def runtime() -> SnmpDiscoveryRuntime:
    from app.services.netbox_service import ensure_device_type, ensure_platform, ensure_primary_ip4, find_device_by_identity, find_device_type_by_model, request
    from app.services.observium_service import exec_command, find_device_by_identity as observium_find_device_by_identity
    from app.services.zabbix_service import find_host_by_identity as zabbix_find_host_by_identity

    return SnmpDiscoveryRuntime(
        observium_exec_result=lambda args: exec_command(args, fail_on_error=False),
        observium_find_device_by_identity=observium_find_device_by_identity,
        zabbix_find_host_by_identity=zabbix_find_host_by_identity,
        netbox_find_device_by_identity=find_device_by_identity,
        netbox_find_device_type_by_model=find_device_type_by_model,
        ensure_netbox_device_type=ensure_device_type,
        ensure_netbox_platform=ensure_platform,
        ensure_netbox_primary_ip4=ensure_primary_ip4,
        netbox_request=request,
        normalize_ip_value=normalize_ip_value,
        build_os_version_label=build_os_version_label,
    )


async def preview(body):
    return {"status": "ok", "result": await build_netbox_snmp_probe(runtime(), body)}


async def import_device(body):
    return {"status": "ok", "result": await import_netbox_device_from_snmp(runtime(), body)}
