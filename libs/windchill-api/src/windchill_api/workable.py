"""Helpers for Workable objects (parts, documents, CAD documents) and their checkout state."""

from __future__ import annotations

from typing import Any

from windchill_api.values import display, first, value

# CheckoutState values Windchill uses: "c/i" checked in, "c/o" checked-out original,
# "wrk" the working copy. Display text varies by locale, so both are checked.
CHECKED_OUT_VALUES = {"c/o", "checked out", "checkedout"}
WORKING_COPY_VALUES = {"wrk", "working", "working copy"}


def checkout_state(entity: dict[str, Any]) -> str:
    state = first(entity, "CheckoutState", "CheckOutStatus", default="")
    return str(value(state) or "").strip().lower()


def is_checked_out(entity: dict[str, Any]) -> bool:
    """True for the original of a checked-out object (not its working copy)."""
    state = checkout_state(entity)
    if state in CHECKED_OUT_VALUES:
        return True
    text = display(first(entity, "CheckoutState", "CheckOutStatus", default="")).lower()
    return "checked out" in text and "working" not in text


def is_working_copy(entity: dict[str, Any]) -> bool:
    return checkout_state(entity) in WORKING_COPY_VALUES


def checked_out_by(entity: dict[str, Any]) -> str:
    """Who holds the checkout. Windchill reports it under different names by release."""
    return display(first(entity, "CheckedOutBy", "Locker", "LockedBy", "ModifiedBy", default=""))
