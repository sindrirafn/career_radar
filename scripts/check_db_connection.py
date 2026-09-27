from career_radar.db import connect


def main() -> None:
    """Verify that Python can reach the configured PostgreSQL database."""

    with connect() as conn:
        with conn.cursor() as cur:
            # Ask PostgreSQL which database and user accepted the connection.
            # This confirms both credentials and target database in one query.
            cur.execute("select current_database(), current_user;")
            database_name, user_name = cur.fetchone()

    print(f"Connected to database '{database_name}' as user '{user_name}'.")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print(error)
        raise SystemExit(1) from error
