import asyncio
import unittest
from unittest.mock import patch

import httpx
from fastapi import HTTPException

from app.integrations.http_client import request_json
from app.integrations.netbox_api import NetBoxApiClient
from app.integrations.zabbix_api import ZabbixApiClient


class HttpIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_json_retries_transport_errors(self) -> None:
        calls = {"count": 0}

        async def fake_sleep(_: float) -> None:
            return None

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def request(self, *args, **kwargs):
                calls["count"] += 1
                if calls["count"] < 3:
                    raise httpx.TimeoutException("timeout")
                return httpx.Response(200, json={"ok": True})

        with patch("app.integrations.http_client.httpx.AsyncClient", FakeClient), patch("app.integrations.http_client.asyncio.sleep", fake_sleep):
            response = await request_json(method="GET", url="https://example.test", retries=2)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls["count"], 3)

    async def test_netbox_client_retries_get_only(self) -> None:
        requests: list[str] = []

        async def fake_request_json(**kwargs):
            requests.append(kwargs["method"])
            return httpx.Response(200, json={"ok": True})

        client = NetBoxApiClient(base_url="https://netbox.test", token="abc", tls_verify=True)
        with patch("app.integrations.netbox_api.request_json", side_effect=fake_request_json):
            await client.request("GET", "/status/")
            await client.request("POST", "/dcim/devices/", {"name": "r1"})
        self.assertEqual(requests, ["GET", "POST"])

    async def test_zabbix_client_auth_errors_are_translated(self) -> None:
        async def fake_request_json(**kwargs):
            return httpx.Response(200, json={"error": {"data": "bad auth"}})

        client = ZabbixApiClient(base_url="https://zabbix.test", tls_verify=True, username="Admin", password="bad")
        with patch("app.integrations.zabbix_api.request_json", side_effect=fake_request_json):
            with self.assertRaises(HTTPException) as ctx:
                await client.authenticate()
        self.assertEqual(ctx.exception.status_code, 401)
