import unittest
from unittest.mock import AsyncMock

from fastapi import HTTPException

from app.schemas.snmp import NetBoxSnmpProbePayload
from app.services.snmp_discovery import SnmpDiscoveryRuntime, build_netbox_snmp_probe, snmp_exec_args


def runtime_stub() -> SnmpDiscoveryRuntime:
    return SnmpDiscoveryRuntime(
        observium_exec_result=lambda args: {"command": args, "output": "stub", "exit_code": 0},
        observium_find_device_by_identity=AsyncMock(return_value=None),
        zabbix_find_host_by_identity=AsyncMock(return_value=None),
        netbox_find_device_by_identity=AsyncMock(return_value=None),
        netbox_find_device_type_by_model=AsyncMock(return_value=None),
        ensure_netbox_device_type=AsyncMock(return_value={}),
        ensure_netbox_platform=AsyncMock(return_value={}),
        ensure_netbox_primary_ip4=AsyncMock(return_value={}),
        netbox_request=AsyncMock(return_value={}),
        normalize_ip_value=lambda ip: f"{ip}/32",
        build_os_version_label=lambda os_name, version: " ".join(part for part in [os_name, version] if part).strip(),
    )


class SnmpDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    def test_snmp_v3_requires_username(self) -> None:
        payload = NetBoxSnmpProbePayload(ip="10.0.0.1", snmp_version="v3", snmp_authlevel="authPriv")
        with self.assertRaises(HTTPException) as ctx:
            snmp_exec_args(payload, ".1.3.6.1.2.1.1.2.0")
        self.assertEqual(ctx.exception.status_code, 400)

    def test_snmp_v3_builds_authpriv_args(self) -> None:
        payload = NetBoxSnmpProbePayload(
            ip="10.0.0.1",
            snmp_version="v3",
            snmp_authlevel="authPriv",
            snmp_authname="netops",
            snmp_authpass="auth-secret",
            snmp_authalgo="SHA-256",
            snmp_cryptopass="priv-secret",
            snmp_cryptoalgo="AES256",
            snmp_context="ctx-1",
        )
        args = snmp_exec_args(payload, ".1.3.6.1.2.1.1.2.0")
        self.assertIn("authPriv", args)
        self.assertIn("netops", args)
        self.assertIn("auth-secret", args)
        self.assertIn("priv-secret", args)
        self.assertIn("ctx-1", args)

    async def test_probe_response_redacts_sensitive_input(self) -> None:
        oid_outputs = {
            ".1.3.6.1.2.1.1.5.0": "switch-a",
            ".1.3.6.1.2.1.1.1.0": "Cisco IOS Software, C9300 Version 17.9",
            ".1.3.6.1.2.1.1.6.0": "HQ-01",
            ".1.3.6.1.2.1.1.2.0": ".1.3.6.1.4.1.9.1.1208",
            ".1.3.6.1.2.1.47.1.1.1.1.11.1": "SER123",
        }

        def fake_exec(args):
            return {"command": args, "output": oid_outputs.get(args[-1], ""), "exit_code": 0}

        runtime = runtime_stub()
        runtime.observium_exec_result = fake_exec
        payload = NetBoxSnmpProbePayload(ip="10.0.0.1", snmp_version="v2c", snmp_community="secret-ro")
        result = await build_netbox_snmp_probe(runtime, payload)
        self.assertEqual(result["input"]["snmp_community"], "***")
        self.assertEqual(result["credential_mode"], "community")
