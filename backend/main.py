import json
import logging
import os
import ipaddress
import shlex
import sqlite3
from pathlib import Path
from typing import Any, Optional

import docker
import httpx
import pymysql
from docker.errors import DockerException
from fastapi import FastAPI, HTTPException
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


def first_non_empty(data: dict[str, Any], *keys: str) -> Optional[str]:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return str(value)
    return None


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
    community = details.get("community")
    if not community:
        for macro in host.get("macros", []):
            if macro.get("macro") == "{$SNMP_COMMUNITY}":
                community = macro.get("value")
                break

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
    row["status"] = int(row.get("status") or 0)
    if get_observium_base_url():
        row["web_url"] = f"{get_observium_base_url()}/device/device={row['device_id']}/"
    return row


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
    return {**result, "result": apply_archive_mode("zabbix", records, "hostid", archived)}


@app.get("/api/zabbix/hosts/{host_id}")
async def zabbix_host(host_id: str):
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
        result["result"][0]["archived"] = archive_status("zabbix", host_id)
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


@app.get("/api/netbox/sites")
async def netbox_sites():
    return await netbox_request("GET", "/dcim/sites/", {"limit": 200})


@app.get("/api/netbox/roles")
async def netbox_device_roles():
    return await netbox_request("GET", "/dcim/device-roles/", {"limit": 200})


@app.get("/api/observium/devices")
async def observium_devices(limit: int = 500, search: str = "", archived: str = "exclude"):
    query = """
        SELECT
            device_id, hostname, sysName, label, ip,
            snmp_version, snmp_community, snmp_port, snmp_transport,
            status, disabled, `ignore`, location, purpose, os,
            last_polled, last_discovered
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
        SELECT
            device_id, hostname, sysName, label, ip,
            snmp_version, snmp_community, snmp_port, snmp_transport,
            snmp_authlevel, snmp_authname, snmp_authalgo, snmp_context,
            status, disabled, `ignore`, location, purpose, os, vendor, hardware, version,
            last_polled, last_discovered
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
    updates: list[str] = []
    params: list[Any] = []
    for field, value in body.items():
        if field not in allowed_fields:
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

    results: list[dict[str, Any]] = []
    for host in zabbix_result["result"] or []:
        payload_snapshot = {"hostid": host.get("hostid"), "host": host.get("host"), "name": host.get("name")}
        try:
            candidate = extract_zabbix_snmp_host(host)
            snmp_check = observium_snmp_check(candidate["address"], candidate["port"], candidate["snmp"])
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
