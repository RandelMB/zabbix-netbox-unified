import os
from typing import Any, Optional

from app.core.secret_providers import read_secret


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    OUTBOUND_TLS_VERIFY: bool = env_flag("OUTBOUND_TLS_VERIFY", True)
    VAULT_TLS_VERIFY: bool = env_flag("VAULT_TLS_VERIFY", OUTBOUND_TLS_VERIFY)
    ZABBIX_TLS_VERIFY: bool = env_flag("ZABBIX_TLS_VERIFY", OUTBOUND_TLS_VERIFY)
    NETBOX_TLS_VERIFY: bool = env_flag("NETBOX_TLS_VERIFY", OUTBOUND_TLS_VERIFY)
    OBSERVIUM_WEB_TLS_VERIFY: bool = env_flag("OBSERVIUM_WEB_TLS_VERIFY", OUTBOUND_TLS_VERIFY)
    SECRET_PROVIDER: str = os.getenv("SECRET_PROVIDER", "environment")
    CUSTOM_SECRET_PROVIDER_CLASS: str = os.getenv("CUSTOM_SECRET_PROVIDER_CLASS", "")
    VAULT_ADDR: str = os.getenv("VAULT_ADDR", "")
    VAULT_TOKEN: str = os.getenv("VAULT_TOKEN", "")
    VAULT_KV_MOUNT: str = os.getenv("VAULT_KV_MOUNT", "secret")
    VAULT_KV_PATH_PREFIX: str = os.getenv("VAULT_KV_PATH_PREFIX", "zabbix-netbox-unified")
    VAULT_KV_VERSION: str = os.getenv("VAULT_KV_VERSION", "v2")
    ZABBIX_URL: str = os.getenv("ZABBIX_URL", "")
    ZABBIX_TOKEN: str = read_secret("zabbix/token", direct_env="ZABBIX_TOKEN")
    ZABBIX_USER: str = os.getenv("ZABBIX_USER", "")
    ZABBIX_PASS: str = read_secret("zabbix/password", direct_env="ZABBIX_PASS")
    NETBOX_URL: str = os.getenv("NETBOX_URL", "")
    NETBOX_TOKEN: str = read_secret("netbox/token", direct_env="NETBOX_TOKEN")
    OBSERVIUM_BASE_URL: str = os.getenv("OBSERVIUM_BASE_URL", "")
    OBSERVIUM_WEB_USER: str = os.getenv("OBSERVIUM_WEB_USER", "")
    OBSERVIUM_WEB_PASS: str = read_secret(
        "observium/web_password",
        direct_env="OBSERVIUM_WEB_PASS",
        direct_env_b64="OBSERVIUM_WEB_PASS_B64",
    )
    OBSERVIUM_WEB_PASS_B64: str = os.getenv("OBSERVIUM_WEB_PASS_B64", "")
    OBSERVIUM_DB_HOST: str = os.getenv("OBSERVIUM_DB_HOST", "mariadb")
    OBSERVIUM_DB_PORT: int = int(os.getenv("OBSERVIUM_DB_PORT", "3306"))
    OBSERVIUM_DB_NAME: str = os.getenv("OBSERVIUM_DB_NAME", "")
    OBSERVIUM_DB_USER: str = os.getenv("OBSERVIUM_DB_USER", "")
    OBSERVIUM_DB_PASSWORD: str = read_secret("observium/db_password", direct_env="OBSERVIUM_DB_PASSWORD")
    OBSERVIUM_CONTAINER: str = os.getenv("OBSERVIUM_CONTAINER", "observium-app")
    OBSERVIUM_ENABLE_POLLER: bool = env_flag("OBSERVIUM_ENABLE_POLLER", False)
    OBSERVIUM_DISCOVERY_MODULES: str = os.getenv(
        "OBSERVIUM_DISCOVERY_MODULES",
        "os,ports,ports-stack,vlans,inventory,neighbours,ip-addresses,arp-table,mibs",
    )
    APP_DB_PATH: str = os.getenv("APP_DB_PATH", "/app/data/zneditor.db")


cfg = Config()
runtime_creds: dict[str, Any] = {}
zabbix_auth_token: Optional[str] = None


def observium_web_password() -> str:
    return cfg.OBSERVIUM_WEB_PASS
