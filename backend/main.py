import json
import logging
import os
import ipaddress
import shlex
import sqlite3
import re
import urllib.parse
import base64
from pathlib import Path
from typing import Any, Optional
from xml.sax.saxutils import escape as xml_escape

import docker
import httpx
import pymysql
from docker.errors import DockerException
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

app = FastAPI(title="Zabbix-NetBox-Observium Editor", version="1.3.0")
logger = logging.getLogger("uvicorn.error")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class Config:
    ZABBIX_URL: str = os.getenv("ZABBIX_URL", "")
    ZABBIX_TOKEN: str = os.getenv("ZABBIX_TOKEN", "")
    ZABBIX_USER: str = os.getenv("ZABBIX_USER", "")
    ZABBIX_PASS: str = os.getenv("ZABBIX_PASS", "")
    NETBOX_URL: str = os.getenv("NETBOX_URL", "")
    NETBOX_TOKEN: str = os.getenv("NETBOX_TOKEN", "")
    OBSERVIUM_BASE_URL: str = os.getenv("OBSERVIUM_BASE_URL", "")
    OBSERVIUM_DB_HOST: str = os.getenv("OBSERVIUM_DB_HOST", "mariadb")
    OBSERVIUM_DB_PORT: int = int(os.getenv("OBSERVIUM_DB_PORT", "3306"))
    OBSERVIUM_DB_NAME: str = os.getenv("OBSERVIUM_DB_NAME", "")
    OBSERVIUM_DB_USER: str = os.getenv("OBSERVIUM_DB_USER", "")
    OBSERVIUM_DB_PASSWORD: str = os.getenv("OBSERVIUM_DB_PASSWORD", "")
    OBSERVIUM_CONTAINER: str = os.getenv("OBSERVIUM_CONTAINER", "observium-app")
    APP_DB_PATH: str = os.getenv("APP_DB_PATH", "/app/data/zneditor.db")


cfg = Config()
runtime_creds: dict[str, Any] = {}
_zabbix_auth_token: Optional[str] = None
_observium_device_columns_cache: Optional[set[str]] = None


class Credentials(BaseModel):
    zabbix_url: Optional[str] = None
    zabbix_token: Optional[str] = None
    zabbix_user: Optional[str] = None
    zabbix_pass: Optional[str] = None
    netbox_url: Optional[str] = None
    netbox_token: Optional[str] = None


class ArchivePayload(BaseModel):
    label: Optional[str] = None
    details: dict[str, Any] = Field(default_factory=dict)


class BulkArchivePayload(BaseModel):
    source: str
    ids: list[str]
    archive: bool = True


class ObserviumDeviceCreate(BaseModel):
    hostname: str
    snmp_version: str = "v2c"
    snmp_community: Optional[str] = None
    snmp_port: int = 161
    snmp_transport: str = "udp"
    snmp_authlevel: Optional[str] = None
    snmp_authname: Optional[str] = None
    snmp_authpass: Optional[str] = None
    snmp_authalgo: Optional[str] = None
    snmp_cryptopass: Optional[str] = None
    snmp_cryptoalgo: Optional[str] = None
    snmp_context: Optional[str] = None
    label: Optional[str] = None
    location: Optional[str] = None
    purpose: Optional[str] = None
    skip_icmp: bool = False
    run_discovery: bool = True
    run_poller: bool = True


class ExportZabbixToObserviumPayload(BaseModel):
    hostids: list[str]
    run_discovery: bool = True
    run_poller: bool = True
    update_existing: bool = True


class CorrelationItemPayload(BaseModel):
    id: str
    label: Optional[str] = None


class CorrelationLinkPayload(BaseModel):
    label: Optional[str] = None
    zabbix: Optional[CorrelationItemPayload] = None
    netbox: Optional[CorrelationItemPayload] = None
    observium: Optional[CorrelationItemPayload] = None


class NetBoxEnrichmentActionPayload(BaseModel):
    action_type: str
    value: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class NetBoxEnrichmentApplyPayload(BaseModel):
    actions: list[NetBoxEnrichmentActionPayload] = Field(default_factory=list)


class NetBoxPrimaryIpFixPayload(BaseModel):
    dry_run: bool = False


class NetBoxSyncProfilePayload(BaseModel):
    enabled: bool = False
    field_sources: dict[str, str] = Field(default_factory=dict)


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


@app.on_event("startup")
def on_startup() -> None:
    init_app_db()


def get_zabbix_url() -> str:
    return runtime_creds.get("zabbix_url", cfg.ZABBIX_URL).rstrip("/")


def zabbix_host_search_ui_url(host: dict[str, Any]) -> str:
    base = get_zabbix_url()
    interfaces = host.get("interfaces") or []
    preferred = next((item for item in interfaces if str(item.get("main")) == "1"), None) or (interfaces[0] if interfaces else None) or {}
    ip = compact_text(preferred.get("ip")) if str(preferred.get("useip", "1")) == "1" else ""
    dns = compact_text(preferred.get("dns")) if str(preferred.get("useip", "1")) != "1" else ""
    query = urllib.parse.urlencode(
        {
            "name": "" if ip else compact_text(host.get("host")),
            "ip": ip,
            "dns": dns,
            "port": compact_text(preferred.get("port")),
            "status": "-1",
            "evaltype": "0",
            "maintenance_status": "1",
            "filter_name": "",
            "filter_show_counter": "0",
            "filter_custom_time": "0",
            "sort": "name",
            "sortorder": "ASC",
            "show_suppressed": "0",
            "action": "host.view",
        }
    )
    return f"{base}/zabbix.php?{query}&tags%5B0%5D%5Btag%5D=&tags%5B0%5D%5Boperator%5D=0&tags%5B0%5D%5Bvalue%5D="


def get_netbox_url() -> str:
    return runtime_creds.get("netbox_url", cfg.NETBOX_URL).rstrip("/")


def get_netbox_token() -> str:
    return runtime_creds.get("netbox_token", cfg.NETBOX_TOKEN)


def get_observium_base_url() -> str:
    return cfg.OBSERVIUM_BASE_URL.rstrip("/")


def get_observium_db_config() -> dict[str, Any]:
    return {
        "host": cfg.OBSERVIUM_DB_HOST,
        "port": cfg.OBSERVIUM_DB_PORT,
        "user": cfg.OBSERVIUM_DB_USER,
        "password": cfg.OBSERVIUM_DB_PASSWORD,
        "database": cfg.OBSERVIUM_DB_NAME,
        "cursorclass": pymysql.cursors.DictCursor,
        "autocommit": True,
    }


def observium_db_query(query: str, params: tuple[Any, ...] = (), fetch: str = "all") -> Any:
    if not cfg.OBSERVIUM_DB_NAME or not cfg.OBSERVIUM_DB_USER or not cfg.OBSERVIUM_DB_PASSWORD:
        raise HTTPException(400, "Observium DB is not configured")

    connection = pymysql.connect(**get_observium_db_config())
    try:
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            if fetch == "one":
                return cursor.fetchone()
            if fetch == "none":
                return None
            return cursor.fetchall()
    finally:
        connection.close()


def get_docker_client():
    try:
        return docker.from_env()
    except DockerException as exc:
        raise HTTPException(500, f"Docker client unavailable: {exc}") from exc


def observium_container():
    try:
        return get_docker_client().containers.get(cfg.OBSERVIUM_CONTAINER)
    except DockerException as exc:
        raise HTTPException(500, f"Observium container unavailable: {exc}") from exc


def observium_exec(args: list[str]) -> dict[str, Any]:
    logger.info("Observium exec: %s", " ".join(shlex.quote(arg) for arg in args))
    result = observium_container().exec_run(args)
    output = result.output.decode("utf-8", errors="replace")
    if result.exit_code != 0:
        raise HTTPException(400, f"Observium command failed: {output}")
    return {"command": args, "output": output}


def is_ip_address(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except Exception:
        return False


def log_export(
    *,
    action: str,
    source: str,
    target: str,
    status: str,
    entity_id: Optional[str],
    entity_label: Optional[str],
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


def set_archive(source: str, external_id: str, label: Optional[str], details: Optional[dict[str, Any]]) -> None:
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


def apply_archive_mode(source: str, items: list[dict[str, Any]], id_field: str, mode: str) -> list[dict[str, Any]]:
    archived = archived_ids(source)
    filtered: list[dict[str, Any]] = []
    for item in items:
        archived_flag = str(item.get(id_field)) in archived
        item["archived"] = archived_flag
        if mode == "exclude" and archived_flag:
            continue
        if mode == "only" and not archived_flag:
            continue
        filtered.append(item)
    return filtered


SYNC_FIELDS = ("primary_ip4", "serial", "platform")
SYNC_SOURCE_DEFAULTS = {
    "primary_ip4": "zabbix",
    "serial": "zabbix",
    "platform": "observium",
}


def get_netbox_sync_profile(device_id: int) -> dict[str, Any]:
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


def save_netbox_sync_profile(device_id: int, enabled: bool, field_sources: dict[str, str]) -> dict[str, Any]:
    normalized = {field: source for field, source in field_sources.items() if field in SYNC_FIELDS and source in {"zabbix", "observium"}}
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


def extract_ip_like_text(value: Any) -> str:
    text = compact_text(value)
    if not text:
        return ""
    match = re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}(?:/\d{1,2})?\b", text)
    return match.group(0) if match else ""


def netbox_device_inventory_ip(device: dict[str, Any]) -> str:
    return (
        compact_text((device.get("primary_ip4") or {}).get("address"))
        or compact_text((device.get("primary_ip") or {}).get("address"))
        or extract_ip_like_text(device.get("description"))
        or "-"
    )


def drawio_fill_color(device: dict[str, Any]) -> str:
    manufacturer = compact_text(((device.get("device_type") or {}).get("manufacturer") or {}).get("name")).lower()
    model = compact_text((device.get("device_type") or {}).get("display") or device.get("device_type", {}).get("model")).lower()
    platform = compact_text((device.get("platform") or {}).get("name")).lower()
    signature = f"{manufacturer} {model} {platform}"
    if "forti" in signature:
        return "#f5f3ff"
    if "cisco" in signature or "catalyst" in signature:
        return "#ecfeff"
    if "avaya" in signature or "nortel" in signature:
        return "#fff7ed"
    if "watchguard" in signature or "firebox" in signature:
        return "#fef3c7"
    return "#f8fafc"


def drawio_ip_text(device: dict[str, Any]) -> str:
    value = netbox_device_inventory_ip(device)
    if value == "-":
        return value
    return host_part(value)


async def fetch_all_netbox_devices(archived: str = "exclude") -> list[dict[str, Any]]:
    limit = 200
    offset = 0
    collected: list[dict[str, Any]] = []
    while True:
        result = await netbox_request("GET", "/dcim/devices/", {"limit": limit, "offset": offset})
        payload = result.get("result") or {}
        batch = payload.get("results") or []
        if not batch:
            break
        collected.extend(batch)
        if not payload.get("next"):
            break
        offset += limit
    return apply_archive_mode("netbox", collected, "id", archived)


def build_inventory_drawio_xml(devices: list[dict[str, Any]]) -> str:
    modified = "2026-04-07T00:00:00.000Z"
    cols = 5
    width = 250
    height = 165
    gap_x = 30
    gap_y = 30
    start_x = 40
    start_y = 40
    png_path = Path("/app/switch.png")
    if not png_path.exists():
        png_path = Path(__file__).resolve().with_name("switch.png")
    image_url = ""
    if png_path.exists():
        image_url = "data:image/png;base64," + base64.b64encode(png_path.read_bytes()).decode("ascii")
    lines = [
        f'<mxfile host="app.diagrams.net" modified="{modified}" agent="ZNEditor" version="24.7.17">',
        '  <diagram id="inventory-netbox" name="NetBox Inventory Raw">',
        '    <mxGraphModel dx="1600" dy="1200" grid="1" gridSize="10" guides="1" tooltips="1" connect="0" arrows="0" fold="1" page="1" pageScale="1" pageWidth="1450" pageHeight="1030" math="0" shadow="0">',
        "      <root>",
        '        <mxCell id="0" />',
        '        <mxCell id="1" parent="0" />',
    ]
    cell_id = 2
    for index, device in enumerate(devices):
        x = start_x + (index % cols) * (width + gap_x)
        y = start_y + (index // cols) * (height + gap_y)
        role = compact_text((device.get("role") or {}).get("name")) or "-"
        model = compact_text((device.get("device_type") or {}).get("display")) or "-"
        value = "<div style='text-align:center;line-height:1.35;'>%s</div>" % "<br>".join(
            [
                xml_escape(compact_text(device.get("name")) or f"Device {device.get('id')}", {chr(34): "&quot;", chr(39): "&apos;"}),
                xml_escape(drawio_ip_text(device), {chr(34): "&quot;", chr(39): "&apos;"}),
                xml_escape(model, {chr(34): "&quot;", chr(39): "&apos;"}),
                xml_escape(role, {chr(34): "&quot;", chr(39): "&apos;"}),
            ]
        )
        lines.append(
            f'        <mxCell id="{cell_id}" value="" style="shape=image;verticalLabelPosition=bottom;verticalAlign=top;imageAspect=0;aspect=fixed;image={image_url};fillColor={drawio_fill_color(device)};strokeColor=none;" '
            f'vertex="1" parent="1"><mxGeometry x="{x + 55}" y="{y + 8}" width="140" height="58" as="geometry" /></mxCell>'
        )
        cell_id += 1
        lines.append(
            f'        <mxCell id="{cell_id}" value="{value}" '
            f'style="rounded=1;whiteSpace=wrap;html=1;fillColor={drawio_fill_color(device)};strokeColor=#475569;fontSize=12;fontFamily=Helvetica;align=center;verticalAlign=middle;spacing=8;" '
            f'vertex="1" parent="1"><mxGeometry x="{x}" y="{y + 70}" width="{width}" height="92" as="geometry" /></mxCell>'
        )
        cell_id += 1
    lines.extend(["      </root>", "    </mxGraphModel>", "  </diagram>", "</mxfile>"])
    return "\n".join(lines) + "\n"


def correlation_summary(group_id: int) -> Optional[dict[str, Any]]:
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


def find_correlation_by_item(source: str, external_id: str) -> Optional[dict[str, Any]]:
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


def list_correlations() -> list[dict[str, Any]]:
    conn = app_db()
    try:
        groups = conn.execute("SELECT id FROM correlation_groups ORDER BY updated_at DESC, id DESC").fetchall()
        results: list[dict[str, Any]] = []
        for group in groups:
            summary = correlation_summary(int(group["id"]))
            if summary:
                results.append(summary)
        return results
    finally:
        conn.close()


def save_correlation(body: CorrelationLinkPayload) -> dict[str, Any]:
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


def remove_correlation_item(group_id: int, source: str) -> dict[str, Any]:
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


def is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, dict, set, tuple)):
        return len(value) == 0
    return False


def compact_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def normalize_key(value: Any) -> str:
    normalized = compact_text(value).lower()
    return "".join(char if char.isalnum() else " " for char in normalized).strip()


def slugify_text(value: Any) -> str:
    normalized = normalize_key(value).replace(" ", "-")
    return normalized.strip("-") or "auto-generated"


def normalize_ip_value(address: str) -> str:
    trimmed = compact_text(address)
    if not trimmed:
        return ""
    if "/" in trimmed:
        return trimmed
    return f"{trimmed}/128" if ":" in trimmed else f"{trimmed}/32"


def host_part(address: str) -> str:
    return compact_text(address).split("/")[0]


def ipv4_candidate(value: Any) -> Optional[str]:
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


def description_ip_candidate(device: dict[str, Any]) -> Optional[str]:
    return ipv4_candidate(device.get("description"))


def extract_main_zabbix_ip(host: dict[str, Any]) -> Optional[str]:
    interfaces = host.get("interfaces") or []
    preferred = next((item for item in interfaces if str(item.get("main")) == "1"), None) or (interfaces[0] if interfaces else None)
    if not preferred:
        return None
    raw = preferred.get("ip") if str(preferred.get("useip", "1")) == "1" else preferred.get("dns")
    return ipv4_candidate(raw)


def extract_observium_ipv4(device: dict[str, Any]) -> Optional[str]:
    return ipv4_candidate(device.get("ip"))


def build_enriched_comments(device: dict[str, Any], zabbix_host: Optional[dict[str, Any]], observium_device: Optional[dict[str, Any]]) -> str:
    lines: list[str] = []
    sys_name = compact_text((observium_device or {}).get("sysName"))
    location = compact_text((observium_device or {}).get("location") or ((zabbix_host or {}).get("inventory") or {}).get("location"))
    version = compact_text((observium_device or {}).get("version") or (zabbix_host or {}).get("vendor_version"))
    os_name = compact_text((observium_device or {}).get("os"))
    hardware = compact_text((observium_device or {}).get("hardware"))

    if sys_name:
        lines.append(f"sysName: {sys_name}")
    if location:
        lines.append(f"SNMP location: {location}")
    if hardware or os_name or version:
        parts = [part for part in [hardware, os_name, version] if part]
        lines.append("Platform detail: " + " | ".join(parts))

    current = compact_text(device.get("comments"))
    if current:
        lines.insert(0, current)
    return "\n".join(dict.fromkeys(line for line in lines if line))


def build_enriched_description(device: dict[str, Any], zabbix_host: Optional[dict[str, Any]], observium_device: Optional[dict[str, Any]]) -> str:
    hardware = compact_text((observium_device or {}).get("hardware"))
    version = compact_text((observium_device or {}).get("version"))
    os_name = compact_text((observium_device or {}).get("os"))
    visible_name = compact_text((zabbix_host or {}).get("name"))
    parts = [part for part in [hardware, os_name, version] if part]
    if parts:
        return " | ".join(parts)
    if visible_name and visible_name != compact_text((zabbix_host or {}).get("host")):
        return visible_name
    return compact_text(device.get("description"))


def build_os_version_label(os_name: Optional[str], version: Optional[str]) -> str:
    os_value = compact_text(os_name)
    version_value = compact_text(version)
    return compact_text(" ".join(part for part in (os_value, version_value) if part))


def build_zabbix_platform_label(host: Optional[dict[str, Any]]) -> str:
    inventory = (host or {}).get("inventory") or {}
    return build_os_version_label(
        inventory.get("os_full") or inventory.get("os") or (host or {}).get("vendor_name"),
        (host or {}).get("vendor_version") or inventory.get("os_short") or inventory.get("software_full") or inventory.get("software"),
    )


def build_observium_platform_label(device: Optional[dict[str, Any]]) -> str:
    return build_os_version_label((device or {}).get("os"), (device or {}).get("version"))


def pick_platform_name(device: dict[str, Any], zabbix_host: Optional[dict[str, Any]], observium_device: Optional[dict[str, Any]]) -> Optional[str]:
    return (
        build_observium_platform_label(observium_device)
        or build_zabbix_platform_label(zabbix_host)
        or compact_text((device.get("platform") or {}).get("display") or (device.get("platform") or {}).get("name"))
        or None
    )


async def netbox_paginated(path: str, params: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    query = dict(params or {})
    query.setdefault("limit", 500)
    result = await netbox_request("GET", path, query)
    payload = result.get("result")
    if isinstance(payload, dict) and "results" in payload:
        return payload.get("results", [])
    if isinstance(payload, list):
        return payload
    return []


async def netbox_find_platform(name: str) -> Optional[dict[str, Any]]:
    target = normalize_key(name)
    for item in await netbox_paginated("/dcim/platforms/"):
        if normalize_key(item.get("name")) == target or normalize_key(item.get("display")) == target:
            return item
    return None


async def ensure_netbox_platform(name: str) -> dict[str, Any]:
    existing = await netbox_find_platform(name)
    if existing:
        return {"created": False, "platform": existing}
    created = await netbox_request("POST", "/dcim/platforms/", {"name": name, "slug": slugify_text(name)[:100]})
    return {"created": True, "platform": created.get("result") or created.get("response") or {}}


async def netbox_find_location(name: str, site_id: Optional[int]) -> Optional[dict[str, Any]]:
    params: dict[str, Any] = {}
    if site_id:
        params["site_id"] = site_id
    target = normalize_key(name)
    for item in await netbox_paginated("/dcim/locations/", params):
        if normalize_key(item.get("name")) == target or normalize_key(item.get("display")) == target:
            return item
    return None


async def ensure_netbox_location(name: str, site_id: Optional[int]) -> dict[str, Any]:
    existing = await netbox_find_location(name, site_id)
    if existing:
        return {"created": False, "location": existing}
    if not site_id:
        raise HTTPException(400, "NetBox site is required to create a location")
    created = await netbox_request(
        "POST",
        "/dcim/locations/",
        {"name": name, "slug": slugify_text(name)[:90], "site": int(site_id)},
    )
    return {"created": True, "location": created.get("result") or created.get("response") or {}}


async def zabbix_host_items(host_id: str) -> list[dict[str, Any]]:
    result = await zabbix_request(
        "item.get",
        {
            "output": ["itemid", "hostid", "name", "key_", "snmp_oid", "lastvalue", "value_type", "status", "state", "error"],
            "hostids": [host_id],
            "filter": {"status": 0},
        },
    )
    return result.get("result") or []


async def zabbix_global_macros() -> list[dict[str, Any]]:
    result = await zabbix_request("usermacro.get", {"output": "extend", "globalmacro": True})
    return result.get("result") or []


async def find_zabbix_serial_item(host_id: str) -> Optional[dict[str, Any]]:
    candidates = await zabbix_host_items(host_id)
    for item in candidates:
        key_name = compact_text(item.get("key_")).lower()
        item_name = compact_text(item.get("name")).lower()
        snmp_oid = compact_text(item.get("snmp_oid"))
        if "entphysicalserialnum" in key_name or "entphysicalserialnum" in item_name or ".1.3.6.1.2.1.47.1.1.1.1.11.1" in snmp_oid:
            return item
    return None


async def ensure_zabbix_serial_item(host_id: str) -> dict[str, Any]:
    existing = await find_zabbix_serial_item(host_id)
    if existing:
        return {"status": "exists", "item": existing}

    host_result = await zabbix_request("host.get", {"output": ["hostid"], "hostids": [host_id], "selectInterfaces": "extend"})
    if not host_result.get("result"):
        raise HTTPException(404, "Zabbix host not found")
    host = host_result["result"][0]
    interface = next((item for item in host.get("interfaces", []) if str(item.get("type")) == "2"), None)
    if not interface:
        raise HTTPException(400, "No SNMP interface available on the correlated Zabbix host")

    created = await zabbix_request(
        "item.create",
        {
            "hostid": host_id,
            "interfaceid": interface["interfaceid"],
            "type": 20,
            "value_type": 4,
            "name": "SNMP entPhysicalSerialNum",
            "key_": "snmp.entPhysicalSerialNum",
            "snmp_oid": ".1.3.6.1.2.1.47.1.1.1.1.11.1",
            "delay": "1h",
        },
    )
    return {"status": "created", "result": created.get("result")}


def serial_candidate_from_zabbix(host: Optional[dict[str, Any]], serial_item: Optional[dict[str, Any]]) -> Optional[str]:
    inventory = (host or {}).get("inventory") or {}
    for key in ("serialno_a", "serialno_b", "serialno"):
        candidate = compact_text(inventory.get(key))
        if candidate:
            return candidate
    latest = compact_text((serial_item or {}).get("lastvalue"))
    if latest:
        return latest
    return None


def observium_device_columns() -> set[str]:
    global _observium_device_columns_cache
    if _observium_device_columns_cache is None:
        rows = observium_db_query("SHOW COLUMNS FROM devices")
        _observium_device_columns_cache = {str(row["Field"]) for row in rows}
    return _observium_device_columns_cache


def serial_candidate_from_observium(device: Optional[dict[str, Any]]) -> Optional[str]:
    return compact_text((device or {}).get("serial")) or None


def observium_device_row(device_id: int) -> Optional[dict[str, Any]]:
    fields = [
        "device_id", "hostname", "sysName", "label", "ip",
        "snmp_version", "snmp_community", "snmp_port", "snmp_transport",
        "snmp_authlevel", "snmp_authname", "snmp_authalgo", "snmp_context",
        "status", "disabled", "`ignore`", "location", "purpose", "os", "vendor", "hardware", "version",
    ]
    if "serial" in observium_device_columns():
        fields.append("serial")
    row = observium_db_query(
        f"""
        SELECT
            {", ".join(fields)}
        FROM devices
        WHERE device_id = %s
        """,
        (device_id,),
        fetch="one",
    )
    if not row:
        return None
    return normalize_observium_device(row)


async def correlated_sources_for_netbox_device(device_id: int) -> dict[str, Any]:
    correlation = find_correlation_by_item("netbox", str(device_id))
    if not correlation:
        raise HTTPException(404, "No saved correlation exists for this NetBox device")

    items = correlation.get("items") or {}
    zabbix_host = None
    observium_device = None

    if items.get("zabbix"):
        global_macros = await zabbix_global_macros()
        zabbix_result = await zabbix_request(
            "host.get",
            {
                "output": "extend",
                "hostids": [items["zabbix"]["id"]],
                "selectInterfaces": "extend",
                "selectInventory": "extend",
                "selectTags": "extend",
                "selectMacros": "extend",
            },
        )
        zabbix_host = (zabbix_result.get("result") or [None])[0]
        if zabbix_host is not None:
            zabbix_host["globalmacros"] = global_macros

    if items.get("observium"):
        observium_device = observium_device_row(int(items["observium"]["id"]))

    return {"correlation": correlation, "zabbix": zabbix_host, "observium": observium_device}


async def choose_primary_interface(device_id: int) -> dict[str, Any]:
    interfaces = await netbox_paginated("/dcim/interfaces/", {"device_id": device_id})
    if interfaces:
        def score(item: dict[str, Any]) -> tuple[int, int]:
            name = compact_text(item.get("name")).lower()
            if any(token in name for token in ("mgmt", "management", "oob")):
                return (0, 0)
            if "vlan1" in name or "vlan 1" in name:
                return (1, 0)
            return (2, int(not item.get("enabled", True)))
        return sorted(interfaces, key=score)[0]

    created = await netbox_request(
        "POST",
        "/dcim/interfaces/",
        {"device": int(device_id), "name": "mgmt0", "type": "virtual", "enabled": True},
    )
    return created.get("result") or created.get("response") or {}


async def ip_assigned_device(ip_payload: dict[str, Any]) -> Optional[int]:
    assigned_object = ip_payload.get("assigned_object") or {}
    interface_url = assigned_object.get("url")
    if interface_url:
        iface_result = await netbox_request("GET", interface_url.replace(f"{get_netbox_url()}/api", ""))
        iface = iface_result.get("result") or {}
        device = iface.get("device") or {}
        if device.get("id") is not None:
            return int(device["id"])
    return None


async def ensure_netbox_primary_ip4(device: dict[str, Any], address: str, status: str = "active") -> dict[str, Any]:
    normalized = normalize_ip_value(address)
    if not normalized:
        raise HTTPException(400, "IPv4 candidate is empty")

    chosen_interface = await choose_primary_interface(int(device["id"]))
    search_results = await netbox_paginated("/ipam/ip-addresses/", {"address": host_part(normalized), "limit": 50})

    reusable = None
    conflict = None
    for candidate in search_results:
        if host_part(candidate.get("address", "")) != host_part(normalized):
            continue
        assigned_device_id = await ip_assigned_device(candidate) if candidate.get("assigned_object") else None
        if assigned_device_id in {None, int(device["id"])}:
            reusable = candidate
            break
        conflict = candidate

    if conflict and reusable is None:
        raise HTTPException(409, f"IP {host_part(normalized)} is already assigned to another NetBox device")

    if reusable:
        update_payload = {
            "address": normalized,
            "status": status,
            "assigned_object_type": "dcim.interface",
            "assigned_object_id": int(chosen_interface["id"]),
        }
        await netbox_request("PATCH", f"/ipam/ip-addresses/{int(reusable['id'])}/", update_payload)
        ip_id = int(reusable["id"])
        created = False
    else:
        created_result = await netbox_request(
            "POST",
            "/ipam/ip-addresses/",
            {
                "address": normalized,
                "status": status,
                "assigned_object_type": "dcim.interface",
                "assigned_object_id": int(chosen_interface["id"]),
            },
        )
        created_ip = created_result.get("result") or created_result.get("response") or {}
        ip_id = int(created_ip["id"])
        created = True

    await netbox_set_primary_ip(int(device["id"]), {"ip_id": ip_id})
    return {"ip_id": ip_id, "address": normalized, "created": created, "interface": chosen_interface}


async def build_netbox_enrichment(device_id: int) -> dict[str, Any]:
    device_result = await netbox_request("GET", f"/dcim/devices/{device_id}/")
    device = device_result.get("result") or {}
    sources = await correlated_sources_for_netbox_device(device_id)
    zabbix_host = sources["zabbix"]
    observium_device = sources["observium"]
    serial_item = await find_zabbix_serial_item(str(zabbix_host["hostid"])) if zabbix_host else None

    platform_name = pick_platform_name(device, zabbix_host, observium_device)
    location_name = compact_text((observium_device or {}).get("location") or ((zabbix_host or {}).get("inventory") or {}).get("location"))
    zabbix_ip = extract_main_zabbix_ip(zabbix_host or {})
    observium_ip = extract_observium_ipv4(observium_device or {})
    description_ip = description_ip_candidate(device)
    serial_candidate = serial_candidate_from_zabbix(zabbix_host, serial_item)

    suggestions: list[dict[str, Any]] = []

    if is_blank(device.get("platform")) and platform_name:
        existing_platform = await netbox_find_platform(platform_name)
        suggestions.append(
            {
                "id": "platform_from_monitoring",
                "action_type": "set_platform",
                "field": "platform",
                "label": "Map platform from Observium/Zabbix",
                "current_value": (device.get("platform") or {}).get("display") or "",
                "proposed_value": platform_name,
                "source": "observium",
                "confidence": "medium",
                "requires_create": not bool(existing_platform),
                "target_section": "DCIM > Platforms",
            }
        )

    if is_blank(device.get("location")) and location_name:
        existing_location = await netbox_find_location(location_name, (device.get("site") or {}).get("id"))
        suggestions.append(
            {
                "id": "location_from_monitoring",
                "action_type": "set_location",
                "field": "location",
                "label": "Map location from SNMP location",
                "current_value": (device.get("location") or {}).get("display") or "",
                "proposed_value": location_name,
                "source": "observium",
                "confidence": "medium",
                "requires_create": not bool(existing_location),
                "target_section": "DCIM > Locations",
            }
        )

    if is_blank(device.get("primary_ip4")) and zabbix_ip:
        suggestions.append(
            {
                "id": "primary_ip4_from_zabbix",
                "action_type": "set_primary_ip4",
                "field": "primary_ip4",
                "label": "Set primary IPv4 from Zabbix",
                "current_value": (device.get("primary_ip4") or {}).get("address") or "",
                "proposed_value": zabbix_ip,
                "source": "zabbix",
                "confidence": "high",
                "requires_create": True,
                "target_section": "IPAM > IP Addresses",
            }
        )
    elif is_blank(device.get("primary_ip4")) and observium_ip:
        suggestions.append(
            {
                "id": "primary_ip4_from_observium",
                "action_type": "set_primary_ip4",
                "field": "primary_ip4",
                "label": "Set primary IPv4 from Observium",
                "current_value": (device.get("primary_ip4") or {}).get("address") or "",
                "proposed_value": observium_ip,
                "source": "observium",
                "confidence": "medium",
                "requires_create": True,
                "target_section": "IPAM > IP Addresses",
            }
        )

    if description_ip and is_blank(device.get("primary_ip4")):
        suggestions.append(
            {
                "id": "migrate_description_ip",
                "action_type": "migrate_description_ip",
                "field": "primary_ip4",
                "label": "Move IP stored in description to primary IPv4",
                "current_value": compact_text(device.get("description")),
                "proposed_value": description_ip,
                "source": "netbox",
                "confidence": "high",
                "requires_create": True,
                "target_section": "IPAM > IP Addresses",
            }
        )

    enriched_description = build_enriched_description(device, zabbix_host, observium_device)
    if (is_blank(device.get("description")) or description_ip) and compact_text(enriched_description):
        suggestions.append(
            {
                "id": "description_from_monitoring",
                "action_type": "set_description",
                "field": "description",
                "label": "Enrich description from monitoring data",
                "current_value": compact_text(device.get("description")),
                "proposed_value": enriched_description,
                "source": "observium",
                "confidence": "medium",
                "requires_create": False,
                "target_section": "Device field",
            }
        )

    enriched_comments = build_enriched_comments(device, zabbix_host, observium_device)
    if is_blank(device.get("comments")) and compact_text(enriched_comments):
        suggestions.append(
            {
                "id": "comments_from_monitoring",
                "action_type": "set_comments",
                "field": "comments",
                "label": "Generate comments from SNMP metadata",
                "current_value": compact_text(device.get("comments")),
                "proposed_value": enriched_comments,
                "source": "observium",
                "confidence": "medium",
                "requires_create": False,
                "target_section": "Device field",
            }
        )

    if is_blank(device.get("serial")) and serial_candidate:
        suggestions.append(
            {
                "id": "serial_from_zabbix",
                "action_type": "set_serial",
                "field": "serial",
                "label": "Sync serial from Zabbix",
                "current_value": compact_text(device.get("serial")),
                "proposed_value": serial_candidate,
                "source": "zabbix",
                "confidence": "medium",
                "requires_create": False,
                "target_section": "Device field",
            }
        )

    if is_blank(device.get("serial")) and zabbix_host and not serial_candidate:
        suggestions.append(
            {
                "id": "ensure_zabbix_serial_item",
                "action_type": "ensure_zabbix_serial_item",
                "field": "serial",
                "label": "Create Zabbix SNMP item for entPhysicalSerialNum",
                "current_value": "",
                "proposed_value": ".1.3.6.1.2.1.47.1.1.1.1.11.1",
                "source": "zabbix",
                "confidence": "medium",
                "requires_create": False,
                "target_section": "Zabbix SNMP item",
                "note": "Value will populate after the next Zabbix poll if the device exposes entPhysicalSerialNum.1",
            }
        )

    return {
        "device": device,
        "correlation": sources["correlation"],
        "sources": {
            "zabbix": {
                "hostid": str(zabbix_host.get("hostid")) if zabbix_host else None,
                "host": (zabbix_host or {}).get("host"),
                "name": (zabbix_host or {}).get("name"),
                "main_ip": zabbix_ip,
            } if zabbix_host else None,
            "observium": {
                "device_id": str(observium_device.get("device_id")) if observium_device else None,
                "hostname": (observium_device or {}).get("hostname"),
                "sysName": (observium_device or {}).get("sysName"),
                "ip": observium_ip,
                "location": (observium_device or {}).get("location"),
                "os": (observium_device or {}).get("os"),
                "vendor": (observium_device or {}).get("vendor"),
                "hardware": (observium_device or {}).get("hardware"),
                "version": (observium_device or {}).get("version"),
            } if observium_device else None,
        },
        "manual_fields": {
            "asset_tag": compact_text(device.get("asset_tag")),
            "serial": compact_text(device.get("serial")),
        },
        "suggestions": suggestions,
    }


async def build_netbox_sync_preview(device_id: int) -> dict[str, Any]:
    device_result = await netbox_request("GET", f"/dcim/devices/{device_id}/")
    device = device_result.get("result") or {}
    sources = await correlated_sources_for_netbox_device(device_id)
    zabbix_host = sources["zabbix"]
    observium_device = sources["observium"]
    serial_item = await find_zabbix_serial_item(str(zabbix_host["hostid"])) if zabbix_host else None
    profile = get_netbox_sync_profile(device_id)

    candidates = {
        "primary_ip4": {
            "zabbix": extract_main_zabbix_ip(zabbix_host or {}),
            "observium": extract_observium_ipv4(observium_device or {}),
        },
        "serial": {
            "zabbix": serial_candidate_from_zabbix(zabbix_host, serial_item),
            "observium": serial_candidate_from_observium(observium_device),
        },
        "platform": {
            "zabbix": pick_platform_name(device, zabbix_host, None),
            "observium": pick_platform_name(device, None, observium_device),
        },
    }

    current_values = {
        "primary_ip4": compact_text((device.get("primary_ip4") or {}).get("address")),
        "serial": compact_text(device.get("serial")),
        "platform": compact_text((device.get("platform") or {}).get("display") or (device.get("platform") or {}).get("name")),
    }

    fields: dict[str, Any] = {}
    for field in SYNC_FIELDS:
        selected_source = profile["field_sources"].get(field) or SYNC_SOURCE_DEFAULTS[field]
        fields[field] = {
            "selected_source": selected_source,
            "current_value": current_values.get(field) or "",
            "candidates": {
                source: compact_text(value)
                for source, value in candidates[field].items()
            },
        }

    return {
        "device": device,
        "correlation": sources["correlation"],
        "profile": profile,
        "sources": {
            "zabbix": {
                "hostid": str(zabbix_host.get("hostid")) if zabbix_host else None,
                "host": (zabbix_host or {}).get("host"),
                "name": (zabbix_host or {}).get("name"),
            } if zabbix_host else None,
            "observium": {
                "device_id": str(observium_device.get("device_id")) if observium_device else None,
                "hostname": (observium_device or {}).get("hostname"),
                "sysName": (observium_device or {}).get("sysName"),
            } if observium_device else None,
        },
        "fields": fields,
    }


async def apply_netbox_sync(device_id: int, enabled: bool, field_sources: dict[str, str]) -> dict[str, Any]:
    profile = save_netbox_sync_profile(device_id, enabled, field_sources)
    preview = await build_netbox_sync_preview(device_id)
    device = preview["device"]
    results: list[dict[str, Any]] = []

    for field in SYNC_FIELDS:
        source = profile["field_sources"].get(field) or SYNC_SOURCE_DEFAULTS[field]
        if source not in {"zabbix", "observium"}:
            results.append({"field": field, "status": "skipped", "reason": "Invalid source"})
            continue
        value = compact_text(preview["fields"][field]["candidates"].get(source))
        if not value:
            results.append({"field": field, "status": "skipped", "reason": f"No value available from {source}"})
            continue
        try:
            if field == "primary_ip4":
                applied = await ensure_netbox_primary_ip4(device, value)
                results.append({"field": field, "status": "updated", "source": source, **applied})
            elif field == "serial":
                await netbox_request("PATCH", f"/dcim/devices/{device_id}/", {"serial": value})
                results.append({"field": field, "status": "updated", "source": source, "value": value})
            elif field == "platform":
                ensured = await ensure_netbox_platform(value)
                await netbox_request("PATCH", f"/dcim/devices/{device_id}/", {"platform": int(ensured["platform"]["id"])})
                results.append({"field": field, "status": "updated", "source": source, "value": value, "created": ensured["created"]})
        except HTTPException as exc:
            results.append({"field": field, "status": "error", "source": source, "error": exc.detail})

    return {"profile": profile, "results": results, "preview": await build_netbox_sync_preview(device_id)}


def first_non_empty(data: dict[str, Any], *keys: str) -> Optional[str]:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def zabbix_macro_value(host: dict[str, Any], name: Optional[str]) -> Optional[str]:
    target = compact_text(name)
    if not target:
        return None
    for macro in host.get("macros", []):
        if compact_text(macro.get("macro")) == target:
            return compact_text(macro.get("value"))
    for macro in host.get("globalmacros", []):
        if compact_text(macro.get("macro")) == target:
            return compact_text(macro.get("value"))
    return None


def resolve_zabbix_macro_text(host: dict[str, Any], value: Optional[str]) -> Optional[str]:
    text = compact_text(value)
    if not text:
        return None
    if re.fullmatch(r"\{\$[^}]+\}", text):
        return zabbix_macro_value(host, text) or text
    return text


def map_security_level(raw: Optional[str]) -> str:
    value = (raw or "").strip().lower()
    mapping = {
        "0": "nanp",
        "1": "anp",
        "2": "ap",
        "noauthnopriv": "nanp",
        "authnopriv": "anp",
        "authpriv": "ap",
        "nanp": "nanp",
        "anp": "anp",
        "ap": "ap",
    }
    return mapping.get(value, "ap")


def map_auth_protocol(raw: Optional[str]) -> Optional[str]:
    value = (raw or "").strip().lower()
    mapping = {"0": "MD5", "1": "SHA", "md5": "MD5", "sha": "SHA"}
    return mapping.get(value)


def map_priv_protocol(raw: Optional[str]) -> Optional[str]:
    value = (raw or "").strip().lower()
    mapping = {"0": "DES", "1": "AES", "des": "DES", "aes": "AES"}
    return mapping.get(value)


def normalize_zabbix_snmp(interface: dict[str, Any], host: dict[str, Any]) -> Optional[dict[str, Any]]:
    details = interface.get("details") or {}
    version = str(details.get("version", "")).strip()
    community = resolve_zabbix_macro_text(host, details.get("community"))
    if not community:
        community = zabbix_macro_value(host, "{$SNMP_COMMUNITY}")

    if version == "1" and community:
        return {"version": "v1", "community": community}
    if version == "2" and community:
        return {"version": "v2c", "community": community}
    if version == "3":
        security_name = first_non_empty(details, "securityname", "security_name")
        if not security_name:
            return None
        return {
            "version": "v3",
            "security_name": security_name,
            "security_level": map_security_level(first_non_empty(details, "securitylevel", "security_level")),
            "auth_protocol": map_auth_protocol(first_non_empty(details, "authprotocol", "auth_protocol")),
            "auth_password": first_non_empty(details, "authpassphrase", "auth_password", "authpass"),
            "priv_protocol": map_priv_protocol(first_non_empty(details, "privprotocol", "priv_protocol")),
            "priv_password": first_non_empty(details, "privpassphrase", "priv_password", "privpass"),
        }
    return None


def extract_zabbix_snmp_host(host: dict[str, Any]) -> dict[str, Any]:
    for interface in host.get("interfaces", []):
        if interface.get("type") != "2":
            continue
        address = interface.get("ip") if interface.get("useip") == "1" else interface.get("dns")
        if not address:
            continue
        snmp = normalize_zabbix_snmp(interface, host)
        if not snmp:
            continue
        return {
            "hostid": str(host["hostid"]),
            "host": host.get("host") or address,
            "name": host.get("name") or host.get("host") or address,
            "address": address,
            "port": int(interface.get("port") or 161),
            "interface": interface,
            "snmp": snmp,
            "inventory": host.get("inventory") or {},
        }
    raise HTTPException(400, f"Host {host.get('host')} has no usable SNMP interface")


def normalize_observium_device(row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    row["disabled"] = bool(row.get("disabled"))
    row["ignore"] = bool(row.get("ignore"))
    row["skip_icmp"] = bool(row.get("skip_icmp") or row.get("disable_icmp") or row.get("icmp_disable"))
    row["status"] = int(row.get("status") or 0)
    if get_observium_base_url():
        row["web_url"] = f"{get_observium_base_url()}/device/device={row['device_id']}/"
    return row


def observium_optional_icmp_column() -> Optional[str]:
    try:
        rows = observium_db_query(
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


def build_observium_add_command(device: ObserviumDeviceCreate) -> list[str]:
    base = ["php", "/opt/observium/add_device.php", device.hostname]
    if device.snmp_version == "v3":
        authlevel = device.snmp_authlevel or "any"
        base.extend([authlevel, "v3", device.snmp_authname or ""])
        if authlevel in {"anp", "ap"}:
            base.append(device.snmp_authpass or "")
            base.append(device.snmp_authalgo or "SHA")
        if authlevel == "ap":
            base.append(device.snmp_cryptopass or "")
            base.append(device.snmp_cryptoalgo or "AES")
        base.extend([str(device.snmp_port), device.snmp_transport])
        if device.snmp_context:
            base.append(device.snmp_context)
        return base
    return base + [device.snmp_community or "", device.snmp_version, str(device.snmp_port), device.snmp_transport]


def observium_refresh_device(device_id: int) -> dict[str, Any]:
    current = observium_db_query("SELECT hostname FROM devices WHERE device_id = %s", (device_id,), fetch="one")
    if not current:
        raise HTTPException(404, "Observium device not found")

    discovery = observium_exec(["php", "/opt/observium/discovery.php", "-h", str(device_id)])
    poller = observium_exec(["php", "/opt/observium/poller.php", "-h", str(device_id)])
    return {"device_id": device_id, "hostname": current["hostname"], "discovery": discovery, "poller": poller}


def observium_snmp_check(address: str, port: int, snmp: dict[str, Any]) -> dict[str, Any]:
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
    return observium_exec(args)


async def zabbix_authenticate() -> str:
    global _zabbix_auth_token
    token = runtime_creds.get("zabbix_token", cfg.ZABBIX_TOKEN)
    if token:
        _zabbix_auth_token = token
        return token

    user = runtime_creds.get("zabbix_user", cfg.ZABBIX_USER)
    passwd = runtime_creds.get("zabbix_pass", cfg.ZABBIX_PASS)
    if not user or not passwd:
        raise HTTPException(400, "No Zabbix credentials configured")

    payload = {"jsonrpc": "2.0", "method": "user.login", "params": {"username": user, "password": passwd}, "id": 1}
    async with httpx.AsyncClient(verify=False, timeout=30) as client:
        response = await client.post(f"{get_zabbix_url()}/api_jsonrpc.php", json=payload)
        data = response.json()
    if "error" in data:
        raise HTTPException(401, f"Zabbix auth failed: {data['error']['data']}")
    _zabbix_auth_token = data["result"]
    return _zabbix_auth_token


async def zabbix_request(method: str, params: dict[str, Any]) -> Any:
    auth = await zabbix_authenticate()
    token = runtime_creds.get("zabbix_token", cfg.ZABBIX_TOKEN)
    payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
    headers = {"Content-Type": "application/json-rpc"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    else:
        payload["auth"] = auth

    async with httpx.AsyncClient(verify=False, timeout=30) as client:
        response = await client.post(f"{get_zabbix_url()}/api_jsonrpc.php", json=payload, headers=headers)
        data = response.json()
    if "error" in data:
        raise HTTPException(400, f"Zabbix error: {data['error']['data']}")
    return {"request": payload, "response": data, "result": data.get("result")}


async def netbox_request(method: str, path: str, body: Optional[dict[str, Any]] = None) -> Any:
    token = get_netbox_token()
    if not token:
        raise HTTPException(400, "No NetBox token configured")
    headers = {"Authorization": f"Token {token}", "Content-Type": "application/json", "Accept": "application/json"}
    url = f"{get_netbox_url()}/api{path}"

    async with httpx.AsyncClient(verify=False, timeout=30) as client:
        if method == "GET":
            response = await client.get(url, headers=headers, params=body or {})
        elif method == "POST":
            response = await client.post(url, headers=headers, json=body or {})
        elif method == "PATCH":
            response = await client.patch(url, headers=headers, json=body or {})
        elif method == "PUT":
            response = await client.put(url, headers=headers, json=body or {})
        elif method == "DELETE":
            response = await client.delete(url, headers=headers)
            return {"status": response.status_code, "request": {"method": method, "url": url, "body": body}, "response": {}}
        else:
            raise HTTPException(400, f"Unsupported method: {method}")

    try:
        response_data = response.json()
    except Exception:
        response_data = {"raw": response.text}
    if response.status_code >= 400:
        raise HTTPException(response.status_code, f"NetBox error: {response_data}")
    return {"request": {"method": method, "url": url, "body": body}, "response": response_data, "result": response_data}


@app.post("/api/credentials")
async def set_credentials(creds: Credentials):
    global runtime_creds
    runtime_creds = creds.dict(exclude_none=True)
    return {"status": "ok", "fields": list(runtime_creds.keys())}


@app.get("/api/credentials")
async def get_credentials():
    return {
        "zabbix_url": runtime_creds.get("zabbix_url", cfg.ZABBIX_URL),
        "zabbix_token": "***" if (runtime_creds.get("zabbix_token") or cfg.ZABBIX_TOKEN) else "",
        "zabbix_user": runtime_creds.get("zabbix_user", cfg.ZABBIX_USER),
        "netbox_url": runtime_creds.get("netbox_url", cfg.NETBOX_URL),
        "netbox_token": "***" if (runtime_creds.get("netbox_token") or cfg.NETBOX_TOKEN) else "",
        "observium_base_url": get_observium_base_url(),
    }


@app.get("/api/check/zabbix")
async def check_zabbix():
    try:
        result = await zabbix_request("apiinfo.version", {})
        return {"ok": True, "version": result["result"]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.get("/api/check/netbox")
async def check_netbox():
    try:
        result = await netbox_request("GET", "/status/")
        return {"ok": True, "version": result["result"].get("netbox-version", "?")}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.get("/api/check/observium")
async def check_observium():
    try:
        row = observium_db_query("SELECT COUNT(*) AS total FROM devices", fetch="one")
        return {"ok": True, "base_url": get_observium_base_url(), "devices": row["total"]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.get("/api/archive/{source}")
async def get_archives(source: str):
    return {"result": list_archives(source)}


@app.post("/api/archive/{source}/{external_id}")
async def archive_device(source: str, external_id: str, body: ArchivePayload):
    set_archive(source, external_id, body.label, body.details)
    return {"status": "ok", "source": source, "external_id": external_id}


@app.delete("/api/archive/{source}/{external_id}")
async def restore_archived_device(source: str, external_id: str):
    clear_archive(source, external_id)
    return {"status": "ok", "source": source, "external_id": external_id}


@app.post("/api/archive/bulk")
async def bulk_archive_devices(body: BulkArchivePayload):
    for external_id in body.ids:
        if body.archive:
            set_archive(body.source, external_id, None, {})
        else:
            clear_archive(body.source, external_id)
    return {"status": "ok", "count": len(body.ids), "source": body.source, "archive": body.archive}


@app.get("/api/export-logs")
async def get_export_logs(limit: int = 100):
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
        return {
            "result": [
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
        }
    finally:
        conn.close()


@app.get("/api/correlations")
async def correlations():
    return {"result": list_correlations()}


@app.get("/api/correlations/match")
async def correlation_match(source: str, external_id: str):
    return {"result": find_correlation_by_item(source, external_id)}


@app.post("/api/correlations/link")
async def correlation_link(body: CorrelationLinkPayload):
    return {"status": "ok", "result": save_correlation(body)}


@app.delete("/api/correlations/{group_id}/{source}")
async def correlation_unlink(group_id: int, source: str):
    if source not in {"zabbix", "netbox", "observium"}:
        raise HTTPException(400, "Invalid source")
    return remove_correlation_item(group_id, source)


@app.get("/api/zabbix/hosts")
async def zabbix_hosts(limit: int = 500, archived: str = "exclude"):
    global_macros = await zabbix_global_macros()
    result = await zabbix_request(
        "host.get",
        {
            "output": "extend",
            "selectInterfaces": "extend",
            "selectInventory": "extend",
            "selectTags": "extend",
            "selectGroups": "extend",
            "selectMacros": "extend",
            "limit": limit,
        },
    )
    records = result["result"] or []
    for record in records:
        record["globalmacros"] = global_macros
        record["ui_url"] = zabbix_host_search_ui_url(record)
    return {**result, "result": apply_archive_mode("zabbix", records, "hostid", archived)}


@app.get("/api/zabbix/hosts/{host_id}")
async def zabbix_host(host_id: str):
    global_macros = await zabbix_global_macros()
    result = await zabbix_request(
        "host.get",
        {
            "output": "extend",
            "hostids": [host_id],
            "selectInterfaces": "extend",
            "selectInventory": "extend",
            "selectTags": "extend",
            "selectGroups": "extend",
            "selectMacros": "extend",
        },
    )
    if result["result"]:
        result["result"][0]["globalmacros"] = global_macros
        result["result"][0]["archived"] = archive_status("zabbix", host_id)
        result["result"][0]["ui_url"] = zabbix_host_search_ui_url(result["result"][0])
    return result


@app.post("/api/zabbix/hosts")
async def zabbix_create_host(body: dict[str, Any]):
    return await zabbix_request("host.create", body)


@app.patch("/api/zabbix/hosts/{host_id}")
async def zabbix_update_host(host_id: str, body: dict[str, Any]):
    body["hostid"] = host_id
    return await zabbix_request("host.update", body)


@app.get("/api/zabbix/interfaces/{host_id}")
async def zabbix_interfaces(host_id: str):
    return await zabbix_request("hostinterface.get", {"output": "extend", "hostids": [host_id]})


@app.post("/api/zabbix/interfaces")
async def zabbix_create_interface(body: dict[str, Any]):
    return await zabbix_request("hostinterface.create", body)


@app.patch("/api/zabbix/interfaces/{iface_id}")
async def zabbix_update_interface(iface_id: str, body: dict[str, Any]):
    body["interfaceid"] = iface_id
    return await zabbix_request("hostinterface.update", body)


@app.delete("/api/zabbix/interfaces/{iface_id}")
async def zabbix_delete_interface(iface_id: str):
    return await zabbix_request("hostinterface.delete", [iface_id])


@app.get("/api/zabbix/groups")
async def zabbix_groups():
    return await zabbix_request("hostgroup.get", {"output": "extend"})


@app.get("/api/netbox/devices")
async def netbox_devices(limit: int = 500, offset: int = 0, archived: str = "exclude"):
    result = await netbox_request("GET", "/dcim/devices/", {"limit": limit, "offset": offset})
    items = result["result"].get("results", [])
    result["result"]["results"] = apply_archive_mode("netbox", items, "id", archived)
    return result


@app.get("/api/netbox/inventory.drawio")
async def netbox_inventory_drawio(archived: str = "exclude"):
    devices = await fetch_all_netbox_devices(archived=archived)
    xml = build_inventory_drawio_xml(devices)
    output_path = Path(cfg.APP_DB_PATH).parent / "inventory.drawio"
    output_path.write_text(xml, encoding="utf-8")
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Content-Disposition": 'attachment; filename="inventory.drawio"'},
    )


@app.get("/api/netbox/devices/{device_id}")
async def netbox_device(device_id: int):
    result = await netbox_request("GET", f"/dcim/devices/{device_id}/")
    if result.get("result"):
        result["result"]["archived"] = archive_status("netbox", str(device_id))
    return result


@app.post("/api/netbox/devices")
async def netbox_create_device(body: dict[str, Any]):
    return await netbox_request("POST", "/dcim/devices/", body)


@app.patch("/api/netbox/devices/{device_id}")
async def netbox_update_device(device_id: int, body: dict[str, Any]):
    return await netbox_request("PATCH", f"/dcim/devices/{device_id}/", body)


@app.post("/api/netbox/devices/{device_id}/primary-ip")
async def netbox_set_primary_ip(device_id: int, body: dict[str, Any]):
    ip_id = body.get("ip_id")
    if ip_id in {None, ""}:
        raise HTTPException(400, "ip_id is required")

    ip_result = await netbox_request("GET", f"/ipam/ip-addresses/{int(ip_id)}/")
    ip_payload = ip_result.get("result") or {}
    address = str(ip_payload.get("address") or "").strip()
    if not address:
        raise HTTPException(400, "NetBox IP payload is missing address")

    try:
        ip_version = ipaddress.ip_interface(address).version
    except ValueError as exc:
        raise HTTPException(400, f"Invalid NetBox IP address: {address}") from exc

    primary_field = "primary_ip4" if ip_version == 4 else "primary_ip6"
    return await netbox_request("PATCH", f"/dcim/devices/{device_id}/", {primary_field: int(ip_id)})


@app.get("/api/netbox/devices/{device_id}/interfaces")
async def netbox_device_interfaces(device_id: int):
    return await netbox_request("GET", "/dcim/interfaces/", {"device_id": device_id, "limit": 200})


@app.get("/api/netbox/interfaces/{iface_id}")
async def netbox_interface(iface_id: int):
    return await netbox_request("GET", f"/dcim/interfaces/{iface_id}/")


@app.post("/api/netbox/interfaces")
async def netbox_create_interface(body: dict[str, Any]):
    return await netbox_request("POST", "/dcim/interfaces/", body)


@app.patch("/api/netbox/interfaces/{iface_id}")
async def netbox_update_interface(iface_id: int, body: dict[str, Any]):
    return await netbox_request("PATCH", f"/dcim/interfaces/{iface_id}/", body)


@app.delete("/api/netbox/interfaces/{iface_id}")
async def netbox_delete_interface(iface_id: int):
    return await netbox_request("DELETE", f"/dcim/interfaces/{iface_id}/")


@app.get("/api/netbox/ips")
async def netbox_ips(device_id: Optional[int] = None, address: Optional[str] = None, limit: int = 200):
    params: dict[str, Any] = {"limit": limit}
    if device_id:
        params["device_id"] = device_id
    if address:
        params["address"] = address
    return await netbox_request("GET", "/ipam/ip-addresses/", params)


@app.post("/api/netbox/ips")
async def netbox_create_ip(body: dict[str, Any]):
    return await netbox_request("POST", "/ipam/ip-addresses/", body)


@app.patch("/api/netbox/ips/{ip_id}")
async def netbox_update_ip(ip_id: int, body: dict[str, Any]):
    return await netbox_request("PATCH", f"/ipam/ip-addresses/{ip_id}/", body)


@app.delete("/api/netbox/ips/{ip_id}")
async def netbox_delete_ip(ip_id: int):
    return await netbox_request("DELETE", f"/ipam/ip-addresses/{ip_id}/")


@app.get("/api/netbox/device-types")
async def netbox_device_types():
    return await netbox_request("GET", "/dcim/device-types/", {"limit": 200})


@app.get("/api/netbox/platforms")
async def netbox_platforms():
    return await netbox_request("GET", "/dcim/platforms/", {"limit": 500})


@app.post("/api/netbox/platforms")
async def netbox_create_platform(body: dict[str, Any]):
    return await netbox_request("POST", "/dcim/platforms/", body)


@app.get("/api/netbox/sites")
async def netbox_sites():
    return await netbox_request("GET", "/dcim/sites/", {"limit": 200})


@app.get("/api/netbox/locations")
async def netbox_locations(site_id: Optional[int] = None):
    params: dict[str, Any] = {"limit": 500}
    if site_id:
        params["site_id"] = site_id
    return await netbox_request("GET", "/dcim/locations/", params)


@app.post("/api/netbox/locations")
async def netbox_create_location(body: dict[str, Any]):
    return await netbox_request("POST", "/dcim/locations/", body)


@app.get("/api/netbox/roles")
async def netbox_device_roles():
    return await netbox_request("GET", "/dcim/device-roles/", {"limit": 200})


@app.get("/api/netbox/devices/{device_id}/sync")
async def netbox_device_sync(device_id: int):
    return {"status": "ok", "result": await build_netbox_sync_preview(device_id)}


@app.put("/api/netbox/devices/{device_id}/sync")
async def netbox_update_sync_profile(device_id: int, body: NetBoxSyncProfilePayload):
    profile = save_netbox_sync_profile(device_id, body.enabled, body.field_sources)
    return {"status": "ok", "result": {"profile": profile, "preview": await build_netbox_sync_preview(device_id)}}


@app.post("/api/netbox/devices/{device_id}/sync/run")
async def netbox_run_sync(device_id: int, body: NetBoxSyncProfilePayload):
    return {"status": "ok", "result": await apply_netbox_sync(device_id, body.enabled, body.field_sources)}


@app.get("/api/netbox/devices/{device_id}/enrichment")
async def netbox_device_enrichment(device_id: int):
    return {"status": "ok", "result": await build_netbox_enrichment(device_id)}


@app.post("/api/netbox/devices/{device_id}/enrichment/apply")
async def netbox_apply_enrichment(device_id: int, body: NetBoxEnrichmentApplyPayload):
    if not body.actions:
        raise HTTPException(400, "No enrichment actions were provided")

    device_result = await netbox_request("GET", f"/dcim/devices/{device_id}/")
    device = device_result.get("result") or {}
    sources = await correlated_sources_for_netbox_device(device_id)
    zabbix_host = sources["zabbix"]
    results: list[dict[str, Any]] = []

    for action in body.actions:
        action_type = action.action_type
        value = compact_text(action.value)
        try:
            if action_type == "set_platform":
                if not value:
                    raise HTTPException(400, "Platform value is required")
                ensured = await ensure_netbox_platform(value)
                platform = ensured["platform"]
                updated = await netbox_request("PATCH", f"/dcim/devices/{device_id}/", {"platform": int(platform["id"])})
                results.append({"action_type": action_type, "status": "ok", "created": ensured["created"], "platform": platform, "device": updated.get("result")})
            elif action_type == "set_location":
                if not value:
                    raise HTTPException(400, "Location value is required")
                ensured = await ensure_netbox_location(value, (device.get("site") or {}).get("id"))
                location = ensured["location"]
                updated = await netbox_request("PATCH", f"/dcim/devices/{device_id}/", {"location": int(location["id"])})
                results.append({"action_type": action_type, "status": "ok", "created": ensured["created"], "location": location, "device": updated.get("result")})
            elif action_type == "set_primary_ip4":
                applied = await ensure_netbox_primary_ip4(device, value)
                results.append({"action_type": action_type, "status": "ok", **applied})
            elif action_type == "migrate_description_ip":
                applied = await ensure_netbox_primary_ip4(device, value or compact_text(device.get("description")))
                cleared = await netbox_request("PATCH", f"/dcim/devices/{device_id}/", {"description": ""})
                results.append({"action_type": action_type, "status": "ok", **applied, "description_cleared": True, "device": cleared.get("result")})
            elif action_type == "set_comments":
                updated = await netbox_request("PATCH", f"/dcim/devices/{device_id}/", {"comments": action.value or ""})
                results.append({"action_type": action_type, "status": "ok", "device": updated.get("result")})
            elif action_type == "set_description":
                updated = await netbox_request("PATCH", f"/dcim/devices/{device_id}/", {"description": action.value or ""})
                results.append({"action_type": action_type, "status": "ok", "device": updated.get("result")})
            elif action_type == "set_serial":
                updated = await netbox_request("PATCH", f"/dcim/devices/{device_id}/", {"serial": action.value or ""})
                results.append({"action_type": action_type, "status": "ok", "device": updated.get("result")})
            elif action_type == "set_asset_tag":
                updated = await netbox_request("PATCH", f"/dcim/devices/{device_id}/", {"asset_tag": action.value or ""})
                results.append({"action_type": action_type, "status": "ok", "device": updated.get("result")})
            elif action_type == "ensure_zabbix_serial_item":
                if not zabbix_host:
                    raise HTTPException(400, "No correlated Zabbix host is available")
                ensured = await ensure_zabbix_serial_item(str(zabbix_host["hostid"]))
                results.append({"action_type": action_type, "status": "ok", **ensured})
            else:
                raise HTTPException(400, f"Unsupported enrichment action: {action_type}")
        except HTTPException as exc:
            results.append({"action_type": action_type, "status": "error", "error": exc.detail})

    refreshed = await build_netbox_enrichment(device_id)
    return {"status": "ok", "results": results, "result": refreshed}


@app.post("/api/netbox/enrichment/fix-primary-ip4-correlated")
async def netbox_fix_primary_ip4_correlated(body: NetBoxPrimaryIpFixPayload = NetBoxPrimaryIpFixPayload()):
    results: list[dict[str, Any]] = []
    for group in list_correlations():
        items = group.get("items") or {}
        nb_item = items.get("netbox")
        zb_item = items.get("zabbix")
        if not nb_item or not zb_item:
            continue
        if archive_status("netbox", nb_item["id"]):
            continue

        try:
            device_result = await netbox_request("GET", f"/dcim/devices/{int(nb_item['id'])}/")
            device = device_result.get("result") or {}
            zabbix_result = await zabbix_request(
                "host.get",
                {
                    "output": "extend",
                    "hostids": [zb_item["id"]],
                    "selectInterfaces": "extend",
                    "selectInventory": "extend",
                },
            )
            host = (zabbix_result.get("result") or [None])[0]
            if not host:
                results.append({"device_id": nb_item["id"], "device": nb_item.get("label"), "status": "skipped", "reason": "Missing correlated Zabbix host"})
                continue

            candidate_ip = extract_main_zabbix_ip(host)
            if not candidate_ip:
                results.append({"device_id": nb_item["id"], "device": device.get("name"), "status": "skipped", "reason": "No IPv4 on main Zabbix interface"})
                continue
            if device.get("primary_ip4"):
                results.append({"device_id": nb_item["id"], "device": device.get("name"), "status": "skipped", "reason": "Primary IPv4 already set", "current": device.get("primary_ip4", {}).get("address")})
                continue
            if body.dry_run:
                results.append({"device_id": nb_item["id"], "device": device.get("name"), "status": "planned", "proposed_ip": candidate_ip})
                continue

            applied = await ensure_netbox_primary_ip4(device, candidate_ip)
            results.append({"device_id": nb_item["id"], "device": device.get("name"), "status": "updated", **applied})
        except HTTPException as exc:
            results.append({"device_id": nb_item["id"], "device": nb_item.get("label"), "status": "error", "error": exc.detail})

    return {"status": "ok", "count": len(results), "result": results}


@app.get("/api/observium/devices")
async def observium_devices(limit: int = 500, search: str = "", archived: str = "exclude"):
    query = """
        SELECT *
        FROM devices
    """
    params: list[Any] = []
    if search:
        query += " WHERE hostname LIKE %s OR sysName LIKE %s OR ip LIKE %s OR location LIKE %s"
        token = f"%{search}%"
        params.extend([token, token, token, token])
    query += " ORDER BY hostname ASC LIMIT %s"
    params.append(limit)
    rows = observium_db_query(query, tuple(params))
    items = [normalize_observium_device(row) for row in rows]
    return {"result": apply_archive_mode("observium", items, "device_id", archived)}


@app.get("/api/observium/devices/{device_id}")
async def observium_device(device_id: int):
    row = observium_db_query(
        """
        SELECT *
        FROM devices
        WHERE device_id = %s
        """,
        (device_id,),
        fetch="one",
    )
    if not row:
        raise HTTPException(404, "Observium device not found")
    result = normalize_observium_device(row)
    result["archived"] = archive_status("observium", str(device_id))
    return {"result": result}


@app.post("/api/observium/devices")
async def observium_create_device(body: ObserviumDeviceCreate):
    add_result = observium_exec(build_observium_add_command(body))
    row = observium_db_query("SELECT device_id, hostname FROM devices WHERE hostname = %s", (body.hostname,), fetch="one")
    if not row:
        raise HTTPException(400, f"Observium add failed: {add_result['output']}")

    updates = {"label": body.label, "location": body.location, "purpose": body.purpose, "ip": body.hostname}
    updates = {key: value for key, value in updates.items() if value}
    icmp_column = observium_optional_icmp_column()
    if icmp_column:
        updates[icmp_column] = int(body.skip_icmp)
    if updates:
        await observium_update_device(row["device_id"], updates)

    refresh_result = None
    if body.run_discovery or body.run_poller:
        refresh_result = {}
        if body.run_discovery:
            refresh_result["discovery"] = observium_exec(["php", "/opt/observium/discovery.php", "-h", str(row["device_id"])])
        if body.run_poller:
            refresh_result["poller"] = observium_exec(["php", "/opt/observium/poller.php", "-h", str(row["device_id"])])
    return {"status": "ok", "device_id": row["device_id"], "hostname": row["hostname"], "add_result": add_result, "refresh_result": refresh_result}


@app.patch("/api/observium/devices/{device_id}")
async def observium_update_device(device_id: int, body: dict[str, Any]):
    current = observium_db_query("SELECT * FROM devices WHERE device_id = %s", (device_id,), fetch="one")
    if not current:
        raise HTTPException(404, "Observium device not found")

    rename_result = None
    if "hostname" in body and body["hostname"] and body["hostname"] != current["hostname"]:
        rename_result = observium_exec(["php", "/opt/observium/rename_device.php", "-p", current["hostname"], body["hostname"]])

    allowed_fields = {
        "label", "ip", "snmp_version", "snmp_community", "snmp_port", "snmp_transport",
        "snmp_authlevel", "snmp_authname", "snmp_authpass", "snmp_authalgo",
        "snmp_cryptopass", "snmp_cryptoalgo", "snmp_context", "location", "purpose",
        "disabled", "ignore", "status",
    }
    icmp_column = observium_optional_icmp_column()
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
        observium_db_query(f"UPDATE devices SET {', '.join(updates)} WHERE device_id = %s", tuple(params), fetch="none")

    updated = observium_db_query("SELECT * FROM devices WHERE device_id = %s", (device_id,), fetch="one")
    hostname_changed = bool(body.get("hostname") and body["hostname"] != current["hostname"])
    if hostname_changed and updated and updated["hostname"] == current["hostname"] and not is_ip_address(str(body["hostname"])):
        updates = []
        params = []
        if not body.get("label"):
            updates.append("`label` = %s")
            params.append(body["hostname"])
        if updates:
            params.append(device_id)
            observium_db_query(f"UPDATE devices SET {', '.join(updates)} WHERE device_id = %s", tuple(params), fetch="none")
            updated = observium_db_query("SELECT * FROM devices WHERE device_id = %s", (device_id,), fetch="one")
    return {"status": "ok", "rename_result": rename_result, "result": normalize_observium_device(updated)}


@app.delete("/api/observium/devices/{device_id}")
async def observium_delete_device(device_id: int):
    current = observium_db_query("SELECT hostname FROM devices WHERE device_id = %s", (device_id,), fetch="one")
    if not current:
        raise HTTPException(404, "Observium device not found")
    result = observium_exec(["php", "/opt/observium/delete_device.php", current["hostname"]])
    return {"status": "ok", "hostname": current["hostname"], "result": result}


@app.post("/api/observium/devices/{device_id}/refresh")
async def observium_refresh(device_id: int):
    return observium_refresh_device(device_id)


@app.post("/api/exports/zabbix-to-observium")
async def export_zabbix_to_observium(body: ExportZabbixToObserviumPayload):
    if not body.hostids:
        raise HTTPException(400, "hostids is required")

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
    global_macros = await zabbix_global_macros()

    results: list[dict[str, Any]] = []
    for host in zabbix_result["result"] or []:
        host["globalmacros"] = global_macros
        payload_snapshot = {"hostid": host.get("hostid"), "host": host.get("host"), "name": host.get("name")}
        try:
            candidate = extract_zabbix_snmp_host(host)
            try:
                snmp_check = observium_snmp_check(candidate["address"], candidate["port"], candidate["snmp"])
            except HTTPException as exc:
                snmp_check = {"status": "error", "message": str(exc.detail)}
            existing = observium_db_query(
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
                updated = await observium_update_device(int(existing["device_id"]), update_payload)
                refresh = observium_refresh_device(int(existing["device_id"])) if (body.run_discovery or body.run_poller) else None
                result = {"status": "updated", "hostid": candidate["hostid"], "host": candidate["host"], "observium_device_id": existing["device_id"], "snmp_check": snmp_check, "update": updated, "refresh": refresh}
            elif existing:
                result = {"status": "skipped", "hostid": candidate["hostid"], "host": candidate["host"], "observium_device_id": existing["device_id"], "message": "Device already exists in Observium"}
            else:
                created = await observium_create_device(
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
                result = {"status": "created", "hostid": candidate["hostid"], "host": candidate["host"], "snmp_check": snmp_check, "create": created}

            log_export(action="manual_export", source="zabbix", target="observium", status=result["status"], entity_id=str(host.get("hostid")), entity_label=host.get("host"), message=f"Zabbix host {host.get('host')} exported to Observium ({result['status']})", payload=payload_snapshot, response=result)
            results.append(result)
        except Exception as exc:
            error_result = {"status": "error", "hostid": host.get("hostid"), "host": host.get("host"), "message": str(exc)}
            log_export(action="manual_export", source="zabbix", target="observium", status="error", entity_id=str(host.get("hostid")), entity_label=host.get("host"), message=str(exc), payload=payload_snapshot, response=error_result)
            results.append(error_result)

    return {"status": "ok", "count": len(results), "result": results}


DEFAULT_MAPPING = {
    "zabbix_to_netbox": {
        "host": "name",
        "name": "display",
        "description": "comments",
        "interfaces[0].ip": "primary_ip4.address",
    },
    "netbox_to_zabbix": {
        "name": "host",
        "primary_ip4.address": "interfaces[0].ip",
        "comments": "description",
    },
    "zabbix_to_observium": {
        "interfaces[0].ip": "hostname",
        "interfaces[0].details.community": "snmp_community",
        "interfaces[0].details.version": "snmp_version",
    },
}

_mapping = DEFAULT_MAPPING.copy()


@app.get("/api/mapping")
async def get_mapping():
    return _mapping


@app.put("/api/mapping")
async def set_mapping(body: dict[str, Any]):
    global _mapping
    _mapping = body
    return {"status": "ok", "mapping": _mapping}


@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "1.3.0", "db": cfg.APP_DB_PATH}
