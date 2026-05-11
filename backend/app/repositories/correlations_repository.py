from __future__ import annotations

from fastapi import HTTPException

from app.repositories.app_db import app_db
from app.schemas.correlations import CorrelationLinkPayload


def correlation_summary(group_id: int) -> dict | None:
    conn = app_db()
    try:
        group = conn.execute(
            "SELECT id, label, created_at, updated_at FROM correlation_groups WHERE id = ?",
            (group_id,),
        ).fetchone()
        if not group:
            return None
        items = conn.execute(
            """
            SELECT source, external_id, display_label, created_at, updated_at
            FROM correlation_items
            WHERE group_id = ?
            ORDER BY source
            """,
            (group_id,),
        ).fetchall()
        payload = {
            "id": group["id"],
            "label": group["label"],
            "created_at": group["created_at"],
            "updated_at": group["updated_at"],
            "items": {},
        }
        for item in items:
            payload["items"][item["source"]] = {
                "id": str(item["external_id"]),
                "label": item["display_label"],
                "created_at": item["created_at"],
                "updated_at": item["updated_at"],
            }
        return payload
    finally:
        conn.close()


def find_correlation_by_item(source: str, external_id: str) -> dict | None:
    conn = app_db()
    try:
        row = conn.execute(
            "SELECT group_id FROM correlation_items WHERE source = ? AND external_id = ?",
            (source, external_id),
        ).fetchone()
        if not row:
            return None
        return correlation_summary(int(row["group_id"]))
    finally:
        conn.close()


def list_correlations() -> list[dict]:
    conn = app_db()
    try:
        groups = conn.execute("SELECT id FROM correlation_groups ORDER BY updated_at DESC, id DESC").fetchall()
        return [summary for group in groups if (summary := correlation_summary(int(group["id"])))]
    finally:
        conn.close()


def save_correlation(body: CorrelationLinkPayload) -> dict:
    items = []
    for source in ("zabbix", "netbox", "observium"):
        value = getattr(body, source)
        if value and str(value.id).strip():
            items.append((source, str(value.id).strip(), (value.label or "").strip() or None))
    if len(items) < 2:
        raise HTTPException(400, "At least two platforms are required to create a correlation")

    conn = app_db()
    try:
        existing_group_ids = {
            int(row["group_id"])
            for source, external_id, _ in items
            for row in conn.execute(
                "SELECT group_id FROM correlation_items WHERE source = ? AND external_id = ?",
                (source, external_id),
            ).fetchall()
        }
        if len(existing_group_ids) > 1:
            raise HTTPException(409, "Selected devices already belong to different correlation groups")

        group_id = next(iter(existing_group_ids), None)
        label = (body.label or "").strip() or None
        if group_id is None:
            cursor = conn.execute("INSERT INTO correlation_groups (label) VALUES (?)", (label,))
            group_id = int(cursor.lastrowid)
        else:
            conn.execute(
                "UPDATE correlation_groups SET label = COALESCE(?, label), updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (label, group_id),
            )

        for source, external_id, display_label in items:
            conn.execute(
                """
                INSERT INTO correlation_items (group_id, source, external_id, display_label)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(source, external_id) DO UPDATE SET
                  group_id = excluded.group_id,
                  display_label = excluded.display_label,
                  updated_at = CURRENT_TIMESTAMP
                """,
                (group_id, source, external_id, display_label),
            )

        conn.execute("UPDATE correlation_groups SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (group_id,))
        conn.commit()
        return correlation_summary(group_id) or {}
    finally:
        conn.close()


def remove_correlation_item(group_id: int, source: str) -> dict:
    conn = app_db()
    try:
        deleted = conn.execute(
            "DELETE FROM correlation_items WHERE group_id = ? AND source = ?",
            (group_id, source),
        ).rowcount
        if not deleted:
            raise HTTPException(404, "Correlation item not found")
        remaining = conn.execute(
            "SELECT COUNT(*) AS count FROM correlation_items WHERE group_id = ?",
            (group_id,),
        ).fetchone()
        if not remaining or int(remaining["count"]) < 2:
            conn.execute("DELETE FROM correlation_groups WHERE id = ?", (group_id,))
            conn.execute("DELETE FROM correlation_items WHERE group_id = ?", (group_id,))
            conn.commit()
            return {"status": "deleted", "group_id": group_id}
        conn.execute("UPDATE correlation_groups SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (group_id,))
        conn.commit()
        return {"status": "ok", "correlation": correlation_summary(group_id)}
    finally:
        conn.close()
