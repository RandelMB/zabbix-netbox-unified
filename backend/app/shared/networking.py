from __future__ import annotations

import ipaddress
from typing import Any

from app.shared.text import compact_text


def is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, dict, set, tuple)):
        return len(value) == 0
    return False


def normalize_ip_value(address: str) -> str:
    trimmed = compact_text(address)
    if not trimmed:
        return ""
    if "/" in trimmed:
        return trimmed
    return f"{trimmed}/128" if ":" in trimmed else f"{trimmed}/32"


def host_part(address: str) -> str:
    return compact_text(address).split("/")[0]


def ipv4_candidate(value: Any) -> str | None:
    raw = compact_text(value)
    if not raw:
        return None
    try:
        parsed = ipaddress.ip_interface(raw if "/" in raw else f"{raw}/32")
    except ValueError:
        return None
    if parsed.version != 4:
        return None
    return str(parsed)


def parse_mac_address(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        raw_bytes = bytes(value)
        if len(raw_bytes) == 6:
            return ":".join(f"{part:02X}" for part in raw_bytes)
        try:
            value = raw_bytes.decode("utf-8", "ignore")
        except Exception:
            value = raw_bytes.hex()
    raw = compact_text(value).replace("-", ":").replace(".", "").upper()
    if not raw:
        return ""
    if "." in compact_text(value):
        chunks = [raw[i:i + 4] for i in range(0, len(raw), 4)]
        raw = ":".join(chunk[:2] + ":" + chunk[2:] for chunk in chunks if len(chunk) == 4)
    if ":" not in raw:
        compact = "".join(char for char in raw if char.isalnum())
        if len(compact) == 12:
            raw = ":".join(compact[i:i + 2] for i in range(0, 12, 2))
    parts = [part.zfill(2) for part in raw.split(":") if part]
    if len(parts) != 6 or any(len(part) != 2 for part in parts):
        return ""
    return ":".join(parts)
