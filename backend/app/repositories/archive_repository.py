from __future__ import annotations

import json
from typing import Any

from app.repositories.app_db import app_db


def log_export(
    *,
    action: str,
    source: str,
    target: str,
    status: str,
    entity_id: str | None,
    entity_label: str | None,
    message: str,
    payload: Any = None,
    response: Any = None,
) -> None:
    conn = app_db()
    try:
        conn.execute(
            """
            INSERT INTO export_logs (action, source, target, entity_id, entity_label, status, message, payload_json, response_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                action,
                source,
                target,
                entity_id,
                entity_label,
                status,
                message,
                json.dumps(payload, ensure_ascii=False, default=str) if payload is not None else None,
                json.dumps(response, ensure_ascii=False, default=str) if response is not None else None,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def archived_ids(source: str) -> set[str]:
    conn = app_db()
    try:
        rows = conn.execute("SELECT external_id FROM archives WHERE source = ?", (source,)).fetchall()
        return {str(row["external_id"]) for row in rows}
    finally:
        conn.close()


def archive_status(source: str, external_id: str) -> bool:
    conn = app_db()
    try:
        row = conn.execute(
            "SELECT 1 FROM archives WHERE source = ? AND external_id = ?",
            (source, str(external_id)),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def set_archive(source: str, external_id: str, label: str | None, details: dict[str, Any] | None) -> None:
    conn = app_db()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO archives (source, external_id, label, details_json, archived_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (source, str(external_id), label, json.dumps(details or {}, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()


def clear_archive(source: str, external_id: str) -> None:
    conn = app_db()
    try:
        conn.execute("DELETE FROM archives WHERE source = ? AND external_id = ?", (source, str(external_id)))
        conn.commit()
    finally:
        conn.close()


def list_archives(source: str) -> list[dict[str, Any]]:
    conn = app_db()
    try:
        rows = conn.execute(
            "SELECT source, external_id, label, details_json, archived_at FROM archives WHERE source = ? ORDER BY archived_at DESC",
            (source,),
        ).fetchall()
        return [
            {
                "source": row["source"],
                "external_id": row["external_id"],
                "label": row["label"],
                "details": json.loads(row["details_json"] or "{}"),
                "archived_at": row["archived_at"],
            }
            for row in rows
        ]
    finally:
        conn.close()


def list_export_logs(limit: int = 100) -> list[dict[str, Any]]:
    conn = app_db()
    try:
        rows = conn.execute(
            """
            SELECT id, created_at, action, source, target, entity_id, entity_label, status, message, payload_json, response_json
            FROM export_logs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "action": row["action"],
                "source": row["source"],
                "target": row["target"],
                "entity_id": row["entity_id"],
                "entity_label": row["entity_label"],
                "status": row["status"],
                "message": row["message"],
                "payload": json.loads(row["payload_json"] or "null"),
                "response": json.loads(row["response_json"] or "null"),
            }
            for row in rows
        ]
    finally:
        conn.close()
