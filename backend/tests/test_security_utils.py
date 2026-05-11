import unittest

from app.shared.security import REDACTED, redact_command_args, sanitize_payload


class SecurityUtilsTests(unittest.TestCase):
    def test_sanitize_payload_redacts_sensitive_keys(self) -> None:
        value = sanitize_payload({"snmp_community": "public", "nested": {"token": "abc", "safe": "ok"}})
        self.assertEqual(value["snmp_community"], REDACTED)
        self.assertEqual(value["nested"]["token"], REDACTED)
        self.assertEqual(value["nested"]["safe"], "ok")

    def test_redact_command_args_masks_snmp_cli_secrets(self) -> None:
        args = ["snmpget", "-v3", "-A", "auth-secret", "-X", "priv-secret", "-c", "public"]
        redacted = redact_command_args(args)
        self.assertEqual(redacted[3], REDACTED)
        self.assertEqual(redacted[5], REDACTED)
        self.assertEqual(redacted[7], REDACTED)
