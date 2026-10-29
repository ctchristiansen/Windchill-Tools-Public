"""Helpers for reading values out of Windchill OData entities."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def value(v: Any) -> Any:
    """Return the internal value of an enum-style property (``{"Value": ..., "Display": ...}``)."""
    if isinstance(v, dict):
        return v.get("Value", v.get("Display"))
    return v


def display(v: Any) -> str:
    """Return the display text of a property, whether it is an enum, a nested entity or a scalar."""
    if v is None:
        return ""
    if isinstance(v, dict):
        for key in ("Display", "Name", "FullName", "Value", "ID"):
            if v.get(key) not in (None, ""):
                return str(v[key])
        return ""
    return str(v)


def first(entity: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Return the first property in ``keys`` that is present and not empty."""
    for key in keys:
        v = entity.get(key)
        if v not in (None, "", [], {}):
            return v
    return default


def parse_datetime(text: str | None) -> datetime | None:
    """Parse an OData timestamp (``2026-10-28T08:16:39Z``) into an aware datetime."""
    if not text:
        return None
    text = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        # Python < 3.11 cannot parse fractional seconds that are not 3 or 6 digits long.
        try:
            dt = datetime.fromisoformat(text[:19])
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def odata_datetime(dt: datetime) -> str:
    """Format a datetime as an OData ``DateTimeOffset`` literal for use in ``$filter``."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def age_days(dt: datetime | None, now: datetime | None = None) -> float | None:
    if dt is None:
        return None
    now = now or datetime.now(timezone.utc)
    return (now - dt).total_seconds() / 86400
