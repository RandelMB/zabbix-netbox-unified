from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from app.shared.text import compact_text


def extract_ip_like_text(value: Any) -> str:
    text = compact_text(value)
    if not text:
        return ""
    match = re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}(?:/\d{1,2})?\b", text)
    return match.group(0) if match else ""


def host_part(address: str) -> str:
    return compact_text(address).split("/")[0]


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


def build_inventory_drawio_xml(devices: list[dict[str, Any]], *, asset_root: Path) -> str:
    modified = "2026-04-07T00:00:00.000Z"
    cols = 5
    width = 250
    height = 165
    gap_x = 30
    gap_y = 30
    start_x = 40
    start_y = 40
    png_path = asset_root / "switch.png"
    if not png_path.exists():
        png_path = Path(__file__).resolve().parents[2] / "switch.png"
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
        fill_color = drawio_fill_color(device)
        lines.append(
            f'        <mxCell id="{cell_id}" value="" style="shape=image;verticalLabelPosition=bottom;verticalAlign=top;imageAspect=0;aspect=fixed;image={image_url};fillColor={fill_color};strokeColor=none;" '
            f'vertex="1" parent="1"><mxGeometry x="{x + 55}" y="{y + 8}" width="140" height="58" as="geometry" /></mxCell>'
        )
        cell_id += 1
        lines.append(
            f'        <mxCell id="{cell_id}" value="{value}" '
            f'style="rounded=1;whiteSpace=wrap;html=1;fillColor={fill_color};strokeColor=#475569;fontSize=12;fontFamily=Helvetica;align=center;verticalAlign=middle;spacing=8;" '
            f'vertex="1" parent="1"><mxGeometry x="{x}" y="{y + 70}" width="{width}" height="92" as="geometry" /></mxCell>'
        )
        cell_id += 1
    lines.extend(["      </root>", "    </mxGraphModel>", "  </diagram>", "</mxfile>"])
    return "\n".join(lines) + "\n"
