from __future__ import annotations

from pathlib import Path
from typing import Any

from app.domain.topology import build_inventory_drawio_xml
from app.integrations.netbox_api import NetBoxApiClient
from app.services.archive_service import apply_archive_mode


async def fetch_all_netbox_devices(
    client: NetBoxApiClient,
    *,
    archived: str = "exclude",
) -> list[dict[str, Any]]:
    limit = 200
    offset = 0
    collected: list[dict[str, Any]] = []
    while True:
        result = await client.request("GET", "/dcim/devices/", {"limit": limit, "offset": offset})
        payload = result.get("result") or {}
        batch = payload.get("results") or []
        if not batch:
            break
        collected.extend(batch)
        if not payload.get("next"):
            break
        offset += limit
    return apply_archive_mode("netbox", collected, "id", archived)


async def build_inventory_drawio_document(
    client: NetBoxApiClient,
    *,
    archived: str,
    asset_root: Path,
) -> str:
    devices = await fetch_all_netbox_devices(client, archived=archived)
    return build_inventory_drawio_xml(devices, asset_root=asset_root)
