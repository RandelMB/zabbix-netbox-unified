import json
import re
from html import unescape
from typing import Any, Optional

import httpx
from fastapi import HTTPException

from app.core.settings import cfg
from app.shared.text import as_int, compact_text


def _strip_tags(value: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return compact_text(unescape(text))


def _split_lines(value: str) -> list[str]:
    text = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return [compact_text(unescape(line)) for line in text.splitlines() if compact_text(line)]


def _speed_to_bps(label: str) -> Optional[int]:
    text = compact_text(label).lower()
    multipliers = {
        "tbps": 1_000_000_000_000,
        "gbps": 1_000_000_000,
        "mbps": 1_000_000,
        "kbps": 1_000,
        "bps": 1,
    }
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*(tbps|gbps|mbps|kbps|bps)", text)
    if not match:
        return None
    return int(float(match.group(1)) * multipliers[match.group(2)])


def _netbox_type_from_port(name: str, raw_type: str, speed_label: str) -> str:
    raw = compact_text(raw_type).lower()
    name_text = compact_text(name).lower()
    speed = _speed_to_bps(speed_label) or 0
    if raw.startswith("l2 vlan") or name_text.startswith("vlan "):
        return "virtual"
    if "lag" in raw or "port-channel" in name_text or "lag" in name_text:
        return "lag"
    if raw == "ethernet":
        if speed >= 100_000_000_000:
            return "100gbase-x-qsfp28"
        if speed >= 40_000_000_000:
            return "40gbase-x-qsfpp"
        if speed >= 25_000_000_000:
            return "25gbase-x-sfp28"
        if speed >= 10_000_000_000:
            return "10gbase-x-sfpp"
        if speed >= 5_000_000_000:
            return "5gbase-t"
        if speed >= 2_500_000_000:
            return "2.5gbase-t"
        if speed >= 1_000_000_000:
            return "1000base-t"
        if speed >= 100_000_000:
            return "100base-tx"
        if speed >= 10_000_000:
            return "10base-t"
    return "other"


class ObserviumWebClient:
    def __init__(self, *, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password

    def configured(self) -> bool:
        return bool(self.base_url and self.username and self.password)

    def _login(self) -> httpx.Client:
        if not self.configured():
            raise HTTPException(503, "Observium web session is not configured")
        client = httpx.Client(base_url=self.base_url, verify=cfg.OBSERVIUM_WEB_TLS_VERIFY, timeout=30, follow_redirects=True)
        client.get("/login/")
        client.post(
            "/login/",
            data={
                "username": self.username,
                "password": self.password,
                "remember": "1",
                "submit": "1",
            },
            headers={"Referer": f"{self.base_url}/login/"},
        )
        home = client.get("/")
        if "Please log in:" in home.text:
            client.close()
            raise HTTPException(401, "Observium web login failed")
        return client

    def device_entities(self) -> list[dict[str, Any]]:
        client = self._login()
        try:
            response = client.get(
                "/ajax/get_entities.php",
                params={"entity_type": "device"},
                headers={"X-Requested-With": "XMLHttpRequest", "Referer": f"{self.base_url}/"},
            )
            payload = json.loads(response.text)
            items: list[dict[str, Any]] = []
            for key, value in payload.items():
                items.append(
                    {
                        "device_id": as_int(key) or 0,
                        "ip": compact_text(value.get("name")),
                        "hostname": compact_text(value.get("subtext")) or compact_text(value.get("name")),
                        "sysName": compact_text(value.get("subtext")) or compact_text(value.get("name")),
                        "status": 1 if compact_text(value.get("group")).upper() == "UP" else 0,
                    }
                )
            return items
        finally:
            client.close()

    def device_popup(self, device_id: int) -> dict[str, Any]:
        client = self._login()
        try:
            response = client.get(
                "/ajax/entity_popup.php",
                params={"entity_type": "device", "entity_id": device_id},
                headers={"X-Requested-With": "XMLHttpRequest", "Referer": f"{self.base_url}/"},
            )
            html = response.text
            row_match = re.search(
                r'<tr class="(?P<state>[^"]+)".*?<a href="device/device=\d+/".*?>(?P<ip>[^<]+)</a><br /></td>\s*<td>(?P<platform>.*?)</td>\s*<td>(?P<identity>.*?)</td>',
                html,
                re.S,
            )
            if not row_match:
                raise HTTPException(404, "Observium web device details not found")
            platform_lines = _split_lines(row_match.group("platform"))
            identity_lines = _split_lines(row_match.group("identity"))
            platform_name = platform_lines[0] if platform_lines else ""
            hardware = platform_lines[1] if len(platform_lines) > 1 else ""
            hostname = identity_lines[-1] if identity_lines else compact_text(row_match.group("ip"))
            return {
                "device_id": int(device_id),
                "hostname": hostname,
                "sysName": hostname,
                "ip": compact_text(row_match.group("ip")),
                "os": platform_name,
                "version": platform_name,
                "hardware": hardware,
                "status": 1 if compact_text(row_match.group("state")).lower() == "up" else 0,
                "disabled": False,
                "ignore": False,
                "skip_icmp": False,
            }
        finally:
            client.close()

    def device_ports(self, device_id: int) -> list[dict[str, Any]]:
        client = self._login()
        try:
            response = client.get(f"/device/device={device_id}/tab=ports/")
            html = response.text
            items: list[dict[str, Any]] = []
            for segment in html.split('min-width: 250px;')[1:]:
                port_match = re.search(r'href="device/device=\d+/tab=port/port=(?P<port_id>\d+)/".*?>(?P<name>[^<]+)</a>', segment, re.S)
                if not port_match:
                    continue
                small_values = re.findall(r"<span class=\"small\">(.*?)</span>", segment, re.S)
                description = _strip_tags(small_values[0]) if small_values else ""
                raw_type = _strip_tags(small_values[1]) if len(small_values) > 1 else ""
                speed_label = _strip_tags(small_values[2]) if len(small_values) > 2 else ""
                mtu_match = re.search(r"MTU\s+(\d+)", segment, re.I)
                mac_match = re.search(r"([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5})", segment)
                vlan_values = re.findall(r"VLAN\s+(\d+(?:-\d+)?)", segment, re.I)
                is_enabled = "entity-popup red" not in segment and "sprite-shutdown" not in segment
                items.append(
                    {
                        "port_id": int(port_match.group("port_id")),
                        "device_id": int(device_id),
                        "ifIndex": None,
                        "name": compact_text(port_match.group("name")),
                        "name_candidates": [compact_text(port_match.group("name"))],
                        "description": description,
                        "mac_address": compact_text(mac_match.group(1)).upper() if mac_match else "",
                        "type": _netbox_type_from_port(port_match.group("name"), raw_type, speed_label),
                        "raw_type": raw_type,
                        "admin_status": "up" if is_enabled else "down",
                        "oper_status": "up" if is_enabled else "down",
                        "enabled_candidate": is_enabled,
                        "speed_bps": _speed_to_bps(speed_label),
                        "speed_label": speed_label,
                        "mtu": as_int(mtu_match.group(1)) if mtu_match else None,
                        "vlans": vlan_values,
                        "lag_parent_port_id": None,
                        "lag_parent_name": "",
                        "lag_member_port_ids": [],
                        "lag_member_names": [],
                        "lag_role": "standalone",
                        "lag_name": "",
                        "raw": {"source": "web_session"},
                    }
                )
            return items
        finally:
            client.close()

    def device_links(self, device_id: int) -> list[dict[str, Any]]:
        client = self._login()
        try:
            response = client.get(f"/device/device={device_id}/tab=ports/view=neighbours/")
            html = response.text
            rows = re.findall(r"<tbody>(.*?)</tbody>", html, re.S)
            if not rows:
                return []
            links: list[dict[str, Any]] = []
            for row_html in re.findall(r"<tr class=\"[^\"]*\">(.*?)</tr>", rows[0], re.S):
                local_match = re.search(r'device/device=\d+/tab=port/port=(?P<port_id>\d+)/".*?>(?P<port_name>[^<]+)</a>', row_html, re.S)
                if not local_match:
                    continue
                remote_device_match = re.search(r'device/device=(?P<device_id>\d+)/".*?>(?P<device_name>[^<]+)</a>', row_html, re.S)
                remote_port_match = re.search(r'device/device=\d+/tab=port/port=(?P<port_id>\d+)/".*?>(?P<port_name>[^<]+)</a>', row_html[local_match.end():], re.S)
                protocol_match = re.search(r'<span class="label [^"]*">([^<]+)</span>', row_html)
                links.append(
                    {
                        "local_port_id": int(local_match.group("port_id")),
                        "remote_device_id": as_int((remote_device_match or {}).group("device_id") if remote_device_match else None),
                        "remote_port_id": as_int((remote_port_match or {}).group("port_id") if remote_port_match else None),
                        "remote_name": compact_text((remote_device_match or {}).group("device_name") if remote_device_match else ""),
                        "remote_port_name": compact_text((remote_port_match or {}).group("port_name") if remote_port_match else ""),
                        "protocol": compact_text(protocol_match.group(1) if protocol_match else "LLDP"),
                    }
                )
            return links
        finally:
            client.close()
