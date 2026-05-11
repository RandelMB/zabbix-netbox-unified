from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Response

from app.core.settings import cfg
from app.integrations.netbox_api import NetBoxApiClient
from app.services.settings_service import get_netbox_token, get_netbox_url
from app.services.topology_export_service import build_inventory_drawio_document


router = APIRouter()


@router.get("/api/netbox/inventory.drawio")
async def netbox_inventory_drawio(archived: str = "exclude"):
    client = NetBoxApiClient(
        base_url=get_netbox_url(),
        token=get_netbox_token(),
        tls_verify=cfg.NETBOX_TLS_VERIFY,
    )
    xml = await build_inventory_drawio_document(
        client,
        archived=archived,
        asset_root=Path(__file__).resolve().parents[2],
    )
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Content-Disposition": 'attachment; filename="inventory.drawio"'},
    )
