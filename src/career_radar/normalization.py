def normalize_skill_name(skill_name: str) -> str:
    """Create the comparison form used for skill grouping and deduplication."""

    return " ".join(skill_name.casefold().split())
