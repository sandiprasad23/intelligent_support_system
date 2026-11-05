import sqlite3
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "storage" / "decisions.db"


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


def main():
    initialize_database()
    print("Database initialized successfully.")


if __name__ == "__main__":
    main()
