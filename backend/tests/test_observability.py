import unittest

from fastapi.testclient import TestClient

from app.core.application import app


class ObservabilityTests(unittest.TestCase):
    def test_request_ids_are_emitted_in_headers(self) -> None:
        client = TestClient(app)
        response = client.get("/does-not-exist")
        self.assertTrue(response.headers.get("X-Request-ID"))
        self.assertTrue(response.headers.get("X-Operation-ID"))
        self.assertTrue(response.headers.get("X-Response-Time-MS"))
