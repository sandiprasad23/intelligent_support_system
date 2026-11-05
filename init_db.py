from pathlib import Path
import sqlite3

database_path = Path("storage/decisions.db")
database_path.parent.mkdir(parents=True, exist_ok=True)
connection = sqlite3.connect(database_path)
connection.execute("""
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
""")
connection.commit()
print ("Database initialized successfully.")