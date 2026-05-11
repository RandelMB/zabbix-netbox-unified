from __future__ import annotations


from app.repositories.archive_repository import archived_ids


def apply_archive_mode(source: str, items: list[dict], id_field: str, mode: str) -> list[dict]:
    archived = archived_ids(source)
    filtered: list[dict] = []
    for item in items:
        archived_flag = str(item.get(id_field)) in archived
        item["archived"] = archived_flag
        if mode == "exclude" and archived_flag:
            continue
        if mode == "only" and not archived_flag:
            continue
        filtered.append(item)
    return filtered
