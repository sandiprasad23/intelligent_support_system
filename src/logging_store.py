import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "storage" / "decisions.db"


def _database_path_from_config():
    configured_path = os.getenv("DATABASE_URL", "sqlite:///./storage/decisions.db")
    if configured_path.startswith("sqlite:///"):
        configured_path = configured_path.removeprefix("sqlite:///")
    database_path = Path(configured_path)
    if not database_path.is_absolute():
        database_path = PROJECT_ROOT / database_path
    return database_path


def initialize_database(database_path=DEFAULT_DATABASE_PATH):
    database_path = Path(database_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS decisions (
                decision_id     TEXT PRIMARY KEY,
                created_at      TEXT NOT NULL,
                ticket_id       TEXT NOT NULL,
                stage           TEXT NOT NULL,
                prediction      TEXT,
                confidence      REAL,
                threshold       REAL,
                action_taken    TEXT NOT NULL,
                reason          TEXT NOT NULL,
                sources_used    TEXT,
                guardrails      TEXT,
                prompt_version  TEXT,
                requirement_ids TEXT
            )
            """
        )


def log_decision(
    ticket_id: str,
    stage: str,
    prediction: str,
    confidence: float,
    threshold: float,
    action_taken: str,
    reason: str,
    sources_used: str = "",
    guardrails: str = "",
    prompt_version: str = "",
    requirement_ids: str = "",
):
    database_path = _database_path_from_config()
    initialize_database(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO decisions (
                decision_id, created_at, ticket_id, stage, prediction, confidence,
                threshold, action_taken, reason, sources_used, guardrails,
                prompt_version, requirement_ids
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                datetime.now(timezone.utc).isoformat(),
                ticket_id,
                stage,
                prediction,
                confidence,
                threshold,
                action_taken,
                reason,
                sources_used,
                guardrails,
                prompt_version,
                requirement_ids,
            ),
        )


def main():
    initialize_database()
    print("Database initialized successfully.")


if __name__ == "__main__":
    main()
