from typing import Any, Optional


def compact_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def normalize_key(value: Any) -> str:
    normalized = compact_text(value).lower()
    return "".join(char if char.isalnum() else " " for char in normalized).strip()


def slugify_text(value: Any) -> str:
    normalized = normalize_key(value).replace(" ", "-")
    return normalized.strip("-") or "auto-generated"


def as_int(value: Any) -> Optional[int]:
    try:
        if value in (None, ""):
            return None
        return int(str(value).strip())
    except Exception:
        return None
