from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.integrations.http_client import request_json
from app.shared.security import sanitize_payload


class ZabbixApiClient:
    def __init__(
        self,
        *,
        base_url: str,
        tls_verify: bool,
        token: str = "",
        username: str = "",
        password: str = "",
        timeout: float = 10.0,
        retries: int = 3,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.tls_verify = tls_verify
        self.token = token
        self.username = username
        self.password = password
        self.timeout = timeout
        self.retries = retries

    async def authenticate(self) -> str:
        if self.token:
            return self.token
        if not self.username or not self.password:
            raise HTTPException(400, "No Zabbix credentials configured")

        payload = {"jsonrpc": "2.0", "method": "user.login", "params": {"username": self.username, "password": self.password}, "id": 1}
        response = await request_json(
            method="POST",
            url=f"{self.base_url}/api_jsonrpc.php",
            headers={"Content-Type": "application/json-rpc"},
            json_body=payload,
            timeout=self.timeout,
            verify=self.tls_verify,
            retries=self.retries,
        )
        data = response.json()
        if "error" in data:
            raise HTTPException(401, f"Zabbix auth failed: {data['error'].get('data') or data['error'].get('message') or 'unknown auth error'}")
        result = data.get("result")
        if not isinstance(result, str) or not result:
            raise HTTPException(502, "Zabbix auth returned an invalid token")
        return result

    async def request(self, method_name: str, params: dict[str, Any], *, auth: str) -> dict[str, Any]:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method_name, "params": params, "id": 1}
        headers = {"Content-Type": "application/json-rpc"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        else:
            payload["auth"] = auth

        response = await request_json(
            method="POST",
            url=f"{self.base_url}/api_jsonrpc.php",
            headers=headers,
            json_body=payload,
            timeout=self.timeout,
            verify=self.tls_verify,
            retries=self.retries,
        )
        data = response.json()
        if "error" in data:
            raise HTTPException(400, f"Zabbix error: {data['error'].get('data') or data['error'].get('message') or 'unknown error'}")
        return {"request": sanitize_payload(payload), "response": data, "result": data.get("result")}
