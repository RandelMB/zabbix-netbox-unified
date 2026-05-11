import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.services.settings_service import set_runtime_credentials
from main import app


class SettingsRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        set_runtime_credentials({})

    def test_settings_snapshot_masks_tokens(self) -> None:
        set_runtime_credentials({"zabbix_token": "abc", "netbox_token": "def"})
        response = self.client.get("/api/settings")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["credentials"]["zabbix_token"], "***")
        self.assertEqual(body["credentials"]["netbox_token"], "***")

    def test_check_endpoints_use_services(self) -> None:
        with patch("app.routes.settings.check_zabbix_connection", AsyncMock(return_value={"ok": True, "version": "7.0"})):
            response = self.client.get("/api/check/zabbix")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["version"], "7.0")
