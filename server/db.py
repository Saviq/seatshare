import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import HTTPException

from server.config import ROOT, Settings

logger = logging.getLogger(__name__)


def connect(settings: Settings) -> sqlite3.Connection:
    connection = sqlite3.connect(settings.db_path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def initialize(settings: Settings) -> None:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing_connection(settings) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE IF NOT EXISTS migrations (name TEXT PRIMARY KEY)")
        for path in sorted((ROOT / "server" / "migrations").glob("*.sql")):
            if connection.execute("SELECT 1 FROM migrations WHERE name=?", (path.name,)).fetchone():
                continue
            # Record each migration atomically with its DDL.
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                + path.read_text()
                + f"\nINSERT INTO migrations VALUES ('{path.name}');\nCOMMIT;"
            )


@contextmanager
def closing_connection(settings: Settings) -> Iterator[sqlite3.Connection]:
    connection = connect(settings)
    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def transaction(settings: Settings) -> Iterator[sqlite3.Connection]:
    with closing_connection(settings) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            logger.warning("Database constraint rejected a mutation")
            raise HTTPException(
                409, "Operation conflicts with an existing booking or request"
            ) from exc
        except sqlite3.OperationalError as exc:
            connection.rollback()
            if exc.sqlite_errorcode in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                logger.warning("Database busy; mutation must be retried")
                raise HTTPException(
                    503, "Database busy; please retry", headers={"Retry-After": "1"}
                ) from exc
            raise
