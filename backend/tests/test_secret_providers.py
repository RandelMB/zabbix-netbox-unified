import base64
import os
import unittest
from unittest.mock import patch

from app.core.secret_providers import EnvironmentSecretProvider, env_secret_name_from_key, read_secret


class SecretProviderTests(unittest.TestCase):
    def test_env_secret_name_from_key(self) -> None:
        self.assertEqual(env_secret_name_from_key("observium/web_password"), "OBSERVIUM_WEB_PASSWORD")

    def test_environment_provider_reads_mapped_env_var(self) -> None:
        provider = EnvironmentSecretProvider()
        with patch.dict(os.environ, {"OBSERVIUM_WEB_PASSWORD": "secret-value"}, clear=False):
            self.assertEqual(provider.get_secret("observium/web_password"), "secret-value")

    def test_read_secret_prefers_direct_env(self) -> None:
        with patch.dict(os.environ, {"NETBOX_TOKEN": "abc123"}, clear=False):
            self.assertEqual(read_secret("netbox/token", direct_env="NETBOX_TOKEN"), "abc123")

    def test_read_secret_supports_base64_env(self) -> None:
        encoded = base64.b64encode(b"vault-like-secret").decode()
        with patch.dict(os.environ, {"OBSERVIUM_WEB_PASS_B64": encoded}, clear=False):
            self.assertEqual(read_secret("observium/web_password", direct_env_b64="OBSERVIUM_WEB_PASS_B64"), "vault-like-secret")
