from __future__ import annotations

import os
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from urllib.parse import urlparse

from dotenv import load_dotenv


_transaction_connection = ContextVar("career_radar_transaction", default=None)


def get_database_url() -> str:
    """Read and validate the private PostgreSQL connection string.

    The real connection string lives in .env, not in source control. Keeping it
    out of the code prevents accidental password commits and makes it easy to
    point the same scripts at a different database later.
    """
    load_dotenv()
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise RuntimeError(
            "DATABASE_URL is not set. Copy .env.example to .env and add your "
            "PostgreSQL connection string."
        )

    parsed_url = urlparse(database_url)

    # These checks catch the setup mistakes that are easiest to make while the
    # project is new: leaving template values in place, or using the old hyphen
    # database name that PostgreSQL would force us to quote everywhere.
    if "your_username" in database_url or "your_password" in database_url:
        raise RuntimeError(
            "DATABASE_URL still has placeholder values. Edit .env with your real "
            "PostgreSQL username, password, host, port, and database name."
        )

    if parsed_url.path.rstrip("/") in {"", "/career-radar"}:
        raise RuntimeError(
            "DATABASE_URL should point to the project database named career_radar. "
            "Use an underscore, not a hyphen."
        )

    return database_url


def connect():
    """Open a connection context, reusing the current unit of work if present."""
    import psycopg

    connection = _transaction_connection.get()
    if connection is not None:
        # Inner helpers must not commit or close the outer workflow's connection.
        return nullcontext(connection)
    return psycopg.connect(get_database_url())


@contextmanager
def transaction():
    """Commit a complete workflow together; nested workflows use savepoints.

    ContextVar isolates simultaneous web requests. The advisory lock serializes
    participating writers so refreshes and review actions cannot race each other.
    It is released automatically on commit or rollback.
    """
    with connect() as connection:
        with connection.transaction():
            if _transaction_connection.get() is None:
                connection.execute("select pg_advisory_xact_lock(1129464914)")
            token = _transaction_connection.set(connection)
            try:
                yield connection
            finally:
                _transaction_connection.reset(token)
