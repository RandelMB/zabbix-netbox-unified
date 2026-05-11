from __future__ import annotations

import json

from app.repositories.app_db import app_db


SYNC_FIELDS = ("primary_ip4", "serial", "platform")
SYNC_SOURCE_DEFAULTS = {
    "primary_ip4": "zabbix",
    "serial": "zabbix",
    "platform": "observium",
}


def get_netbox_sync_profile(device_id: int) -> dict:
    conn = app_db()
    try:
        row = conn.execute(
            "SELECT device_id, enabled, field_sources_json, updated_at FROM netbox_sync_profiles WHERE device_id = ?",
            (str(device_id),),
        ).fetchone()
        if not row:
            return {"device_id": str(device_id), "enabled": False, "field_sources": {}, "updated_at": None}
        return {
            "device_id": row["device_id"],
            "enabled": bool(row["enabled"]),
            "field_sources": json.loads(row["field_sources_json"] or "{}"),
            "updated_at": row["updated_at"],
        }
    finally:
        conn.close()


def save_netbox_sync_profile(device_id: int, enabled: bool, field_sources: dict[str, str]) -> dict:
    normalized = {
        field: source
        for field, source in field_sources.items()
        if field in SYNC_FIELDS and source in {"zabbix", "observium"}
    }
    conn = app_db()
    try:
        conn.execute(
            """
            INSERT INTO netbox_sync_profiles (device_id, enabled, field_sources_json, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(device_id) DO UPDATE SET
              enabled = excluded.enabled,
              field_sources_json = excluded.field_sources_json,
              updated_at = CURRENT_TIMESTAMP
            """,
            (str(device_id), 1 if enabled else 0, json.dumps(normalized, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()
    return get_netbox_sync_profile(device_id)
