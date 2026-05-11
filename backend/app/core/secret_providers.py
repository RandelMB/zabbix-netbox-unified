import base64
import importlib
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional, Protocol

import httpx


class SecretProvider(Protocol):
    def get_secret(self, key: str) -> str:
        ...

    def set_secret(self, key: str, value: str) -> None:
        ...


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_secret_name_from_key(key: str) -> str:
    return key.replace("/", "_").replace(".", "_").upper()


class EnvironmentSecretProvider:
    def get_secret(self, key: str) -> str:
        return os.getenv(env_secret_name_from_key(key), "")

    def set_secret(self, key: str, value: str) -> None:
        os.environ[env_secret_name_from_key(key)] = value


@dataclass
class VaultConfig:
    addr: str
    token: str
    mount: str
    path_prefix: str
    kv_version: str


class VaultSecretProvider:
    def __init__(self, config: VaultConfig):
        self.config = config

    def _split_key(self, key: str) -> tuple[str, str]:
        parts = [part for part in key.strip("/").split("/") if part]
        if not parts:
            return ("", "value")
        path_parts = parts[:-1] or parts
        field = parts[-1] if len(parts) > 1 else "value"
        full_path = "/".join(part for part in [self.config.path_prefix.strip("/"), "/".join(path_parts).strip("/")] if part)
        return (full_path, field)

    def _request_path(self, path: str) -> str:
        mount = self.config.mount.strip("/")
        if self.config.kv_version == "v2":
            return f"/v1/{mount}/data/{path}"
        return f"/v1/{mount}/{path}"

    def _payload_data(self, payload: dict) -> dict:
        if self.config.kv_version == "v2":
            return ((payload.get("data") or {}).get("data") or {})
        return payload.get("data") or {}

    def get_secret(self, key: str) -> str:
        path, field = self._split_key(key)
        if not self.config.addr or not self.config.token or not path:
            return ""
        headers = {"X-Vault-Token": self.config.token}
        verify_tls = _env_flag("VAULT_TLS_VERIFY", _env_flag("OUTBOUND_TLS_VERIFY", True))
        with httpx.Client(base_url=self.config.addr.rstrip("/"), timeout=15, verify=verify_tls) as client:
            response = client.get(self._request_path(path), headers=headers)
            response.raise_for_status()
            data = self._payload_data(response.json())
        value = data.get(field, "")
        return value if isinstance(value, str) else str(value or "")

    def set_secret(self, key: str, value: str) -> None:
        path, field = self._split_key(key)
        if not self.config.addr or not self.config.token or not path:
            raise ValueError("Vault provider is not configured")
        headers = {"X-Vault-Token": self.config.token}
        body = {"data": {field: value}} if self.config.kv_version == "v2" else {field: value}
        verify_tls = _env_flag("VAULT_TLS_VERIFY", _env_flag("OUTBOUND_TLS_VERIFY", True))
        with httpx.Client(base_url=self.config.addr.rstrip("/"), timeout=15, verify=verify_tls) as client:
            response = client.post(self._request_path(path), headers=headers, json=body)
            response.raise_for_status()


class CustomSecretProvider:
    def __init__(self, import_path: str):
        if ":" in import_path:
            module_name, class_name = import_path.split(":", 1)
        else:
            module_name, class_name = import_path.rsplit(".", 1)
        module = importlib.import_module(module_name)
        provider_cls = getattr(module, class_name)
        self.provider = provider_cls()

    def get_secret(self, key: str) -> str:
        value = self.provider.get_secret(key)
        return value if isinstance(value, str) else str(value or "")

    def set_secret(self, key: str, value: str) -> None:
        if not hasattr(self.provider, "set_secret"):
            raise NotImplementedError("Custom provider does not support set_secret")
        self.provider.set_secret(key, value)


@lru_cache(maxsize=1)
def get_secret_provider() -> SecretProvider:
    provider_name = os.getenv("SECRET_PROVIDER", "environment").strip().lower()
    if provider_name in {"", "environment", "env"}:
        return EnvironmentSecretProvider()
    if provider_name == "vault":
        return VaultSecretProvider(
            VaultConfig(
                addr=os.getenv("VAULT_ADDR", ""),
                token=os.getenv("VAULT_TOKEN", ""),
                mount=os.getenv("VAULT_KV_MOUNT", "secret"),
                path_prefix=os.getenv("VAULT_KV_PATH_PREFIX", "zabbix-netbox-unified"),
                kv_version=os.getenv("VAULT_KV_VERSION", "v2").lower(),
            )
        )
    if provider_name == "custom":
        return CustomSecretProvider(os.getenv("CUSTOM_SECRET_PROVIDER_CLASS", ""))
    raise ValueError(f"Unsupported secret provider: {provider_name}")


def read_secret(key: str, *, direct_env: str = "", direct_env_b64: str = "", default: str = "") -> str:
    if direct_env:
        direct_value = os.getenv(direct_env, "")
        if direct_value:
            return direct_value
    if direct_env_b64:
        encoded = os.getenv(direct_env_b64, "")
        if encoded:
            try:
                return base64.b64decode(encoded).decode("utf-8")
            except Exception:
                return default
    try:
        value = get_secret_provider().get_secret(key)
    except Exception:
        value = ""
    return value or default
