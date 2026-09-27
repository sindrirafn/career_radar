from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from career_radar.db import connect, transaction
from career_radar.listings import JobListingInput
from career_radar.normalization import normalize_skill_name


INSERT_RAW_JOB_SQL = """
insert into raw_job_listings (
    title,
    company,
    source,
    url,
    canonical_url,
    location,
    posted_date_raw,
    deadline_raw,
    category_guess,
    description,
    requirements,
    skills_mentioned,
    notes,
    source_file,
    source_row_number
) values (
    %(title)s,
    %(company)s,
    %(source)s,
    %(url)s,
    %(canonical_url)s,
    %(location)s,
    %(posted_date_raw)s,
    %(deadline_raw)s,
    %(category_guess)s,
    %(description)s,
    %(requirements)s,
    %(skills_mentioned)s,
    %(notes)s,
    %(source_file)s,
    %(source_row_number)s
)
on conflict (canonical_url) do update
set
    title = excluded.title,
    company = excluded.company,
    source = excluded.source,
    url = excluded.url,
    location = excluded.location,
    posted_date_raw = excluded.posted_date_raw,
    deadline_raw = excluded.deadline_raw,
    category_guess = excluded.category_guess,
    description = excluded.description,
    requirements = excluded.requirements,
    skills_mentioned = excluded.skills_mentioned,
    notes = excluded.notes,
    source_file = excluded.source_file,
    source_row_number = excluded.source_row_number,
    last_seen_at = now()
returning id;
"""

UPSERT_SKILL_SQL = """
insert into skills (name, normalized_name)
values (%(name)s, %(normalized_name)s)
on conflict (normalized_name) do update
set name = skills.name
returning id;
"""

INSERT_JOB_SKILL_SQL = """
insert into job_listing_skills (
    job_listing_id,
    skill_id,
    source_text
) values (
    %(job_listing_id)s,
    %(skill_id)s,
    %(source_text)s
)
on conflict do nothing;
"""


@dataclass(frozen=True, slots=True)
class IngestionResult:
    """Summary returned after a listing source has been ingested."""

    processed_listing_count: int
    unique_job_count: int
    unique_skill_count: int
    job_skill_link_count: int
    merged_listing_count: int


def canonicalize_url(url: str) -> str:
    """Create the stable URL identity used for listing deduplication.

    The goal is not to invent a universal web crawler's URL normalizer. It is
    to remove the specific noise that appears in our job-listing sources:
    tracking query parameters, ``www.``, trailing slashes, and Alfred language
    prefixes that still point at the same Icelandic job listing.
    """

    stripped_url = url.strip()
    parts = urlsplit(stripped_url)

    if not parts.netloc:
        return stripped_url

    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]

    path = re.sub(r"/+", "/", parts.path).rstrip("/")
    if netloc == "alfred.is":
        path = re.sub(r"^/(en|is|uk|pl)(?=/starf/)", "", path, flags=re.IGNORECASE)

    tracking_query_keys = {"fbclid", "gclid", "msclkid"}
    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
        and key.lower() not in tracking_query_keys
    ]
    query = urlencode(query_pairs, doseq=True)

    return urlunsplit((scheme, netloc, path, query, ""))


def normalize_skill(skill: str) -> str:
    """Create the comparison form used for grouping and deduplication."""

    return normalize_skill_name(skill)


def split_skills(skills_mentioned: str | None) -> list[str]:
    """Split semicolon-separated skill text into unique skill names.

    The raw listing keeps skills in one text field because that is how the
    first manually collected dataset was produced. The database stores the
    split skills separately because recurring skills are one of the main things
    this project is meant to analyze.
    """

    if not skills_mentioned:
        return []

    skills = []
    seen_normalized_names = set()

    for raw_skill in skills_mentioned.split(";"):
        skill = raw_skill.strip()
        normalized_name = normalize_skill(skill)

        if not normalized_name or normalized_name in seen_normalized_names:
            continue

        skills.append(skill)
        seen_normalized_names.add(normalized_name)

    return skills


def listing_to_db_params(listing: JobListingInput) -> dict[str, str | int | None]:
    """Convert a standard listing input into raw_job_listings parameters."""

    return {
        "title": listing.title,
        "company": listing.company,
        "source": listing.source,
        "url": listing.url,
        "canonical_url": canonicalize_url(listing.url),
        "location": listing.location,
        "posted_date_raw": listing.posted_date,
        "deadline_raw": listing.deadline,
        "category_guess": listing.category_guess,
        "description": listing.description,
        "requirements": listing.requirements,
        "skills_mentioned": listing.skills_mentioned,
        "notes": listing.notes,
        "source_file": listing.source_file,
        "source_row_number": listing.source_row_number,
    }


def merge_duplicate_listings(cur) -> int:
    """Merge rows that now canonicalize to the same listing identity.

    This protects us when the canonicalization rules improve over time. For
    example, an older import may have stored an Alfred locale URL as unique, and
    a newer import may use the non-locale version. Once the normalizer knows
    those are the same listing, this function merges the duplicate rows.
    """

    cur.execute(
        """
        select
            r.id,
            r.url,
            r.last_seen_at,
            exists (
                select 1
                from job_applications a
                where a.job_listing_id = r.id
            ) as has_application
        from raw_job_listings r;
        """
    )

    listings_by_canonical_url = {}
    for job_listing_id, url, last_seen_at, has_application in cur.fetchall():
        canonical_url = canonicalize_url(url)
        listings_by_canonical_url.setdefault(canonical_url, []).append(
            {
                "id": job_listing_id,
                "last_seen_at": last_seen_at,
                "has_application": has_application,
            }
        )

    merged_row_count = 0

    for canonical_url, listings in listings_by_canonical_url.items():
        if len(listings) == 1:
            listing = listings[0]
            cur.execute(
                """
                update raw_job_listings
                set canonical_url = %s
                where id = %s and canonical_url <> %s;
                """,
                (canonical_url, listing["id"], canonical_url),
            )
            continue

        application_row_count = sum(
            1
            for listing in listings
            if listing["has_application"]
        )
        if application_row_count > 1:
            duplicate_ids = ", ".join(str(listing["id"]) for listing in listings)
            raise RuntimeError(
                "Multiple duplicate listings have application rows and need a manual "
                f"merge before import can continue: {duplicate_ids}"
            )

        keep_listing = max(
            listings,
            key=lambda listing: (
                listing["has_application"],
                listing["last_seen_at"],
                listing["id"],
            ),
        )
        keep_id = keep_listing["id"]
        loser_ids = [
            listing["id"]
            for listing in listings
            if listing["id"] != keep_id
        ]

        cur.execute(
            """
            insert into job_listing_skills (
                job_listing_id,
                skill_id,
                source_text
            )
            select
                %s,
                skill_id,
                source_text
            from job_listing_skills
            where job_listing_id = any(%s)
            on conflict (job_listing_id, skill_id) do nothing;
            """,
            (keep_id, loser_ids),
        )

        cur.execute(
            """
            insert into job_listing_categories (
                job_listing_id,
                category_id,
                source
            )
            select
                %s,
                category_id,
                source
            from job_listing_categories
            where job_listing_id = any(%s)
            on conflict (job_listing_id, category_id) do nothing;
            """,
            (keep_id, loser_ids),
        )

        cur.execute(
            """
            update job_applications
            set
                job_listing_id = %s,
                updated_at = now()
            where job_listing_id = any(%s);
            """,
            (keep_id, loser_ids),
        )

        cur.execute(
            """
            update collected_job_candidates
            set promoted_job_listing_id = %s
            where promoted_job_listing_id = any(%s);
            """,
            (keep_id, loser_ids),
        )

        cur.execute(
            "delete from raw_job_listings where id = any(%s);",
            (loser_ids,),
        )

        cur.execute(
            "update raw_job_listings set canonical_url = %s where id = %s;",
            (canonical_url, keep_id),
        )

        merged_row_count += len(loser_ids)

    return merged_row_count


def remove_unlinked_skills(cur) -> None:
    """Delete orphaned imported skills while preserving profile-only skills."""

    cur.execute("select to_regclass('profile_skills') is not null;")
    profile_skills_exists = cur.fetchone()[0]
    if profile_skills_exists:
        cur.execute(
            """
            delete from skills s
            where not exists (
                select 1
                from job_listing_skills jls
                where jls.skill_id = s.id
            )
            and not exists (
                select 1
                from profile_skills ps
                where ps.skill_id = s.id
            );
            """
        )
    else:
        cur.execute(
            """
            delete from skills s
            where not exists (
                select 1
                from job_listing_skills jls
                where jls.skill_id = s.id
            );
            """
        )


@transaction()
def ingest_job_listings(listings: Iterable[JobListingInput]) -> IngestionResult:
    """Import or refresh normalized job listings in PostgreSQL.

    Source adapters are responsible for collecting listings. This function owns
    the shared persistence rules: URL canonicalization, listing upserts, skill
    link rebuilding, orphan-skill cleanup, and duplicate-row merging.
    """

    listing_rows = list(listings)
    if not listing_rows:
        raise RuntimeError("No job listings were provided for ingestion.")

    with connect() as conn:
        with conn.cursor() as cur:
            for listing in listing_rows:
                row = listing_to_db_params(listing)
                cur.execute(INSERT_RAW_JOB_SQL, row)
                job_listing_id = cur.fetchone()[0]

                # Rebuild this listing's skill links from the latest source row
                # without deleting the listing itself or attached applications.
                cur.execute(
                    "delete from job_listing_skills where job_listing_id = %s;",
                    (job_listing_id,),
                )

                for skill in split_skills(listing.skills_mentioned):
                    cur.execute(
                        UPSERT_SKILL_SQL,
                        {
                            "name": skill,
                            "normalized_name": normalize_skill(skill),
                        },
                    )
                    skill_id = cur.fetchone()[0]

                    cur.execute(
                        INSERT_JOB_SKILL_SQL,
                        {
                            "job_listing_id": job_listing_id,
                            "skill_id": skill_id,
                            "source_text": skill,
                        },
                    )

            remove_unlinked_skills(cur)
            merged_listing_count = merge_duplicate_listings(cur)

            cur.execute("select count(*) from raw_job_listings;")
            job_count = cur.fetchone()[0]

            cur.execute("select count(*) from skills;")
            skill_count = cur.fetchone()[0]

            cur.execute("select count(*) from job_listing_skills;")
            job_skill_link_count = cur.fetchone()[0]

    return IngestionResult(
        processed_listing_count=len(listing_rows),
        unique_job_count=job_count,
        unique_skill_count=skill_count,
        job_skill_link_count=job_skill_link_count,
        merged_listing_count=merged_listing_count,
    )
