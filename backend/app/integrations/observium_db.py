from __future__ import annotations

from typing import Any, Callable

from app.core.settings import cfg
from app.domain.observium_normalization import normalize_observium_port
from app.shared.networking import parse_mac_address
from app.shared.text import as_int, compact_text


ObserviumQuery = Callable[[str, tuple[Any, ...], str], Any]

_table_columns_cache: dict[str, set[str]] = {}
_device_columns_cache: set[str] | None = None


def query_db(query: ObserviumQuery, sql: str, params: tuple[Any, ...] = (), fetch: str = "all") -> Any:
    return query(sql, params, fetch)


def table_columns(query: ObserviumQuery, table: str) -> set[str]:
    cached = _table_columns_cache.get(table)
    if cached is not None:
        return cached
    try:
        rows = query_db(
            query,
            """
            SELECT COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
            """,
            (cfg.OBSERVIUM_DB_NAME, table),
        )
    except Exception:
        rows = []
    columns = {compact_text(row.get("COLUMN_NAME")) for row in rows or [] if compact_text(row.get("COLUMN_NAME"))}
    _table_columns_cache[table] = columns
    return columns


def table_exists(query: ObserviumQuery, table: str) -> bool:
    return bool(table_columns(query, table))


def find_table_column(columns: set[str], *candidates: str) -> str:
    for candidate in candidates:
        if candidate in columns:
            return candidate
    return ""


def device_columns(query: ObserviumQuery) -> set[str]:
    global _device_columns_cache
    if _device_columns_cache is None:
        try:
            rows = query_db(query, "SHOW COLUMNS FROM devices")
            _device_columns_cache = {str(row["Field"]) for row in rows}
        except Exception:
            _device_columns_cache = set()
    return _device_columns_cache


def optional_icmp_column(query: ObserviumQuery) -> str | None:
    try:
        rows = query_db(
            query,
            """
            SELECT COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'devices'
            """,
            (cfg.OBSERVIUM_DB_NAME,),
        )
    except Exception:
        return None
    available = {row.get("COLUMN_NAME") for row in rows or []}
    for candidate in ("skip_icmp", "disable_icmp", "icmp_disable"):
        if candidate in available:
            return candidate
    return None


def port_stack_rows(query: ObserviumQuery, port_ids: list[int]) -> list[dict[str, Any]]:
    if not port_ids or not table_exists(query, "ports_stack"):
        return []
    columns = table_columns(query, "ports_stack")
    high_col = find_table_column(columns, "port_id_high", "high_port_id", "higher_port_id")
    low_col = find_table_column(columns, "port_id_low", "low_port_id", "lower_port_id")
    if not high_col or not low_col:
        return []
    placeholders = ", ".join(["%s"] * len(port_ids))
    try:
        return query_db(
            query,
            f"SELECT * FROM ports_stack WHERE {high_col} IN ({placeholders}) OR {low_col} IN ({placeholders})",
            tuple(port_ids + port_ids),
        )
    except Exception:
        return []


def apply_lag_relationships(query: ObserviumQuery, ports: list[dict[str, Any]]) -> None:
    port_map = {int(port["port_id"]): port for port in ports if port.get("port_id")}
    rows = port_stack_rows(query, list(port_map.keys()))
    if rows:
        columns = table_columns(query, "ports_stack")
        high_col = find_table_column(columns, "port_id_high", "high_port_id", "higher_port_id")
        low_col = find_table_column(columns, "port_id_low", "low_port_id", "lower_port_id")
        for row in rows:
            high_id = as_int(row.get(high_col))
            low_id = as_int(row.get(low_col))
            if not high_id or not low_id or high_id not in port_map or low_id not in port_map:
                continue
            parent = port_map[high_id]
            member = port_map[low_id]
            if low_id not in parent["lag_member_port_ids"]:
                parent["lag_member_port_ids"].append(low_id)
            member["lag_parent_port_id"] = high_id
    for port in ports:
        name = compact_text(port.get("name"))
        lag_name = compact_text(port.get("lag_name"))
        if port.get("type") == "lag" or any(token in name.lower() or token in lag_name.lower() for token in ("mlag", "port-channel", "lag", "ae", "bond")):
            if port["lag_role"] == "standalone":
                port["lag_role"] = "parent"
    for port in ports:
        parent_id = as_int(port.get("lag_parent_port_id"))
        if parent_id and parent_id in port_map:
            parent = port_map[parent_id]
            port["lag_parent_name"] = parent.get("name") or parent.get("lag_name") or ""
            port["lag_role"] = "member"
            if int(port["port_id"]) not in parent["lag_member_port_ids"]:
                parent["lag_member_port_ids"].append(int(port["port_id"]))
    for port in ports:
        member_names = []
        for member_id in port.get("lag_member_port_ids") or []:
            member = port_map.get(member_id)
            if member:
                member_names.append(member.get("name") or "")
        port["lag_member_names"] = [name for name in member_names if name]


def device_row(query: ObserviumQuery, device_id: int) -> dict[str, Any] | None:
    fields = [
        "device_id", "hostname", "sysName", "label", "ip",
        "snmp_version", "snmp_community", "snmp_port", "snmp_transport",
        "snmp_authlevel", "snmp_authname", "snmp_authalgo", "snmp_context",
        "status", "disabled", "`ignore`", "location", "purpose", "os", "vendor", "hardware", "version",
    ]
    if "serial" in device_columns(query):
        fields.append("serial")
    return query_db(
        query,
        f"""
        SELECT
            {", ".join(fields)}
        FROM devices
        WHERE device_id = %s
        """,
        (device_id,),
        fetch="one",
    )


def port_rows(query: ObserviumQuery, device_id: int) -> list[dict[str, Any]]:
    if not table_exists(query, "ports"):
        raise RuntimeError("Observium DB ports table unavailable")
    rows = query_db(
        query,
        """
        SELECT *
        FROM ports
        WHERE device_id = %s
        ORDER BY COALESCE(ifIndex, 0), COALESCE(port_id, 0)
        """,
        (device_id,),
    )
    ports = [normalize_observium_port(row) for row in rows or [] if not as_int(dict(row).get("deleted"))]
    if not ports:
        raise RuntimeError("Observium DB returned no ports")
    if table_exists(query, "ports_vlans"):
        port_ids = [port["port_id"] for port in ports if port.get("port_id")]
        if port_ids:
            placeholders = ", ".join(["%s"] * len(port_ids))
            try:
                vlan_rows = query_db(query, f"SELECT * FROM ports_vlans WHERE port_id IN ({placeholders})", tuple(port_ids))
            except Exception:
                vlan_rows = []
            vlan_map: dict[int, list[str]] = {}
            for row in vlan_rows or []:
                port_id = as_int(dict(row).get("port_id"))
                vlan_id = compact_text(dict(row).get("vlan_vlan") or dict(row).get("vlan") or dict(row).get("vlan_id"))
                if port_id and vlan_id:
                    vlan_map.setdefault(port_id, [])
                    if vlan_id not in vlan_map[port_id]:
                        vlan_map[port_id].append(vlan_id)
            for port in ports:
                direct_vlan = compact_text((port.get("raw") or {}).get("vlan_vlan") or (port.get("raw") or {}).get("vlan"))
                vlans = list(vlan_map.get(port["port_id"], []))
                if direct_vlan and direct_vlan not in vlans:
                    vlans.append(direct_vlan)
                port["vlans"] = vlans
    apply_lag_relationships(query, ports)
    mac_counts: dict[str, int] = {}
    for port in ports:
        mac = parse_mac_address(port.get("mac_address"))
        if mac:
            mac_counts[mac] = mac_counts.get(mac, 0) + 1
    for port in ports:
        mac = parse_mac_address(port.get("mac_address"))
        if mac and mac_counts.get(mac, 0) > 1:
            port["mac_sync_skipped"] = "duplicate_device_mac"
    return ports


def link_rows(query: ObserviumQuery, local_device_id: int) -> list[dict[str, Any]]:
    if table_exists(query, "links"):
        columns = table_columns(query, "links")
        local_device_col = find_table_column(columns, "local_device_id", "device_id", "device_id_local")
        if local_device_col:
            return query_db(query, f"SELECT * FROM links WHERE {local_device_col} = %s", (local_device_id,))
    if table_exists(query, "neighbours"):
        columns = table_columns(query, "neighbours")
        local_device_col = find_table_column(columns, "device_id", "local_device_id", "device_id_local")
        local_port_col = find_table_column(columns, "port_id", "local_port_id", "port_id_local")
        if local_device_col and local_port_col:
            active_col = find_table_column(columns, "active")
            where_clauses = [f"{local_device_col} = %s"]
            params: list[Any] = [local_device_id]
            if active_col:
                where_clauses.append(f"{active_col} = %s")
                params.append(1)
            rows = query_db(query, f"SELECT * FROM neighbours WHERE {' AND '.join(where_clauses)}", tuple(params))
            normalized_rows: list[dict[str, Any]] = []
            for row in rows or []:
                item = dict(row)
                item.setdefault("local_device_id", item.get(local_device_col))
                item.setdefault("local_port_id", item.get(local_port_col))
                item.setdefault("protocol", item.get("protocol") or "lldp")
                normalized_rows.append(item)
            return normalized_rows
    return []


def mac_ip_rows(query: ObserviumQuery, mac_address: str) -> list[dict[str, Any]]:
    normalized = parse_mac_address(mac_address)
    if not normalized:
        return []
    queries: list[tuple[str, tuple[Any, ...]]] = []
    if table_exists(query, "ipv4_mac"):
        queries.append(
            (
                """
                SELECT *
                FROM ipv4_mac
                WHERE REPLACE(UPPER(COALESCE(mac_address, '')), '-', '') = REPLACE(%s, ':', '')
                """,
                (normalized,),
            )
        )
    if table_exists(query, "arp_table"):
        queries.append(
            (
                """
                SELECT *
                FROM arp_table
                WHERE REPLACE(UPPER(COALESCE(mac_address, '')), '-', '') = REPLACE(%s, ':', '')
                """,
                (normalized,),
            )
        )
    rows: list[dict[str, Any]] = []
    for sql, params in queries:
        try:
            rows.extend(query_db(query, sql, params) or [])
        except Exception:
            continue
    return rows
