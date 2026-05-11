from __future__ import annotations

import asyncio
from typing import Any, Iterable

import httpx
from fastapi import HTTPException


RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


async def request_json(
    *,
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    params: dict[str, Any] | None = None,
    timeout: float = 15.0,
    verify: bool = True,
    retries: int = 0,
    retryable_status_codes: Iterable[int] = RETRYABLE_STATUS_CODES,
) -> httpx.Response:
    attempts = retries + 1
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            async with httpx.AsyncClient(verify=verify, timeout=timeout) as client:
                response = await client.request(method, url, headers=headers, json=json_body, params=params)
            if response.status_code in retryable_status_codes and attempt < attempts:
                await asyncio.sleep(min(0.25 * attempt, 1.0))
                continue
            return response
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
            last_error = exc
            if attempt >= attempts:
                break
            await asyncio.sleep(min(0.25 * attempt, 1.0))
    raise HTTPException(503, f"Upstream request failed: {last_error or 'unknown transport error'}")
