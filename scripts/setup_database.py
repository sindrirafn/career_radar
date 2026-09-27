from pathlib import Path

from career_radar.db import connect, transaction
from career_radar.sql import statements_from


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SQL_FILES = [
    PROJECT_ROOT / "sql" / "00_create_parse_iso_date.sql",
    PROJECT_ROOT / "sql" / "01_create_raw_job_listings.sql",
    PROJECT_ROOT / "sql" / "18_create_collected_job_candidates.sql",
    PROJECT_ROOT / "sql" / "19_create_candidate_ai_enrichments.sql",
    PROJECT_ROOT / "sql" / "20_create_candidate_duplicate_reviews.sql",
    PROJECT_ROOT / "sql" / "02_create_skill_tables.sql",
    PROJECT_ROOT / "sql" / "15_create_profile_skills.sql",
    PROJECT_ROOT / "sql" / "03_create_job_listings_v1_view.sql",
    PROJECT_ROOT / "sql" / "05_create_job_applications.sql",
    PROJECT_ROOT / "sql" / "08_create_category_tables.sql",
    PROJECT_ROOT / "sql" / "09_seed_job_categories.sql",
    PROJECT_ROOT / "sql" / "12_create_job_listings_enriched_v1_view.sql",
    PROJECT_ROOT / "sql" / "14_create_possible_duplicate_job_listings_view.sql",
    PROJECT_ROOT / "sql" / "16_create_skill_gap_views.sql",
    PROJECT_ROOT / "sql" / "17_create_powerbi_views.sql",
]


@transaction()
def main() -> None:
    """Create or update the database objects needed for V1 analysis."""

    with connect() as conn:
        with conn.cursor() as cur:
            for sql_file in SQL_FILES:
                for statement in statements_from(sql_file.read_text(encoding="utf-8")):
                    cur.execute(statement)
                print(f"Applied {sql_file.relative_to(PROJECT_ROOT)}")

    print("Database setup complete.")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print(error)
        raise SystemExit(1) from error
