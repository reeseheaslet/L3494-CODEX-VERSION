def build_emp_key(name: str) -> str:
    """
    Normalize employee name to a common comparison key.

    Converts:
        HEASLET, REESE J -> HEASLET, REESE
        HEASLET, REESE   -> HEASLET, REESE
    """
    if name is None:
        return ""

    name = str(name).strip().upper()

    if "," not in name:
        return name

    last, first_part = name.split(",", 1)

    first_part = first_part.strip()

    # Take only first word (drops middle initial)
    first = first_part.split(" ")[0]

    return f"{last.strip()}, {first.strip()}"
