from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from dotenv import load_dotenv

from career_radar.ai_enrichment import (
    has_matching_latest_ai_enrichment,
    load_candidates_for_enrichment,
    normalize_ai_payload,
    save_ai_enrichment,
)
from career_radar.applications import ApplicationStatus, set_application
from career_radar.candidates import (
    CandidateSaveResult,
    promote_candidate_to_listing,
    save_collected_candidates,
)
from career_radar.db import connect, transaction
from career_radar.ingestion import canonicalize_url
from career_radar.listings import JobListingInput
from career_radar.normalization import normalize_skill_name


PROJECT_ROOT = Path(__file__).resolve().parents[3]
INBOX_DATA_DIR = PROJECT_ROOT / "data" / "inbox"
CHATGPT_INBOX_PROMPT_VERSION = "chatgpt_task_inbox_v1"
DEFAULT_MODEL_LABEL = "chatgpt-scheduled-task"
DEFAULT_CSV_URL_ENV_VAR = "CHATGPT_INBOX_CSV_URL"

REQUIRED_COLUMNS = {
    "batch_date",
    "first_seen_date",
    "last_seen_date",
    "listing_status",
    "title",
    "company",
    "source",
    "source_unique_id",
    "url",
    "canonical_url_guess",
    "all_known_source_urls",
    "location",
    "posted_date",
    "deadline",
    "category_guess",
    "tags",
    "match_score_0_to_100",
    "fit_tier",
    "description",
    "requirements",
    "explicit_skills",
    "inferred_skills",
    "skill_gaps",
    "positive_signals",
    "concerns",
    "fit_reason",
    "duplicate_hint",
    "possible_same_role_group",
    "notes",
}

UNKNOWN_VALUES = {"", "-", "n/a", "na", "none", "null", "unknown"}
FIT_TIER_FALLBACK_SCORES = {
    "strong": 90,
    "reasonable": 70,
    "stretch": 50,
    "weak": 30,
    "exclude": 0,
}
TRACK_KEYWORDS = {
    "data_automation": (
        "analytics",
        "automation",
        "business intelligence",
        "data",
        "power bi",
        "reporting",
        "sql",
    ),
    "software_it": (
        "azure",
        "cloud",
        "developer",
        "help desk",
        "infrastructure",
        "information technology",
        "it support",
        "microsoft 365",
        "sharepoint",
        "software",
        "support",
    ),
    "engineering": (
        "engineering",
        "industrial",
        "instrumentation",
        "manufacturing",
        "process",
        "quality",
        "r&d",
        "technical systems",
        "validation",
    ),
    "biomedical_healthtech": (
        "biomedical",
        "clinical",
        "health",
        "health-tech",
        "healthcare",
        "life science",
        "medical device",
        "pharma",
    ),
    "transferable": (
        "communication",
        "coordination",
        "customer",
        "implementation",
        "process improvement",
        "technical service",
        "troubleshooting",
    ),
}


@dataclass(frozen=True, slots=True)
class InboxRow:
    """One source-preserving row exported by the scheduled ChatGPT task."""

    source_name: str
    source_row_number: int
    values: dict[str, str]
    source_path: Path | None = None


@dataclass(frozen=True, slots=True)
class InboxImportResult:
    """Summary returned after importing one or more ChatGPT inbox sources."""

    source_count: int
    source_row_count: int
    skipped_duplicate_url_count: int
    staged_result: CandidateSaveResult | None
    saved_ai_enrichment_count: int
    skipped_unchanged_ai_enrichment_count: int
    historical_applied_count: int
    historical_not_applying_count: int
    dry_run: bool


def clean_text(value: str | None) -> str:
    """Return stripped text; CSV readers use empty strings for missing cells."""

    if value is None:
        return ""

    return str(value).strip()


def optional_text(value: str | None) -> str | None:
    """Normalize blank, placeholder, or unknown values to None."""

    cleaned_value = clean_text(value)
    if cleaned_value.lower() in UNKNOWN_VALUES:
        return None

    return cleaned_value


def split_semicolon_values(value: str | None) -> list[str]:
    """Split semicolon/newline fields while preserving the original wording."""

    cleaned_value = clean_text(value)
    if not cleaned_value or cleaned_value.lower() in UNKNOWN_VALUES:
        return []

    values = []
    seen_normalized_values = set()
    for raw_part in re.split(r"[;\n]+", cleaned_value):
        part = raw_part.strip()
        normalized_value = normalize_skill_name(part)
        if not normalized_value or normalized_value in seen_normalized_values:
            continue

        values.append(part)
        seen_normalized_values.add(normalized_value)

    return values


def semicolon_text(values: Iterable[str]) -> str | None:
    """Turn a list of values back into the semicolon shape used by ingestion."""

    cleaned_values = []
    seen_normalized_values = set()
    for value in values:
        cleaned_value = clean_text(value)
        normalized_value = normalize_skill_name(cleaned_value)
        if not normalized_value or normalized_value in seen_normalized_values:
            continue

        cleaned_values.append(cleaned_value)
        seen_normalized_values.add(normalized_value)

    if not cleaned_values:
        return None

    return "; ".join(cleaned_values)


def required_text(row: InboxRow, column_name: str) -> str:
    """Read a required cell and raise a row-specific error when it is blank."""

    value = optional_text(row.values.get(column_name))
    if value is None:
        raise RuntimeError(
            f"{row.source_name}:{row.source_row_number}: {column_name} is required."
        )

    return value


def relative_source_file(path: Path) -> str:
    """Store project-relative source paths when possible."""

    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def inbox_csv_files(file_paths: list[str] | None = None) -> list[Path]:
    """Resolve explicit inbox files or every CSV in data/inbox."""

    if file_paths:
        csv_files = [Path(file_path).expanduser() for file_path in file_paths]
    else:
        csv_files = sorted(INBOX_DATA_DIR.glob("*.csv"))

    missing_files = [csv_file for csv_file in csv_files if not csv_file.exists()]
    if missing_files:
        raise RuntimeError(
            "Inbox CSV file not found: "
            + ", ".join(str(csv_file) for csv_file in missing_files)
        )

    if not csv_files:
        raise RuntimeError(
            "No ChatGPT inbox source found. Add a CSV file to "
            f"{INBOX_DATA_DIR}, pass a live CSV link with --url, or set "
            f"{DEFAULT_CSV_URL_ENV_VAR} in .env."
        )

    return csv_files


def configured_csv_urls() -> list[str]:
    """Read optional live CSV inbox URLs from the private environment."""

    load_dotenv()
    raw_url_value = os.getenv(DEFAULT_CSV_URL_ENV_VAR, "")
    return [
        url.strip()
        for url in re.split(r"[\n,]+", raw_url_value)
        if url.strip()
    ]


def validate_columns(source_name: str, fieldnames: list[str] | None) -> list[str]:
    """Fail early if the scheduled-task export changed shape."""

    if not fieldnames:
        raise RuntimeError(f"{source_name} has no header row.")

    cleaned_fieldnames = [fieldname.strip() for fieldname in fieldnames]
    if len(set(cleaned_fieldnames)) != len(cleaned_fieldnames):
        raise RuntimeError(f"{source_name} has duplicate column names.")
    missing_columns = sorted(REQUIRED_COLUMNS - set(cleaned_fieldnames))
    if missing_columns:
        raise RuntimeError(
            f"{source_name} is missing required columns: "
            + ", ".join(missing_columns)
        )

    return cleaned_fieldnames


def csv_download_url(csv_url: str) -> str:
    """Convert a normal Google Sheets edit URL into a CSV export URL."""

    parsed_url = urlparse(csv_url)
    path_parts = [part for part in parsed_url.path.split("/") if part]

    if (
        parsed_url.netloc == "docs.google.com"
        and len(path_parts) >= 4
        and path_parts[0] == "spreadsheets"
        and path_parts[1] == "d"
        and path_parts[3] == "edit"
    ):
        spreadsheet_id = path_parts[2]
        query_values = parse_qs(parsed_url.query)
        fragment_values = parse_qs(parsed_url.fragment)
        gid = (
            query_values.get("gid")
            or fragment_values.get("gid")
            or ["0"]
        )[0]
        export_query = urlencode({"format": "csv", "gid": gid})
        return (
            f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?"
            f"{export_query}"
        )

    return csv_url


def read_inbox_rows_from_reader(
    source_name: str,
    reader: csv.DictReader,
    source_path: Path | None = None,
) -> tuple[list[InboxRow], int]:
    """Read source rows from one scheduled-task CSV source."""

    rows = []
    skipped_duplicate_url_count = 0
    seen_canonical_urls = set()

    reader.fieldnames = validate_columns(source_name, reader.fieldnames)

    for source_row_number, row in enumerate(reader, start=2):
        if None in row or any(value is None for value in row.values()):
            raise RuntimeError(
                f"{source_name}:{source_row_number}: CSV row width does not match the header."
            )
        if not any(clean_text(value) for value in row.values()):
            continue

        inbox_row = InboxRow(
            source_name=source_name,
            source_row_number=source_row_number,
            values={
                key: clean_text(value)
                for key, value in row.items()
                if key is not None
            },
            source_path=source_path,
        )
        url = required_text(inbox_row, "url")
        canonical_url = canonicalize_url(url)

        if canonical_url in seen_canonical_urls:
            skipped_duplicate_url_count += 1
            continue

        seen_canonical_urls.add(canonical_url)
        rows.append(inbox_row)

    return rows, skipped_duplicate_url_count


def read_inbox_rows(csv_file: Path) -> tuple[list[InboxRow], int]:
    """Read source rows from one local scheduled-task CSV export."""

    with csv_file.open(newline="", encoding="utf-8-sig") as file:
        return read_inbox_rows_from_reader(
            source_name=str(csv_file),
            reader=csv.DictReader(file),
            source_path=csv_file,
        )


def fetch_inbox_csv_url(csv_url: str) -> str:
    """Download a CSV inbox from a live URL using only the standard library."""

    download_url = csv_download_url(csv_url)
    request = Request(download_url, headers={"User-Agent": "career-radar/0.1"})
    try:
        with urlopen(request, timeout=30) as response:
            charset = response.headers.get_content_charset() or "utf-8-sig"
            return response.read().decode(charset).lstrip("\ufeff")
    except HTTPError as error:
        if error.code in {401, 403} and "docs.google.com/spreadsheets" in download_url:
            raise RuntimeError(
                f"{csv_url}: Google denied the CSV download. Use the Sheet's "
                "published CSV link, or make the Sheet readable to anyone with "
                "the link as Viewer. The normal browser edit/share URL is not "
                "enough for this no-login importer."
            ) from error

        raise RuntimeError(
            f"{csv_url}: HTTP {error.code} while downloading inbox CSV."
        ) from error
    except URLError as error:
        raise RuntimeError(
            f"{csv_url}: could not download inbox CSV: {error.reason}"
        ) from error


def read_inbox_rows_from_url(csv_url: str) -> tuple[list[InboxRow], int]:
    """Read source rows from a live scheduled-task CSV URL."""

    csv_text = fetch_inbox_csv_url(csv_url)
    return read_inbox_rows_from_reader(
        source_name=csv_url,
        reader=csv.DictReader(StringIO(csv_text)),
    )


def note_line(label: str, value: str | None) -> str | None:
    """Format one optional scheduled-task field for candidate notes."""

    cleaned_value = optional_text(value)
    if cleaned_value is None:
        return None

    return f"{label}: {cleaned_value}"


def notes_from_inbox_row(row: InboxRow) -> str | None:
    """Preserve scheduled-task metadata that staging does not store directly."""

    note_parts = [
        note_line("Task batch date", row.values.get("batch_date")),
        note_line("Task first seen date", row.values.get("first_seen_date")),
        note_line("Task last seen date", row.values.get("last_seen_date")),
        note_line("Task listing status", row.values.get("listing_status")),
        note_line("Task source unique ID", row.values.get("source_unique_id")),
        note_line("Task canonical URL guess", row.values.get("canonical_url_guess")),
        note_line("Task known source URLs", row.values.get("all_known_source_urls")),
        note_line("Task tags", row.values.get("tags")),
        note_line("Task match score", row.values.get("match_score_0_to_100")),
        note_line("Task fit tier", row.values.get("fit_tier")),
        note_line("Task inferred skills", row.values.get("inferred_skills")),
        note_line("Task skill gaps", row.values.get("skill_gaps")),
        note_line("Task positive signals", row.values.get("positive_signals")),
        note_line("Task concerns", row.values.get("concerns")),
        note_line("Task fit reason", row.values.get("fit_reason")),
        note_line("Task duplicate hint", row.values.get("duplicate_hint")),
        note_line("Task same-role group", row.values.get("possible_same_role_group")),
        note_line("Task notes", row.values.get("notes")),
    ]

    return "\n".join(part for part in note_parts if part) or None


def listing_from_inbox_row(row: InboxRow) -> JobListingInput:
    """Convert one scheduled-task row into the shared listing input shape."""

    explicit_skills = semicolon_text(
        split_semicolon_values(row.values.get("explicit_skills"))
    )

    try:
        return JobListingInput(
            title=required_text(row, "title"),
            company=required_text(row, "company"),
            source=optional_text(row.values.get("source")) or "ChatGPT task",
            url=required_text(row, "url"),
            location=optional_text(row.values.get("location")),
            posted_date=optional_text(row.values.get("posted_date")),
            deadline=optional_text(row.values.get("deadline")),
            category_guess=optional_text(row.values.get("category_guess")),
            description=optional_text(row.values.get("description")),
            requirements=optional_text(row.values.get("requirements")),
            skills_mentioned=explicit_skills,
            notes=notes_from_inbox_row(row),
            source_file=(
                relative_source_file(row.source_path)
                if row.source_path is not None
                else row.source_name
            ),
            source_row_number=row.source_row_number,
        )
    except ValueError as error:
        raise RuntimeError(
            f"{row.source_name}:{row.source_row_number}: {error}"
        ) from error


def parse_relevance_score(row: InboxRow) -> int:
    """Read the task's percentage-style 0-100 match score."""

    raw_score = optional_text(row.values.get("match_score_0_to_100"))
    fit_tier = normalize_fit_tier(row)
    if raw_score is None:
        return FIT_TIER_FALLBACK_SCORES[fit_tier]

    score_match = re.search(r"[-+]?\d+(?:\.\d+)?", raw_score)
    if score_match is None:
        raise RuntimeError(
            f"{row.source_name}:{row.source_row_number}: "
            "match_score_0_to_100 must be numeric."
        )

    score = float(score_match.group(0))
    if not 0 <= score <= 100:
        raise RuntimeError(
            f"{row.source_name}:{row.source_row_number}: "
            "match_score_0_to_100 must be between 0 and 100."
        )

    return round(score)


def normalize_fit_tier(row: InboxRow) -> str:
    """Normalize task fit tiers to the database-controlled vocabulary."""

    fit_tier = (optional_text(row.values.get("fit_tier")) or "weak").lower()
    if fit_tier not in FIT_TIER_FALLBACK_SCORES:
        raise RuntimeError(
            f"{row.source_name}:{row.source_row_number}: unsupported fit_tier: "
            f"{fit_tier}"
        )

    return fit_tier


def relevance_label_from_score(fit_tier: str, score: int) -> str:
    """Infer the AI relevance label from task score and fit tier."""

    if fit_tier == "exclude":
        return "exclude"
    if fit_tier == "weak" or score < 40:
        return "weak"
    if score >= 80:
        return "target"
    return "adjacent"


def tracks_from_inbox_row(row: InboxRow) -> list[str]:
    """Map free-text task tags into the controlled AI strategic tracks."""

    search_text = " ".join(
        optional_text(row.values.get(column_name)) or ""
        for column_name in ("tags", "category_guess")
    ).lower()

    tracks = []
    for track, keywords in TRACK_KEYWORDS.items():
        if any(keyword in search_text for keyword in keywords):
            tracks.append(track)

    return tracks


def inferred_skills_from_inbox_row(row: InboxRow) -> list[dict[str, str]]:
    """Store semicolon-separated inferred skills as evidence-bearing JSON."""

    inferred_skills = []
    for skill in split_semicolon_values(row.values.get("inferred_skills")):
        inferred_skills.append(
            {
                "skill": skill,
                "confidence": "medium",
                "evidence": "Imported from ChatGPT task inferred_skills field.",
            }
        )

    return inferred_skills


def ai_payload_from_inbox_row(row: InboxRow) -> dict[str, object]:
    """Convert scheduled-task enrichment columns into the AI evidence schema."""

    fit_tier = normalize_fit_tier(row)
    score = parse_relevance_score(row)
    payload = {
        "relevance_label": relevance_label_from_score(fit_tier, score),
        "fit_tier": fit_tier,
        "fit_score": score,
        "priority_score": score,
        "tracks": tracks_from_inbox_row(row),
        "explicit_skills": split_semicolon_values(row.values.get("explicit_skills")),
        "inferred_skills": inferred_skills_from_inbox_row(row),
        "skill_gaps": split_semicolon_values(row.values.get("skill_gaps")),
        "positive_signals": split_semicolon_values(
            row.values.get("positive_signals")
        ),
        "concerns": split_semicolon_values(row.values.get("concerns")),
        "evidence": optional_text(row.values.get("fit_reason"))
        or "Imported from ChatGPT scheduled-task inbox row.",
    }

    return normalize_ai_payload(payload)


def load_candidate_for_inbox_row(row: InboxRow):
    """Load the staged candidate created or refreshed from this inbox row."""

    canonical_url = canonicalize_url(required_text(row, "url"))
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select id
                from collected_job_candidates
                where canonical_url = %s;
                """,
                (canonical_url,),
            )
            candidate_id_row = cur.fetchone()

    if candidate_id_row is None:
        raise RuntimeError(
            f"{row.source_name}:{row.source_row_number}: "
            "candidate was not found after staging."
        )

    candidates = load_candidates_for_enrichment(
        label="all",
        limit=1,
        candidate_id=candidate_id_row[0],
        force=True,
    )
    if not candidates:
        raise RuntimeError(
            f"{row.source_name}:{row.source_row_number}: "
            "candidate could not be loaded for enrichment."
        )

    return candidates[0]


def historical_workflow_decision(
    row: InboxRow,
) -> tuple[ApplicationStatus, str, str] | None:
    """Map explicit saved-job reconciliation decisions into the app workflow.

    Rows without one of these prefixes remain in candidate review so the user can
    decide them in the app.  The source reconciliation does not include a
    trustworthy application date, so historical applications intentionally keep
    ``applied_on`` empty.
    """

    notes = (optional_text(row.values.get("notes")) or "").casefold()
    if notes.startswith("applied."):
        return (
            "applied",
            "Historical application imported from the saved-job reconciliation.",
            "No application date was available in the saved-job export.",
        )
    if notes.startswith("skipped."):
        return (
            "not_applying",
            "Historical skipped listing imported from the saved-job reconciliation.",
            "Retained for historical job-market analysis; no application was made.",
        )

    return None


def application_exists(job_listing_id: int) -> bool:
    """Return whether a workflow row already exists for a promoted listing."""

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select 1
                from job_applications
                where job_listing_id = %s;
                """,
                (job_listing_id,),
            )
            return cur.fetchone() is not None


def sync_historical_workflow_decisions(
    inbox_rows: Iterable[InboxRow],
) -> tuple[int, int]:
    """Promote explicit applied/skipped rows without overwriting later choices."""

    historical_applied_count = 0
    historical_not_applying_count = 0

    for row in inbox_rows:
        workflow_decision = historical_workflow_decision(row)
        if workflow_decision is None:
            continue

        status, next_action, notes = workflow_decision
        candidate = load_candidate_for_inbox_row(row)
        if candidate.review_status == "rejected":
            continue
        promotion = promote_candidate_to_listing(candidate.id)

        # A later user decision is authoritative; imports only fill a missing
        # workflow record and never reset an existing application status.
        if application_exists(promotion.job_listing_id):
            continue

        saved = set_application(
            job_listing_id=promotion.job_listing_id,
            status=status,
            applied_on=None,
            application_url=None,
            cv_version=None,
            cover_letter_version=None,
            follow_up_date=None,
            next_action=next_action,
            notes=notes,
            overwrite_existing=False,
        )
        if not saved:
            continue
        if status == "applied":
            historical_applied_count += 1
        else:
            historical_not_applying_count += 1

    return historical_applied_count, historical_not_applying_count


def import_chatgpt_inbox(
    file_paths: list[str] | None = None,
    csv_urls: list[str] | None = None,
    save_ai_results: bool = True,
    model_label: str = DEFAULT_MODEL_LABEL,
    dry_run: bool = False,
) -> InboxImportResult:
    """Stage scheduled-task CSV rows and optionally save their AI evidence."""

    inbox_rows = []
    skipped_duplicate_url_count = 0

    # An explicit local source must not also pull an unrelated configured Sheet.
    url_sources = csv_urls if csv_urls is not None else (
        [] if file_paths else configured_csv_urls()
    )
    csv_files = inbox_csv_files(file_paths) if file_paths or not url_sources else []

    for csv_url in url_sources:
        rows, skipped_count = read_inbox_rows_from_url(csv_url)
        inbox_rows.extend(rows)
        skipped_duplicate_url_count += skipped_count

    for csv_file in csv_files:
        rows, skipped_count = read_inbox_rows(csv_file)
        inbox_rows.extend(rows)
        skipped_duplicate_url_count += skipped_count

    # Use the same first-occurrence rule across sources as within a source.
    # Otherwise the listing could come from one file and its AI snapshot from another.
    unique_rows = {}
    for row in inbox_rows:
        canonical_url = canonicalize_url(required_text(row, "url"))
        if canonical_url in unique_rows:
            skipped_duplicate_url_count += 1
        else:
            unique_rows[canonical_url] = row
    inbox_rows = list(unique_rows.values())
    listings = [listing_from_inbox_row(row) for row in inbox_rows]
    if not listings:
        raise RuntimeError("No non-empty scheduled-task rows found.")

    if save_ai_results:
        for row in inbox_rows:
            ai_payload_from_inbox_row(row)

    if dry_run:
        return InboxImportResult(
            source_count=len(url_sources) + len(csv_files),
            source_row_count=len(inbox_rows),
            skipped_duplicate_url_count=skipped_duplicate_url_count,
            staged_result=None,
            saved_ai_enrichment_count=0,
            skipped_unchanged_ai_enrichment_count=0,
            historical_applied_count=0,
            historical_not_applying_count=0,
            dry_run=True,
        )

    with transaction():
        return save_inbox_rows(
            inbox_rows, listings, len(url_sources) + len(csv_files),
            skipped_duplicate_url_count, save_ai_results, model_label,
        )


def save_inbox_rows(
    inbox_rows: list[InboxRow],
    listings: list[JobListingInput],
    source_count: int,
    skipped_duplicate_url_count: int,
    save_ai_results: bool,
    model_label: str,
) -> InboxImportResult:
    """Persist prevalidated input inside the importer's transaction."""

    staged_result = save_collected_candidates(listings)

    saved_ai_enrichment_count = 0
    skipped_unchanged_ai_enrichment_count = 0
    if save_ai_results:
        for row in inbox_rows:
            candidate = load_candidate_for_inbox_row(row)
            payload = ai_payload_from_inbox_row(row)
            if has_matching_latest_ai_enrichment(
                candidate_id=candidate.id,
                provider="chatgpt_task",
                model=model_label,
                prompt_version=CHATGPT_INBOX_PROMPT_VERSION,
                payload=payload,
            ):
                skipped_unchanged_ai_enrichment_count += 1
                continue

            save_ai_enrichment(
                candidate=candidate,
                model=model_label,
                payload=payload,
                response_id=None,
                provider="chatgpt_task",
                prompt_version=CHATGPT_INBOX_PROMPT_VERSION,
            )
            saved_ai_enrichment_count += 1

    (
        historical_applied_count,
        historical_not_applying_count,
    ) = sync_historical_workflow_decisions(inbox_rows)

    return InboxImportResult(
        source_count=source_count,
        source_row_count=len(inbox_rows),
        skipped_duplicate_url_count=skipped_duplicate_url_count,
        staged_result=staged_result,
        saved_ai_enrichment_count=saved_ai_enrichment_count,
        skipped_unchanged_ai_enrichment_count=skipped_unchanged_ai_enrichment_count,
        historical_applied_count=historical_applied_count,
        historical_not_applying_count=historical_not_applying_count,
        dry_run=False,
    )


def print_inbox_import_result(result: InboxImportResult) -> None:
    """Print a compact summary after importing scheduled-task rows."""

    action = "Validated" if result.dry_run else "Imported"
    print(
        f"{action} {result.source_row_count} scheduled-task row(s) "
        f"from {result.source_count} source(s)."
    )
    if result.skipped_duplicate_url_count:
        print(
            "Skipped "
            f"{result.skipped_duplicate_url_count} exact duplicate URL row(s)."
        )
    if result.staged_result is None:
        print("Dry run only. No candidates or AI results were saved.")
        return

    print(f"    staged candidates processed: {result.staged_result.processed_count}")
    print(f"    strong candidates: {result.staged_result.strong_count}")
    print(f"    possible candidates: {result.staged_result.possible_count}")
    print(f"    likely irrelevant: {result.staged_result.irrelevant_count}")
    print(f"    hard negatives: {result.staged_result.hard_negative_count}")
    print(f"    saved task AI results: {result.saved_ai_enrichment_count}")
    print(
        "    unchanged task AI results skipped: "
        f"{result.skipped_unchanged_ai_enrichment_count}"
    )
    print(f"    historical applications added: {result.historical_applied_count}")
    print(
        "    historical skipped listings added: "
        f"{result.historical_not_applying_count}"
    )
