from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.core.settings import cfg
from app.domain.netbox_normalization import build_observium_platform_label
from app.domain.observium_normalization import first_non_empty, normalize_observium_device, serial_candidate_from_observium
from app.integrations.observium_db import device_row as db_device_row
from app.integrations.observium_db import link_rows as db_link_rows
from app.integrations.observium_db import mac_ip_rows as db_mac_ip_rows
from app.integrations.observium_db import optional_icmp_column
from app.integrations.observium_db import port_rows as db_port_rows
from app.integrations.observium_runtime import ObserviumRuntime
from app.repositories.archive_repository import archive_status, log_export
from app.services.archive_service import apply_archive_mode
from app.services.settings_service import get_observium_base_url, is_ip_address, observium_runtime, observium_web_client
from app.shared.networking import host_part, parse_mac_address
from app.shared.text import as_int, compact_text, normalize_key


def _runtime() -> ObserviumRuntime:
    return observium_runtime()


def db_query(sql: str, params: tuple[Any, ...] = (), fetch: str = "all") -> Any:
    return _runtime().db_query(sql, params=params, fetch=fetch)


def exec_command(args: list[str], *, fail_on_error: bool = True) -> dict[str, Any]:
    return _runtime().exec(args, fail_on_error=fail_on_error)


def device_columns() -> set[str]:
    try:
        rows = db_query("SHOW COLUMNS FROM devices")
        return {str(row["Field"]) for row in rows}
    except Exception:
        return set()


def device(device_id: int) -> dict[str, Any] | None:
    try:
        row = db_device_row(db_query, device_id)
        if not row:
            return None
        return normalize_observium_device(row, get_observium_base_url())
    except Exception:
        client = observium_web_client()
        if not client.configured():
            raise
        popup = client.device_popup(device_id)
        return normalize_observium_device(popup, get_observium_base_url()) if popup else None


def device_ports(device_id: int) -> list[dict[str, Any]]:
    try:
        return db_port_rows(db_query, device_id)
    except Exception:
        client = observium_web_client()
        if not client.configured():
            raise
        return client.device_ports(device_id)


def find_device_by_mac(mac_address: str) -> dict[str, Any] | None:
    normalized = parse_mac_address(mac_address)
    if not normalized:
        return None
    try:
        row = db_query(
            """
            SELECT device_id
            FROM ports
            WHERE REPLACE(UPPER(COALESCE(ifPhysAddress, '')), '-', '') = REPLACE(%s, ':', '')
            LIMIT 1
            """,
            (normalized,),
            fetch="one",
        )
    except Exception:
        row = None
    if not row:
        return None
    return device(int(row["device_id"]))


async def find_device_by_identity(name: str, ip_value: str = "", mac_address: str = "") -> dict[str, Any] | None:
    target_name = normalize_key(name)
    target_ip = host_part(ip_value) if ip_value else ""
    if mac_address:
        by_mac = find_device_by_mac(mac_address)
        if by_mac:
            return by_mac
    rows = db_query("SELECT device_id FROM devices ORDER BY device_id DESC")
    for row in rows or []:
        current = device(int(row["device_id"]))
        if not current:
            continue
        if target_name and (normalize_key(current.get("hostname")) == target_name or normalize_key(current.get("sysName")) == target_name):
            return current
        if target_ip and host_part(current.get("ip") or "") == target_ip:
            return current
    return None


def link_rows(local_device_id: int) -> list[dict[str, Any]]:
    try:
        rows = db_link_rows(db_query, local_device_id)
        if rows:
            return rows
    except Exception:
        pass
    client = observium_web_client()
    return client.device_links(local_device_id) if client.configured() else []


def ip_from_mac(mac_address: str) -> str:
    for row in db_mac_ip_rows(db_query, mac_address):
        for key in ("ipv4_address", "ip_address", "address", "ip"):
            candidate = compact_text(row.get(key))
            if candidate:
                return host_part(candidate)
    return ""


def link_neighbor_mac(row: dict[str, Any]) -> str:
    for key in ("remote_ifPhysAddress", "remote_mac", "peer_mac", "remote_port_mac", "lldpRemChassisId"):
        value = parse_mac_address(row.get(key))
        if value:
            return value
    return ""


def link_neighbor_name(row: dict[str, Any]) -> str:
    for key in ("remote_hostname", "remote_sysName", "remote_name", "lldpRemSysName", "remote_device"):
        value = compact_text(row.get(key))
        if value:
            return value
    return ""


def build_add_command(body: Any) -> list[str]:
    base = ["php", "/opt/observium/add_device.php", body.hostname]
    if body.snmp_version == "v3":
        authlevel_map = {"noAuthNoPriv": "any", "authNoPriv": "anp", "authPriv": "ap"}
        authlevel = authlevel_map.get(body.snmp_authlevel or "", body.snmp_authlevel or "any")
        base.extend([authlevel, "v3", body.snmp_authname or ""])
        if authlevel in {"anp", "ap"}:
            base.append(body.snmp_authpass or "")
            base.append(body.snmp_authalgo or "SHA")
        if authlevel == "ap":
            base.append(body.snmp_cryptopass or "")
            base.append(body.snmp_cryptoalgo or "AES")
        base.extend([str(body.snmp_port), body.snmp_transport])
        if body.snmp_context:
            base.append(body.snmp_context)
        return base
    return base + [body.snmp_community or "", body.snmp_version, str(body.snmp_port), body.snmp_transport]


def discovery_modules() -> list[str]:
    return [module.strip() for module in cfg.OBSERVIUM_DISCOVERY_MODULES.split(",") if module.strip()]


def build_discovery_command(device_id: int) -> list[str]:
    command = ["php", "/opt/observium/discovery.php", "-h", str(device_id)]
    modules = discovery_modules()
    if modules:
        command.extend(["-m", ",".join(modules)])
    return command


def run_discovery(device_id: int) -> dict[str, Any]:
    result = exec_command(build_discovery_command(device_id))
    result["mode"] = "metadata-only"
    result["modules"] = discovery_modules()
    return result


def run_poller(device_id: int) -> dict[str, Any]:
    if not cfg.OBSERVIUM_ENABLE_POLLER:
        return {"status": "skipped", "reason": "poller_disabled_by_config", "device_id": device_id}
    return exec_command(["php", "/opt/observium/poller.php", "-h", str(device_id)])


def refresh_device(device_id: int) -> dict[str, Any]:
    current = db_query("SELECT hostname FROM devices WHERE device_id = %s", (device_id,), fetch="one")
    if not current:
        raise HTTPException(404, "Observium device not found")
    discovery = run_discovery(device_id)
    poller = run_poller(device_id)
    return {"device_id": device_id, "hostname": current["hostname"], "discovery": discovery, "poller": poller}


def snmp_check(address: str, port: int, snmp: dict[str, Any]) -> dict[str, Any]:
    args = [
        "/opt/observium/scripts/local-sync/snmp_check.sh",
        address,
        snmp["version"],
        str(port),
        snmp.get("community", ""),
        snmp.get("security_name", ""),
        snmp.get("auth_protocol", ""),
        snmp.get("auth_password", ""),
        snmp.get("priv_protocol", ""),
        snmp.get("priv_password", ""),
        snmp.get("security_level", ""),
    ]
    return exec_command(args)


async def list_devices(limit: int = 500, search: str = "", archived: str = "exclude") -> dict[str, Any]:
    try:
        query = "SELECT * FROM devices"
        params: list[Any] = []
        if search:
            query += " WHERE hostname LIKE %s OR sysName LIKE %s OR ip LIKE %s OR location LIKE %s"
            token = f"%{search}%"
            params.extend([token, token, token, token])
        query += " ORDER BY hostname ASC LIMIT %s"
        params.append(limit)
        rows = db_query(query, tuple(params))
        items = [normalize_observium_device(row, get_observium_base_url()) for row in rows]
    except Exception:
        client = observium_web_client()
        if not client.configured():
            raise
        items = [normalize_observium_device(row, get_observium_base_url()) for row in client.device_entities()]
        if search:
            token = normalize_key(search)
            items = [item for item in items if token in normalize_key(item.get("hostname")) or token in normalize_key(item.get("sysName")) or token in normalize_key(item.get("ip"))]
        items = items[:limit]
    return {"result": apply_archive_mode("observium", items, "device_id", archived)}


async def get_device(device_id: int) -> dict[str, Any]:
    current = device(device_id)
    if not current:
        raise HTTPException(404, "Observium device not found")
    current["archived"] = archive_status("observium", str(device_id))
    return {"result": current}


async def get_device_ports(device_id: int) -> dict[str, Any]:
    current = device(device_id)
    if not current:
        raise HTTPException(404, "Observium device not found")
    return {"result": device_ports(device_id)}


async def create_device(body: Any) -> dict[str, Any]:
    add_result = exec_command(build_add_command(body))
    row = db_query("SELECT device_id, hostname FROM devices WHERE hostname = %s", (body.hostname,), fetch="one")
    if not row:
        raise HTTPException(400, f"Observium add failed: {add_result['output']}")
    updates = {"label": body.label, "location": body.location, "purpose": body.purpose, "ip": body.hostname}
    updates = {key: value for key, value in updates.items() if value}
    icmp_column = optional_icmp_column(db_query)
    if icmp_column:
        updates[icmp_column] = int(body.skip_icmp)
    if updates:
        await update_device(int(row["device_id"]), updates)
    refresh_result = None
    if body.run_discovery or body.run_poller:
        refresh_result = {}
        if body.run_discovery:
            refresh_result["discovery"] = run_discovery(int(row["device_id"]))
        if body.run_poller:
            refresh_result["poller"] = run_poller(int(row["device_id"]))
    return {"status": "ok", "device_id": row["device_id"], "hostname": row["hostname"], "add_result": add_result, "refresh_result": refresh_result}


async def update_device(device_id: int, body: dict[str, Any]) -> dict[str, Any]:
    current = db_query("SELECT * FROM devices WHERE device_id = %s", (device_id,), fetch="one")
    if not current:
        raise HTTPException(404, "Observium device not found")
    rename_result = None
    if "hostname" in body and body["hostname"] and body["hostname"] != current["hostname"]:
        rename_result = exec_command(["php", "/opt/observium/rename_device.php", "-p", current["hostname"], body["hostname"]])
    allowed_fields = {
        "label", "ip", "snmp_version", "snmp_community", "snmp_port", "snmp_transport",
        "snmp_authlevel", "snmp_authname", "snmp_authpass", "snmp_authalgo",
        "snmp_cryptopass", "snmp_cryptoalgo", "snmp_context", "location", "purpose",
        "disabled", "ignore", "status",
    }
    icmp_column = optional_icmp_column(db_query)
    translated_body = dict(body)
    if "skip_icmp" in translated_body and icmp_column:
        translated_body[icmp_column] = int(bool(translated_body.pop("skip_icmp")))
    updates: list[str] = []
    params: list[Any] = []
    for field, value in translated_body.items():
        if field not in allowed_fields:
            if icmp_column and field == icmp_column:
                updates.append(f"`{field}` = %s")
                params.append(int(value))
            continue
        updates.append(f"`{field}` = %s")
        if field in {"disabled", "ignore", "status", "snmp_port"} and value is not None:
            params.append(int(value))
        else:
            params.append(value)
    if updates:
        params.append(device_id)
        db_query(f"UPDATE devices SET {', '.join(updates)} WHERE device_id = %s", tuple(params), fetch="none")
    updated = db_query("SELECT * FROM devices WHERE device_id = %s", (device_id,), fetch="one")
    hostname_changed = bool(body.get("hostname") and body["hostname"] != current["hostname"])
    if hostname_changed and updated and updated["hostname"] == current["hostname"] and not is_ip_address(str(body["hostname"])):
        fallback_updates = []
        fallback_params = []
        if not body.get("label"):
            fallback_updates.append("`label` = %s")
            fallback_params.append(body["hostname"])
        if fallback_updates:
            fallback_params.append(device_id)
            db_query(f"UPDATE devices SET {', '.join(fallback_updates)} WHERE device_id = %s", tuple(fallback_params), fetch="none")
            updated = db_query("SELECT * FROM devices WHERE device_id = %s", (device_id,), fetch="one")
    return {"status": "ok", "rename_result": rename_result, "result": normalize_observium_device(updated, get_observium_base_url())}


async def delete_device(device_id: int) -> dict[str, Any]:
    current = db_query("SELECT hostname FROM devices WHERE device_id = %s", (device_id,), fetch="one")
    if not current:
        raise HTTPException(404, "Observium device not found")
    result = exec_command(["php", "/opt/observium/delete_device.php", current["hostname"]])
    return {"status": "ok", "hostname": current["hostname"], "result": result}


async def refresh(device_id: int) -> dict[str, Any]:
    return refresh_device(device_id)


async def export_zabbix_hosts_to_observium(body: Any) -> dict[str, Any]:
    if not body.hostids:
        raise HTTPException(400, "hostids is required")
    from app.schemas.observium import ObserviumDeviceCreate
    from app.services.zabbix_service import extract_zabbix_snmp_host, global_macros, request as zabbix_request

    zabbix_result = await zabbix_request(
        "host.get",
        {
            "output": "extend",
            "hostids": body.hostids,
            "selectInterfaces": "extend",
            "selectInventory": "extend",
            "selectMacros": "extend",
            "selectTags": "extend",
            "selectGroups": "extend",
        },
    )
    macros = await global_macros()
    results: list[dict[str, Any]] = []
    for host in zabbix_result["result"] or []:
        host["globalmacros"] = macros
        payload_snapshot = {"hostid": host.get("hostid"), "host": host.get("host"), "name": host.get("name")}
        try:
            candidate = extract_zabbix_snmp_host(host)
            try:
                check = snmp_check(candidate["address"], candidate["port"], candidate["snmp"])
            except HTTPException as exc:
                check = {"status": "error", "message": str(exc.detail)}
            existing = db_query(
                "SELECT device_id, hostname FROM devices WHERE hostname = %s OR ip = %s LIMIT 1",
                (candidate["address"], candidate["address"]),
                fetch="one",
            )
            if existing and body.update_existing:
                update_payload = {
                    "hostname": candidate["address"],
                    "ip": candidate["address"],
                    "snmp_version": candidate["snmp"]["version"],
                    "snmp_community": candidate["snmp"].get("community"),
                    "snmp_port": candidate["port"],
                    "snmp_transport": "udp",
                    "location": candidate["inventory"].get("location") or "",
                    "purpose": candidate["inventory"].get("type_full") or "",
                    "label": candidate["name"],
                }
                updated = await update_device(int(existing["device_id"]), update_payload)
                refresh_result = refresh_device(int(existing["device_id"])) if (body.run_discovery or body.run_poller) else None
                result = {"status": "updated", "hostid": candidate["hostid"], "host": candidate["host"], "observium_device_id": existing["device_id"], "snmp_check": check, "update": updated, "refresh": refresh_result}
            elif existing:
                result = {"status": "skipped", "hostid": candidate["hostid"], "host": candidate["host"], "observium_device_id": existing["device_id"], "message": "Device already exists in Observium"}
            else:
                created = await create_device(
                    ObserviumDeviceCreate(
                        hostname=candidate["address"],
                        snmp_version=candidate["snmp"]["version"],
                        snmp_community=candidate["snmp"].get("community"),
                        snmp_port=candidate["port"],
                        snmp_transport="udp",
                        snmp_authlevel=candidate["snmp"].get("security_level"),
                        snmp_authname=candidate["snmp"].get("security_name"),
                        snmp_authpass=candidate["snmp"].get("auth_password"),
                        snmp_authalgo=candidate["snmp"].get("auth_protocol"),
                        snmp_cryptopass=candidate["snmp"].get("priv_password"),
                        snmp_cryptoalgo=candidate["snmp"].get("priv_protocol"),
                        label=candidate["name"],
                        location=candidate["inventory"].get("location") or "",
                        purpose=candidate["inventory"].get("type_full") or "",
                        run_discovery=body.run_discovery,
                        run_poller=body.run_poller,
                    )
                )
                result = {"status": "created", "hostid": candidate["hostid"], "host": candidate["host"], "snmp_check": check, "create": created}
            log_export(action="manual_export", source="zabbix", target="observium", status=result["status"], entity_id=str(host.get("hostid")), entity_label=host.get("host"), message=f"Zabbix host {host.get('host')} exported to Observium ({result['status']})", payload=payload_snapshot, response=result)
            results.append(result)
        except Exception as exc:
            error_result = {"status": "error", "hostid": host.get("hostid"), "host": host.get("host"), "message": str(exc)}
            log_export(action="manual_export", source="zabbix", target="observium", status="error", entity_id=str(host.get("hostid")), entity_label=host.get("host"), message=str(exc), payload=payload_snapshot, response=error_result)
            results.append(error_result)
    return {"status": "ok", "count": len(results), "result": results}


__all__ = [
    "build_observium_platform_label",
    "db_query",
    "device",
    "device_ports",
    "export_zabbix_hosts_to_observium",
    "find_device_by_identity",
    "ip_from_mac",
    "link_neighbor_mac",
    "link_neighbor_name",
    "link_rows",
    "list_devices",
    "refresh",
    "serial_candidate_from_observium",
    "snmp_check",
    "update_device",
]
