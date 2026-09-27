import csv
import re
from pathlib import Path

from career_radar.ingestion import ingest_job_listings
from career_radar.listings import JobListingInput


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"
REQUIRED_COLUMNS = {
    "title",
    "company",
    "source",
    "url",
    "location",
    "posted_date",
    "deadline",
    "category_guess",
    "description",
    "requirements",
    "skills_mentioned",
    "notes",
}


def csv_sort_key(path: Path) -> tuple[str, str]:
    """Sort raw CSV files by date in the filename, then by filename.

    Processing older files first and newer files later means a newer baseline
    refreshes overlapping listings through the canonical_url upsert.
    """

    match = re.search(r"\d{4}-\d{2}-\d{2}", path.name)
    file_date = match.group(0) if match else "0000-00-00"
    return file_date, path.name


def raw_csv_files() -> list[Path]:
    """Return all raw CSV inputs in deterministic import order."""

    return sorted(RAW_DATA_DIR.glob("*.csv"), key=csv_sort_key)


def validate_columns(csv_file: Path, fieldnames: list[str] | None) -> None:
    """Fail early if a CSV does not match the expected raw-listing shape."""

    if not fieldnames:
        raise RuntimeError(f"{csv_file} has no header row.")

    if len(set(fieldnames)) != len(fieldnames):
        raise RuntimeError(f"{csv_file} has duplicate column names.")

    missing_columns = sorted(REQUIRED_COLUMNS - set(fieldnames))
    if missing_columns:
        raise RuntimeError(
            f"{csv_file} is missing required columns: {', '.join(missing_columns)}"
        )


def listing_from_csv_row(
    row: dict[str, str],
    source_file: Path,
    source_row_number: int,
) -> JobListingInput:
    """Convert one CSV row into the standard ingestion shape."""

    try:
        return JobListingInput(
            title=row["title"],
            company=row["company"],
            source=row["source"],
            url=row["url"],
            location=row["location"],
            posted_date=row["posted_date"],
            deadline=row["deadline"],
            category_guess=row["category_guess"],
            description=row["description"],
            requirements=row["requirements"],
            skills_mentioned=row["skills_mentioned"],
            notes=row["notes"],
            source_file=str(source_file.relative_to(PROJECT_ROOT)),
            source_row_number=source_row_number,
        )
    except ValueError as error:
        raise RuntimeError(
            f"{source_file}:{source_row_number}: {error}"
        ) from error


def collect_csv_listings() -> tuple[list[JobListingInput], int]:
    """Read every raw CSV file and return standard listing inputs."""

    listings = []
    csv_files = raw_csv_files()

    if not csv_files:
        raise RuntimeError(f"No raw CSV files found in {RAW_DATA_DIR}.")

    for csv_file in csv_files:
        with csv_file.open(newline="", encoding="utf-8-sig") as file:
            reader = csv.DictReader(file)
            validate_columns(csv_file, reader.fieldnames)
            for source_row_number, row in enumerate(reader, start=2):
                if None in row or any(value is None for value in row.values()):
                    raise RuntimeError(
                        f"{csv_file}:{source_row_number}: CSV row width does not match the header."
                    )
                listings.append(
                    listing_from_csv_row(row, csv_file, source_row_number)
                )

    return listings, len(csv_files)


def main() -> None:
    """Import or refresh every raw CSV file in PostgreSQL."""

    listings, csv_file_count = collect_csv_listings()
    result = ingest_job_listings(listings)

    print(
        f"Processed {result.processed_listing_count} raw rows from "
        f"{csv_file_count} CSV files. Database now has {result.unique_job_count} "
        f"unique jobs, {result.unique_skill_count} unique skills, and "
        f"{result.job_skill_link_count} job-skill links."
    )
    if result.merged_listing_count:
        print(f"Merged {result.merged_listing_count} duplicate listing rows.")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print(error)
        raise SystemExit(1) from error
