import re
from typing import Any

from app.shared.text import compact_text, slugify_text


def is_vlan_tag_slug(slug: str) -> bool:
    return compact_text(slug).lower().startswith("vlan-")


def merge_interface_vlan_tags(existing_tags: list[dict[str, Any]], vlan_tag_ids: list[int]) -> list[int]:
    preserved = [
        int(tag["id"])
        for tag in existing_tags
        if tag.get("id") is not None and not is_vlan_tag_slug(compact_text(tag.get("slug")))
    ]
    merged = preserved + [int(tag_id) for tag_id in vlan_tag_ids if tag_id is not None]
    deduped: list[int] = []
    for tag_id in merged:
        if tag_id not in deduped:
            deduped.append(tag_id)
    return deduped


def summarize_vlan_ids(vlan_values: list[Any]) -> list[str]:
    values = []
    for item in vlan_values or []:
        value = compact_text(item)
        if value:
            values.append(value)
    if not values:
        return []

    numbers: list[int] = []
    non_numeric: list[str] = []
    for value in values:
        try:
            numbers.append(int(value))
        except Exception:
            if value not in non_numeric:
                non_numeric.append(value)

    ranges: list[str] = []
    if numbers:
        ordered = sorted(set(numbers))
        start = ordered[0]
        end = ordered[0]
        for number in ordered[1:]:
            if number == end + 1:
                end = number
                continue
            ranges.append(f"{start}-{end}" if start != end else str(start))
            start = number
            end = number
        ranges.append(f"{start}-{end}" if start != end else str(start))
    return ranges + non_numeric


def vlan_token_sort_key(token: Any) -> tuple[int, int, str]:
    value = compact_text(token)
    if not value:
        return (3, 0, "")
    if re.fullmatch(r"\d+", value):
        return (0, int(value), value)
    match = re.fullmatch(r"(\d+)-(\d+)", value)
    if match:
        return (0, int(match.group(1)), value)
    return (2, 0, value.lower())


def vlan_representation_tokens(vlan_values: list[Any]) -> list[str]:
    normalized = [compact_text(item) for item in vlan_values or [] if compact_text(item)]
    if not normalized:
        return []

    numeric_values: list[int] = []
    non_numeric: list[str] = []
    for item in normalized:
        try:
            numeric_values.append(int(item))
        except Exception:
            if item not in non_numeric:
                non_numeric.append(item)

    tokens: list[str] = []
    ordered = sorted(set(numeric_values))
    if ordered:
        range_size = ordered[-1] - ordered[0] + 1
        density = (len(ordered) / range_size) if range_size > 0 else 0
        if len(ordered) <= 15:
            tokens.extend(str(item) for item in ordered)
        elif len(ordered) > 50 and density >= 0.7:
            tokens.append(f"{ordered[0]}-{ordered[-1]}")
        else:
            tokens.extend(summarize_vlan_ids([str(item) for item in ordered]))

    tokens.extend(non_numeric)
    deduped: list[str] = []
    for token in tokens:
        if token not in deduped:
            deduped.append(token)
    return deduped


def vlan_tag_name_and_slug(token: str) -> tuple[str, str]:
    normalized = compact_text(token)
    return (f"VLAN {normalized}", slugify_text(f"vlan-{normalized}")[:100])


def interface_vlan_representation(tags: list[dict[str, Any]]) -> list[str]:
    tokens: list[str] = []
    for tag in tags or []:
        slug = compact_text(tag.get("slug")).lower()
        if not is_vlan_tag_slug(slug):
            continue
        token = compact_text(slug[5:]).replace("--", "-")
        if token and token not in tokens:
            tokens.append(token)
    return sorted(tokens, key=vlan_token_sort_key)
