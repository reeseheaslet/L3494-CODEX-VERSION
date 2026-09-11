from __future__ import annotations

KNOWN_NAME_ALIASES: dict[str, str] = {
    # First Due / alternate name -> canonical comparison name
    "FIX, MATTHEW": "FIX, MATT",
    "PUNA, WILLIAM": "PUNA JR, WILLIAM",
    "SHACKLEFORD, SAM": "SHACKELFORD, SAM",
    "PUSSICH, ROLAND": "PUSSICH JR, ROLAND",

    # Optional self-maps for readability / consistency
    "FIX, MATT": "FIX, MATT",
    "PUNA JR, WILLIAM": "PUNA JR, WILLIAM",
    "SHACKELFORD, SAM": "SHACKELFORD, SAM",
    "PUSSICH JR, ROLAND": "PUSSICH JR, ROLAND",
}


def canonicalize_employee_name(name: str | None) -> str | None:
    """
    Convert a normalized employee name into one canonical project name.

    Rules:
    - preserve None / missing values
    - trim whitespace
    - uppercase for consistency
    - apply known alias mapping if present
    """
    if name is None:
        return None

    normalized = str(name).strip().upper()
    if not normalized:
        return normalized

    return KNOWN_NAME_ALIASES.get(normalized, normalized)
