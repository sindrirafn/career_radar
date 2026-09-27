from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from career_radar.listings import JobListingInput


ALFRED_JOBS_URL = "https://www.alfred.is/jobs"
SOURCE_NAME = "Alfreð"
USER_AGENT = "career-radar-local-learning/0.1"
NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)
DISPLAYED_COUNT_RE = re.compile(
    r"(?:Öll störf|All jobs)\s*\(\s*(\d+)\s*\)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class AlfredPageDiagnostic:
    """Evidence about how complete the public jobs page payload is."""

    displayed_job_count: int | None
    payload_total_count: int | None
    payload_total_pages: int | None
    embedded_job_count: int
    unique_slug_count: int
    duplicate_slug_count: int
    jobs_missing_slug_count: int

    @property
    def appears_complete(self) -> bool:
        """Return whether the page payload matches its visible job count."""

        return (
            self.embedded_job_count == (self.payload_total_count or self.displayed_job_count)
            and self.duplicate_slug_count == 0
            and self.jobs_missing_slug_count == 0
        )


class HTMLTextExtractor(HTMLParser):
    """Extract readable text from small HTML fragments in Alfreð job data."""

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        cleaned_data = data.strip()
        if cleaned_data:
            self._chunks.append(cleaned_data)

    def text(self) -> str:
        return "\n".join(self._chunks)


def fetch_alfred_jobs_page(url: str = ALFRED_JOBS_URL) -> str:
    """Fetch the public Alfreð jobs page.

    We intentionally fetch the normal jobs page instead of a hidden API
    endpoint. Alfreð's robots.txt allows ordinary page access and disallows
    /api/* paths.
    """

    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8")


def html_fragment_to_text(html_fragment: str | None) -> str | None:
    """Convert a small HTML fragment into readable plain text."""

    if not html_fragment:
        return None

    parser = HTMLTextExtractor()
    parser.feed(html_fragment)
    extracted_text = parser.text().strip()
    return extracted_text or None


def iso_datetime_to_date(value: str | None) -> str | None:
    """Convert Alfreð ISO datetimes into YYYY-MM-DD strings for raw storage."""

    if not value:
        return None

    normalized_value = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized_value).date().isoformat()
    except ValueError:
        return None


def location_from_job(job: dict[str, Any]) -> str | None:
    """Build a readable location string from the first-page Alfreð payload."""

    addresses = job.get("addresses") or []
    location_parts = []

    for address in addresses:
        formatted = address.get("formatted")
        sub_locality = address.get("subLocality")
        location = ", ".join(
            part
            for part in (formatted, sub_locality)
            if isinstance(part, str) and part.strip()
        )
        if location:
            location_parts.append(location)

    if location_parts:
        return "; ".join(location_parts)

    return None


def notes_from_job(job: dict[str, Any]) -> str:
    """Keep source-specific details that do not deserve their own columns yet."""

    notes = [f"alfred_id={job.get('id')}", f"slug={job.get('slug')}"]

    job_types = job.get("jobTypes") or []
    if job_types:
        notes.append(f"job_types={', '.join(job_types)}")

    status = job.get("status") or {}
    status_value = status.get("value")
    if status_value:
        notes.append(f"alfred_status={status_value}")

    if job.get("expired") is not None:
        notes.append(f"expired={job.get('expired')}")

    return "; ".join(notes)


def description_from_job(job: dict[str, Any]) -> str | None:
    """Prefer readable text from HTML fragments, with plain description fallback."""

    body_text = html_fragment_to_text(job.get("bodyhtml"))
    footer_text = html_fragment_to_text(job.get("footer"))
    text_parts = [part for part in (body_text, footer_text) if part]

    if text_parts:
        return "\n\n".join(text_parts)

    description = job.get("description")
    if isinstance(description, str) and description.strip():
        return description.strip()

    return None


def _alfred_jobs_payload(
    html_text: str,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Read the public Next.js payload and its job objects."""

    match = NEXT_DATA_RE.search(html_text)
    if not match:
        raise RuntimeError("Could not find __NEXT_DATA__ in the Alfreð jobs page.")

    page_data = json.loads(match.group(1))
    jobs_payload = page_data["props"]["pageProps"]["jobs"]
    jobs = jobs_payload["jobs"]
    if not isinstance(jobs, list):
        raise RuntimeError("Alfreð jobs payload is not a list.")

    return page_data, jobs_payload, jobs


def diagnose_alfred_jobs_page(html_text: str) -> AlfredPageDiagnostic:
    """Check whether one public page contains the complete visible inventory."""

    _, jobs_payload, jobs = _alfred_jobs_payload(html_text)
    slugs = [
        job.get("slug")
        for job in jobs
        if isinstance(job, dict) and isinstance(job.get("slug"), str)
    ]
    displayed_match = DISPLAYED_COUNT_RE.search(html_text)

    return AlfredPageDiagnostic(
        displayed_job_count=(
            int(displayed_match.group(1)) if displayed_match else None
        ),
        payload_total_count=(
            jobs_payload.get("totalCount")
            if isinstance(jobs_payload.get("totalCount"), int)
            else None
        ),
        payload_total_pages=(
            jobs_payload.get("totalPages")
            if isinstance(jobs_payload.get("totalPages"), int)
            else None
        ),
        embedded_job_count=len(jobs),
        unique_slug_count=len(set(slugs)),
        duplicate_slug_count=len(slugs) - len(set(slugs)),
        jobs_missing_slug_count=len(jobs) - len(slugs),
    )


def listing_from_alfred_job(job: dict[str, Any], source_url: str) -> JobListingInput:
    """Convert one Alfreð page job object into the standard ingestion shape."""

    slug = job.get("slug")
    if not isinstance(slug, str) or not slug.strip():
        raise RuntimeError("Alfreð job is missing a slug.")

    brand = job.get("brand") or {}
    company = brand.get("name")
    if not isinstance(company, str) or not company.strip():
        company = "Unknown company"

    return JobListingInput(
        title=job["title"],
        company=company,
        source=SOURCE_NAME,
        url=urljoin(source_url, f"/starf/{slug}"),
        location=location_from_job(job),
        posted_date=iso_datetime_to_date(job.get("published")),
        deadline=iso_datetime_to_date(job.get("deadline")),
        description=description_from_job(job),
        notes=notes_from_job(job),
    )


def parse_alfred_jobs_page(
    html_text: str,
    source_url: str = ALFRED_JOBS_URL,
) -> list[JobListingInput]:
    """Parse the public Alfreð jobs page into standard listing inputs."""

    _, _, jobs = _alfred_jobs_payload(html_text)

    listings = []
    for job in jobs:
        listings.append(listing_from_alfred_job(job, source_url))

    return listings


def collect_alfred_jobs(limit: int | None = None) -> list[JobListingInput]:
    """Fetch and parse the first public Alfreð jobs page."""

    html_text = fetch_alfred_jobs_page()
    listings = parse_alfred_jobs_page(html_text)

    if limit is not None:
        return listings[:limit]

    return listings
