from __future__ import annotations

from typing import Any

from app.shared.text import compact_text


REDACTED = "***"
SENSITIVE_KEY_TOKENS = (
    "token",
    "password",
    "secret",
    "community",
    "cookie",
    "authpass",
    "cryptopass",
    "passphrase",
)
SENSITIVE_CLI_FLAGS = {"-A", "-X", "-c"}


def is_sensitive_key(key: str) -> bool:
    normalized = compact_text(key).replace("-", "_").lower()
    return any(token in normalized for token in SENSITIVE_KEY_TOKENS)


def sanitize_payload(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            sanitized[key] = REDACTED if is_sensitive_key(str(key)) and item not in (None, "") else sanitize_payload(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    return value


def redact_command_args(args: list[str]) -> list[str]:
    if not args:
        return []

    redacted = list(args)
    command_text = " ".join(args[:3]).lower()

    if "add_device.php" in command_text and len(redacted) > 3:
        return redacted[:3] + ["<redacted-snmp-params>"]

    if "snmp_check.sh" in command_text:
        for index in (4, 7, 9):
            if index < len(redacted) and redacted[index]:
                redacted[index] = REDACTED
        return redacted

    previous = ""
    for index, item in enumerate(redacted):
        if previous in SENSITIVE_CLI_FLAGS and item:
            redacted[index] = REDACTED
        previous = item

    return redacted
