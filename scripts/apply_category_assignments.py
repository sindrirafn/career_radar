from pathlib import Path

from career_radar.db import connect, transaction
from career_radar.sql import statements_from


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSIGNMENT_FILES = [
    PROJECT_ROOT / "sql" / "10_assign_current_job_categories.sql",
    PROJECT_ROOT / "sql" / "13_apply_category_rules.sql",
]


@transaction()
def main() -> None:
    """Apply seeded and rule-based category assignments."""

    with connect() as conn:
        with conn.cursor() as cur:
            for assignment_file in ASSIGNMENT_FILES:
                for statement in statements_from(assignment_file.read_text(encoding="utf-8")):
                    cur.execute(statement)
                print(f"Applied {assignment_file.relative_to(PROJECT_ROOT)}")

            cur.execute("select count(*) from job_listing_categories;")
            assignment_count = cur.fetchone()[0]

            cur.execute(
                """
                select count(*)
                from job_listings_v1 j
                left join job_listing_categories jlc on jlc.job_listing_id = j.id
                where jlc.job_listing_id is null;
                """
            )
            uncategorized_count = cur.fetchone()[0]

    print(
        f"Category assignment complete: {assignment_count} links, "
        f"{uncategorized_count} uncategorized jobs."
    )


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print(error)
        raise SystemExit(1) from error
