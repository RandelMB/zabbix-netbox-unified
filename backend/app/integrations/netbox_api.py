from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.integrations.http_client import request_json
from app.shared.security import sanitize_payload


class NetBoxApiClient:
    def __init__(self, *, base_url: str, token: str, tls_verify: bool, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.tls_verify = tls_verify
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        if not self.token:
            raise HTTPException(400, "No NetBox token configured")
        return {"Authorization": f"Token {self.token}", "Content-Type": "application/json", "Accept": "application/json"}

    async def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}/api{path}"
        normalized_method = method.upper()
        retries = 2 if normalized_method == "GET" else 0
        response = await request_json(
            method=normalized_method,
            url=url,
            headers=self._headers(),
            json_body=None if normalized_method in {"GET", "DELETE"} else (body or {}),
            params=body or {} if normalized_method == "GET" else None,
            timeout=self.timeout,
            verify=self.tls_verify,
            retries=retries,
        )
        if normalized_method == "DELETE":
            return {"status": response.status_code, "request": sanitize_payload({"method": normalized_method, "url": url, "body": body}), "response": {}}

        try:
            response_data = response.json()
        except Exception:
            response_data = {"raw": response.text}
        if response.status_code >= 400:
            raise HTTPException(response.status_code, f"NetBox error: {response_data}")
        return {
            "request": sanitize_payload({"method": normalized_method, "url": url, "body": body}),
            "response": response_data,
            "result": response_data,
        }
