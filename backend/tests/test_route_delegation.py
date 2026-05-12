import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from main import app


class RouteDelegationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_zabbix_route_delegates_to_service(self) -> None:
        with patch("app.routes.zabbix.zabbix_service.hosts", AsyncMock(return_value={"result": []})) as mock_call:
            response = self.client.get("/api/zabbix/hosts")
        self.assertEqual(response.status_code, 200)
        mock_call.assert_awaited_once()

    def test_netbox_route_delegates_to_service(self) -> None:
        with patch("app.routes.netbox.netbox_service.list_devices_route", AsyncMock(return_value={"result": {"results": []}})) as mock_call:
            response = self.client.get("/api/netbox/devices")
        self.assertEqual(response.status_code, 200)
        mock_call.assert_awaited_once()

    def test_observium_route_delegates_to_service(self) -> None:
        with patch("app.routes.observium.observium_service.list_devices", AsyncMock(return_value={"result": []})) as mock_call:
            response = self.client.get("/api/observium/devices")
        self.assertEqual(response.status_code, 200)
        mock_call.assert_awaited_once()

    def test_discovery_route_delegates_to_service(self) -> None:
        payload = {"source": "observium", "source_id": "1"}
        with patch("app.routes.discovery.discovery_service.build_preview", AsyncMock(return_value={"proposals": []})) as mock_call:
            response = self.client.post("/api/discovery/lldp/preview", json=payload)
        self.assertEqual(response.status_code, 200)
        mock_call.assert_awaited_once()
