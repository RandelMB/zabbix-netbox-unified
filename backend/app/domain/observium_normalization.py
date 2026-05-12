from __future__ import annotations

import re
from typing import Any

from app.shared.networking import parse_mac_address
from app.shared.text import as_int, compact_text


def humanize_speed(speed_bps: int | None) -> str:
    if not speed_bps or speed_bps <= 0:
        return ""
    units = ["bps", "Kbps", "Mbps", "Gbps", "Tbps"]
    value = float(speed_bps)
    unit = units[0]
    for candidate in units:
        unit = candidate
        if value < 1000 or candidate == units[-1]:
            break
        value /= 1000.0
    return f"{value:.0f} {unit}" if value >= 100 else f"{value:.1f} {unit}"


def first_non_empty(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def port_speed_bps(row: dict[str, Any]) -> int | None:
    speed = as_int(first_non_empty(row, "ifSpeed", "if_speed"))
    if speed:
        return speed
    high_speed = as_int(first_non_empty(row, "ifHighSpeed", "if_high_speed"))
    if high_speed:
        return high_speed * 1_000_000
    return None


def normalize_port_name_text(value: Any) -> str:
    text = compact_text(value)
    if not text:
        return ""
    lowered = text.lower()
    patterns = (
        (r"^port\s+(\d+)$", lambda m: f"port{m.group(1)}"),
        (r"^gigabitethernet\s*([0-9/]+)$", lambda m: f"gi{m.group(1)}"),
        (r"^gi(?:gabitethernet)?\s*([0-9/]+)$", lambda m: f"gi{m.group(1)}"),
        (r"^tengigabitethernet\s*([0-9/]+)$", lambda m: f"te{m.group(1)}"),
        (r"^te\s*([0-9/]+)$", lambda m: f"te{m.group(1)}"),
        (r"^ethernet\s*([0-9/]+)$", lambda m: f"ethernet {m.group(1)}"),
        (r"^eth\s*([0-9/]+)$", lambda m: f"eth {m.group(1)}"),
        (r"^fastethernet\s*([0-9/]+)$", lambda m: f"fa{m.group(1)}"),
        (r"^fa\s*([0-9/]+)$", lambda m: f"fa{m.group(1)}"),
        (r"^port-channel\s*([0-9/]+)$", lambda m: f"port-channel{m.group(1)}"),
        (r"^po\s*([0-9/]+)$", lambda m: f"po{m.group(1)}"),
        (r"^ae\s*([0-9/]+)$", lambda m: f"ae{m.group(1)}"),
    )
    for pattern, formatter in patterns:
        match = re.match(pattern, lowered)
        if match:
            return formatter(match)
    if re.match(r"^(port\d+|gi[0-9/]+|te[0-9/]+|fa[0-9/]+|po[0-9/]+|ae[0-9/]+|eth ?[0-9/]+|ethernet ?[0-9/]+)$", lowered):
        return lowered
    return text


def looks_like_short_port_name(value: Any) -> bool:
    text = normalize_port_name_text(value)
    if not text:
        return False
    lowered = text.lower()
    return bool(
        re.match(r"^(port\d+|gi[0-9/]+|te[0-9/]+|fa[0-9/]+|po[0-9/]+|ae[0-9/]+|eth ?[0-9/]+|ethernet ?[0-9/]+|vlan ?\d+|lo\d+)$", lowered)
        or "mlag" in lowered
        or "lag" in lowered
        or "port-channel" in lowered
        or lowered.startswith("_")
    )


def first_non_empty_normalized(*values: Any) -> str:
    for value in values:
        text = normalize_port_name_text(value)
        if text:
            return text
    return ""


def pick_observium_port_name(row: dict[str, Any]) -> str:
    candidates = [
        row.get("port_label_short"),
        row.get("ifName"),
        row.get("port_label"),
        row.get("port_label_base"),
        row.get("label"),
        row.get("ifDescr"),
        row.get("port_descr"),
    ]
    for candidate in candidates:
        if looks_like_short_port_name(candidate):
            return compact_text(candidate)
    return compact_text(first_non_empty_normalized(*candidates)) or compact_text(row.get("ifIndex")) or "unnamed"


def pick_observium_port_description(row: dict[str, Any], port_name: str) -> str:
    vendor = compact_text(first_non_empty(row, "vendor", "device_vendor")).lower()
    port_number = compact_text(first_non_empty(row, "ifIndex", "if_index", "port_id"))
    generic_patterns = [
        rf"port\s*{re.escape(port_number)}$" if port_number else "",
        r"module\s*-\s*port\s*\d+$",
        r"^vlan\s*#?\d+$",
    ]
    for candidate in (row.get("ifAlias"), row.get("port_descr")):
        text = compact_text(candidate)
        lowered = text.lower()
        if not text or text == compact_text(port_name):
            continue
        if vendor and vendor in lowered and re.search(r"port\s+\d+$", lowered):
            continue
        if any(pattern and re.search(pattern, lowered) for pattern in generic_patterns):
            continue
        if looks_like_short_port_name(text):
            continue
        if text:
            return text
    return ""


def is_lag_name(value: Any) -> bool:
    text = compact_text(value).lower()
    return any(token in text for token in ("mlag", "port-channel", "lag", "ae", "bond")) or text.startswith("_")


def lag_name_candidate(row: dict[str, Any]) -> str:
    for candidate in (row.get("ifName"), row.get("ifDescr"), row.get("port_label"), row.get("ifAlias")):
        text = compact_text(candidate)
        if text and is_lag_name(text):
            return text
    return ""


def pick_observium_vlan_id(row: dict[str, Any]) -> str | None:
    for key in ("vlan_vlan", "vlan", "vlan_id", "ifVlan", "if_vlan", "access_vlan"):
        value = compact_text(row.get(key))
        if value and value not in {"0", "None"}:
            return value
    return None


def netbox_interface_type_candidate(row: dict[str, Any]) -> str:
    raw_type = compact_text(first_non_empty(row, "ifType", "if_type")).lower()
    speed = port_speed_bps(row) or 0
    mapping = {
        "softwareloopback": "virtual",
        "propvirtual": "virtual",
        "l2vlan": "virtual",
        "bridge": "bridge",
        "ieee8023adlag": "lag",
    }
    if raw_type in mapping:
        return mapping[raw_type]
    if raw_type == "ethernetcsmacd":
        if speed >= 100_000_000_000:
            return "100gbase-x-qsfp28"
        if speed >= 40_000_000_000:
            return "40gbase-x-qsfpp"
        if speed >= 25_000_000_000:
            return "25gbase-x-sfp28"
        if speed >= 10_000_000_000:
            return "10gbase-x-sfpp"
        if speed >= 5_000_000_000:
            return "5gbase-t"
        if speed >= 2_500_000_000:
            return "2.5gbase-t"
        if speed >= 1_000_000_000:
            return "1000base-t"
        if speed >= 100_000_000:
            return "100base-tx"
        if speed >= 10_000_000:
            return "10base-t"
        return "other"
    return ""


def summarize_oper_state(row: dict[str, Any]) -> dict[str, Any]:
    oper = compact_text(first_non_empty(row, "ifOperStatus", "if_oper_status"))
    admin = compact_text(first_non_empty(row, "ifAdminStatus", "if_admin_status"))
    speed_bps = port_speed_bps(row)
    return {
        "oper_status": oper,
        "admin_status": admin,
        "speed_bps": speed_bps,
        "speed_label": humanize_speed(speed_bps),
        "type": compact_text(first_non_empty(row, "ifType", "if_type")),
        "mtu": as_int(first_non_empty(row, "ifMtu", "if_mtu")),
    }


def normalize_observium_port(row: dict[str, Any]) -> dict[str, Any]:
    data = dict(row)
    port_name = pick_observium_port_name(data)
    state = summarize_oper_state(data)
    candidates = []
    for candidate in (
        data.get("port_label_short"),
        data.get("ifDescr"),
        data.get("ifName"),
        data.get("port_label"),
        data.get("port_label_base"),
        data.get("label"),
        data.get("port_descr"),
    ):
        text = compact_text(candidate)
        if text and text not in candidates:
            candidates.append(text)
    return {
        "port_id": as_int(data.get("port_id")) or 0,
        "device_id": as_int(data.get("device_id")) or 0,
        "ifIndex": as_int(data.get("ifIndex") or data.get("if_index")),
        "name": port_name,
        "name_candidates": candidates or [port_name],
        "description": pick_observium_port_description(data, port_name),
        "mac_address": parse_mac_address(first_non_empty(
            data,
            "ifPhysAddress",
            "ifPhysAddress_hex",
            "ifPhysAddress_text",
            "if_phys_address",
            "phys_address",
            "mac_address",
        )),
        "type": netbox_interface_type_candidate(data),
        "raw_type": state["type"],
        "admin_status": state["admin_status"],
        "oper_status": state["oper_status"],
        "enabled_candidate": state["admin_status"].lower() not in {"down", "disabled", "admin down"} if state["admin_status"] else None,
        "speed_bps": state["speed_bps"],
        "speed_label": state["speed_label"],
        "mtu": state["mtu"],
        "vlans": [],
        "lag_parent_port_id": None,
        "lag_parent_name": "",
        "lag_member_port_ids": [],
        "lag_member_names": [],
        "lag_role": "standalone",
        "lag_name": lag_name_candidate(data),
        "raw": data,
    }


def normalize_observium_device(row: dict[str, Any], base_url: str = "") -> dict[str, Any]:
    row = dict(row)
    row["disabled"] = bool(row.get("disabled"))
    row["ignore"] = bool(row.get("ignore"))
    row["skip_icmp"] = bool(row.get("skip_icmp") or row.get("disable_icmp") or row.get("icmp_disable"))
    row["status"] = int(row.get("status") or 0)
    if base_url:
        row["web_url"] = f"{base_url}/device/device={row['device_id']}/"
    return row


def serial_candidate_from_observium(device: dict[str, Any] | None) -> str | None:
    return compact_text((device or {}).get("serial")) or None
