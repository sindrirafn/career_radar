from pathlib import Path

from career_radar.db import connect
from career_radar.sql import statements_from


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_FILE = PROJECT_ROOT / "sql" / "04_first_analysis_queries.sql"


def print_rows(rows: list[tuple], columns: list[str]) -> None:
    """Print a compact terminal table for query results."""

    print(" | ".join(columns))
    print("-" * 80)
    for row in rows:
        print(" | ".join("" if value is None else str(value) for value in row))


def main() -> None:
    """Run the starter analysis SQL and print each result set."""

    with connect() as conn:
        with conn.cursor() as cur:
            for index, statement in enumerate(
                statements_from(ANALYSIS_FILE.read_text(encoding="utf-8")),
                start=1,
            ):
                print(f"\nQuery {index}")
                print("=" * 80)
                cur.execute(statement)
                if cur.description is None:
                    print("No rows returned.")
                    continue

                columns = [column.name for column in cur.description]
                rows = cur.fetchall()
                print_rows(rows, columns)


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print(error)
        raise SystemExit(1) from error
