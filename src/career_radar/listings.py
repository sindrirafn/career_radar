from __future__ import annotations

from dataclasses import dataclass


def clean_optional_text(value: str | None) -> str | None:
    """Normalize optional source text before it reaches the database layer."""

    if value is None:
        return None

    cleaned_value = value.strip()
    return cleaned_value or None


@dataclass(frozen=True, slots=True)
class JobListingInput:
    """Standard job-listing shape accepted by the ingestion pipeline.

    Each source adapter should convert its own source data into this structure.
    CSV rows, API responses, and scraped pages can all flow through the same
    ingestion logic once they use this shared shape.
    """

    title: str
    company: str
    source: str
    url: str
    location: str | None = None
    posted_date: str | None = None
    deadline: str | None = None
    category_guess: str | None = None
    description: str | None = None
    requirements: str | None = None
    skills_mentioned: str | None = None
    notes: str | None = None
    source_file: str | None = None
    source_row_number: int | None = None

    def __post_init__(self) -> None:
        """Validate required fields and trim optional text fields."""

        for field_name in ("title", "company", "source", "url"):
            cleaned_value = clean_optional_text(getattr(self, field_name))
            if cleaned_value is None:
                raise ValueError(f"{field_name} is required.")
            object.__setattr__(self, field_name, cleaned_value)

        optional_text_fields = (
            "location",
            "posted_date",
            "deadline",
            "category_guess",
            "description",
            "requirements",
            "skills_mentioned",
            "notes",
            "source_file",
        )
        for field_name in optional_text_fields:
            object.__setattr__(
                self,
                field_name,
                clean_optional_text(getattr(self, field_name)),
            )

        if self.source_row_number is not None and self.source_row_number < 1:
            raise ValueError("source_row_number must be positive when provided.")
