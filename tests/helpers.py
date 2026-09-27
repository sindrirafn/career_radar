import csv
from pathlib import Path

from career_radar.sources.chatgpt_inbox import REQUIRED_COLUMNS


def inbox_values(number=1, **overrides):
    values = dict.fromkeys(REQUIRED_COLUMNS, "")
    values.update(
        title=f"Data engineer {number}", company="Test company", source="Test",
        url=f"https://example.com/jobs/{number}", fit_tier="strong",
        match_score_0_to_100="85", explicit_skills="SQL; Python",
        category_guess="data", fit_reason="SQL and Python are requested.",
    )
    values.update(overrides)
    return values


def write_inbox(directory, *rows):
    path = Path(directory) / "inbox.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=sorted(REQUIRED_COLUMNS))
        writer.writeheader()
        writer.writerows(rows or [inbox_values()])
    return path
