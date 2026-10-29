"""Rules for names Windchill will reject or that cause trouble downstream (CAD, file systems)."""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable

# Characters Windchill does not allow in folder names.
DEFAULT_ILLEGAL_CHARACTERS = frozenset('/\\:*?"<>|')


def find_name_problems(
    name: str,
    illegal_characters: Iterable[str] = DEFAULT_ILLEGAL_CHARACTERS,
    *,
    check_hidden: bool = True,
    check_whitespace: bool = True,
) -> list[str]:
    """Return a description of each problem with a name (empty if it is fine)."""
    illegal = set(illegal_characters)
    problems = []
    found = sorted({c for c in name if c in illegal})
    if found:
        problems.append("illegal characters: " + " ".join(found))
    if check_hidden:
        # Cc = control, Cf = format (zero-width etc.), Co/Cs/Cn = private/surrogate/unassigned
        hidden = sorted({c for c in name if unicodedata.category(c).startswith("C")})
        if hidden:
            problems.append("hidden characters: " + " ".join(f"U+{ord(c):04X}" for c in hidden))
    if check_whitespace and name != name.strip():
        problems.append("leading/trailing whitespace")
    return problems


def visible(text: str) -> str:
    """Escape hidden characters so they show up in a text report."""
    if any(unicodedata.category(c).startswith("C") for c in text):
        return text.encode("unicode_escape").decode("ascii")
    return text
