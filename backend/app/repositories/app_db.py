import sqlite3
from pathlib import Path

from app.core.settings import cfg


def app_db() -> sqlite3.Connection:
    db_path = Path(cfg.APP_DB_PATH)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_app_db() -> None:
    conn = app_db()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS archives (
              source TEXT NOT NULL,
              external_id TEXT NOT NULL,
              label TEXT,
              details_json TEXT,
              archived_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY (source, external_id)
            );

            CREATE TABLE IF NOT EXISTS export_logs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              action TEXT NOT NULL,
              source TEXT NOT NULL,
              target TEXT NOT NULL,
              entity_id TEXT,
              entity_label TEXT,
              status TEXT NOT NULL,
              message TEXT,
              payload_json TEXT,
              response_json TEXT
            );

            CREATE TABLE IF NOT EXISTS correlation_groups (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              label TEXT,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS correlation_items (
              group_id INTEGER NOT NULL,
              source TEXT NOT NULL,
              external_id TEXT NOT NULL,
              display_label TEXT,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY (source, external_id),
              UNIQUE (group_id, source),
              FOREIGN KEY (group_id) REFERENCES correlation_groups(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS netbox_sync_profiles (
              device_id TEXT PRIMARY KEY,
              enabled INTEGER NOT NULL DEFAULT 0,
              field_sources_json TEXT NOT NULL DEFAULT '{}',
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.commit()
    finally:
        conn.close()
