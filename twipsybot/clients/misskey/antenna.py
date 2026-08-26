from typing import Any

__all__ = (
    "build_antenna_index",
    "dedupe_non_empty",
    "resolve_antenna_selector",
)


def build_antenna_index(
    antennas: Any,
) -> tuple[set[str], dict[str, list[str]], dict[str, str]]:
    antenna_ids: set[str] = set()
    name_to_ids: dict[str, list[str]] = {}
    id_to_name: dict[str, str] = {}
    if not isinstance(antennas, list):
        return antenna_ids, name_to_ids, id_to_name
    for antenna in antennas:
        if not isinstance(antenna, dict):
            continue
        antenna_id = antenna.get("id")
        if not isinstance(antenna_id, str) or not antenna_id:
            continue
        antenna_ids.add(antenna_id)
        name = antenna.get("name")
        if isinstance(name, str) and (normalized := name.strip()):
            name_to_ids.setdefault(normalized, []).append(antenna_id)
            id_to_name[antenna_id] = normalized
    return antenna_ids, name_to_ids, id_to_name


def resolve_antenna_selector(
    selector: str, antenna_ids: set[str], name_to_ids: dict[str, list[str]]
) -> tuple[str, str | None]:
    if selector in antenna_ids:
        return selector, None
    candidates = name_to_ids.get(selector)
    if not candidates:
        lowered = selector.lower()
        merged = [
            antenna_id
            for name, ids in name_to_ids.items()
            if name.lower() == lowered
            for antenna_id in ids
        ]
        candidates = list(dict.fromkeys(merged))
    if not candidates:
        return "", "not_found"
    if len(candidates) != 1:
        return "", "ambiguous"
    return candidates[0], None


def dedupe_non_empty(values: list[str]) -> list[str]:
    return list(dict.fromkeys(v for v in values if v))
