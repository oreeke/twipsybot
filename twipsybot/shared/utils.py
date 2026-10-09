from typing import Any

__all__ = ("normalize_tokens",)


def normalize_tokens(value: Any, *, lower: bool = False) -> list[str]:
    if value is None or isinstance(value, bool):
        return []
    if isinstance(value, str):
        tokens = [t.strip() for t in value.replace(",", " ").split() if t.strip()]
    elif isinstance(value, list):
        tokens = [str(v).strip() for v in value if v is not None and str(v).strip()]
    else:
        s = str(value).strip()
        tokens = [s] if s else []
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        k = t.lower() if lower else t
        if k in seen:
            continue
        seen.add(k)
        out.append(k if lower else t)
    return out
