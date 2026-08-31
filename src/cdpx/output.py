"""CLI output formatting.

The default is agent-optimized: compact JSON, one line, large values
bounded. Humans can request `--pretty`; a full audit can request
`--full`.
"""

from __future__ import annotations

from typing import Any

DEFAULT_LIMIT = 50
DEFAULT_MAX_CHARS = 4000


def bound(data: Any, *, full: bool = False, limit: int = DEFAULT_LIMIT) -> Any:
    if full:
        return data
    limit = max(1, limit)
    return _bound_value(data, limit=limit, root=True)


def bound_rgaa(data: Any, *, full: bool = False, limit: int = DEFAULT_LIMIT) -> Any:
    """Bound variable evidence while preserving schema-mandated audit arrays."""
    if full:
        return data
    return _bound_rgaa_value(data, limit=max(1, limit))


def _bound_rgaa_value(value: Any, *, limit: int) -> Any:
    structural = {"themes", "criteria", "tests", "pages"}
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(item, str) and len(item) > DEFAULT_MAX_CHARS:
                out[key] = item[:DEFAULT_MAX_CHARS]
                out[f"{key}_truncated"] = True
                out[f"{key}_chars"] = len(item)
            elif isinstance(item, list) and key not in structural and len(item) > limit:
                out[key] = [_bound_rgaa_value(entry, limit=limit) for entry in item[:limit]]
                out[f"{key}_truncated"] = True
                out[f"{key}_total"] = len(item)
                out[f"{key}_limit"] = limit
            else:
                out[key] = _bound_rgaa_value(item, limit=limit)
        return out
    if isinstance(value, list):
        return [_bound_rgaa_value(item, limit=limit) for item in value]
    return value


def _bound_value(value: Any, *, limit: int, root: bool = False) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(item, str) and len(item) > DEFAULT_MAX_CHARS:
                out[key] = item[:DEFAULT_MAX_CHARS]
                out[f"{key}_truncated"] = True
                out[f"{key}_chars"] = len(item)
            elif isinstance(item, list) and len(item) > limit:
                out[key] = [_bound_value(v, limit=limit) for v in item[:limit]]
                out[f"{key}_truncated"] = True
                out[f"{key}_total"] = len(item)
                out[f"{key}_limit"] = limit
            else:
                out[key] = _bound_value(item, limit=limit)
        return out
    if isinstance(value, list):
        # Root lists keep their published shape; nested lists get metadata from
        # their parent dict.
        items = value if root else value[:limit]
        return [_bound_value(v, limit=limit) for v in items]
    return value
