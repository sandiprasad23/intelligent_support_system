import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from src.config import PROJECT_ROOT, env_value

AUDIT_COLUMNS = {
    "input_summary": "TEXT NOT NULL DEFAULT '{}'",
    "model_name": "TEXT NOT NULL DEFAULT ''",
    "prediction_json": "TEXT NOT NULL DEFAULT '{}'",
    "alternatives_json": "TEXT NOT NULL DEFAULT '[]'",
    "stage_results": "TEXT NOT NULL DEFAULT '{}'",
    "guardrail_results": "TEXT NOT NULL DEFAULT '{}'",
}


def _database_path_from_config(env_name="DATABASE_URL"):
    configured_path = env_value(env_name)
    if configured_path.startswith("sqlite:///"):
        configured_path = configured_path.removeprefix("sqlite:///")
    database_path = Path(configured_path)
    if not database_path.is_absolute():
        database_path = PROJECT_ROOT / database_path
    return database_path


def initialize_database(database_path=None):
    database_path = Path(database_path) if database_path is not None else _database_path_from_config()
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
                requirement_ids TEXT,
                input_summary TEXT NOT NULL DEFAULT '{}',
                model_name TEXT NOT NULL DEFAULT '',
                prediction_json TEXT NOT NULL DEFAULT '{}',
                alternatives_json TEXT NOT NULL DEFAULT '[]',
                stage_results TEXT NOT NULL DEFAULT '{}',
                guardrail_results TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        existing_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(decisions)")
        }
        for column_name, definition in AUDIT_COLUMNS.items():
            if column_name not in existing_columns:
                connection.execute(
                    f"ALTER TABLE decisions ADD COLUMN {column_name} {definition}"
                )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS llm_usage (
                usage_date TEXT PRIMARY KEY,
                calls INTEGER NOT NULL
            )
            """
        )


def reserve_llm_call(limit=None):
    """Atomically reserve one provider call from the configured UTC-day budget."""
    if limit is None:
        limit = int(os.getenv("MAX_LLM_CALLS_PER_DAY", "100"))
    if limit < 1:
        return False

    database_path = _database_path_from_config(
        "LLM_USAGE_DATABASE_URL" if os.getenv("LLM_USAGE_DATABASE_URL") else "DATABASE_URL"
    )
    initialize_database(database_path)
    usage_date = datetime.now(timezone.utc).date().isoformat()
    with sqlite3.connect(database_path, timeout=10) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT calls FROM llm_usage WHERE usage_date = ?", (usage_date,)
        ).fetchone()
        if row and row[0] >= limit:
            return False
        connection.execute(
            """
            INSERT INTO llm_usage (usage_date, calls) VALUES (?, 1)
            ON CONFLICT(usage_date) DO UPDATE SET calls = calls + 1
            """,
            (usage_date,),
        )
        return True


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
    input_summary=None,
    model_name: str = "",
    prediction_details=None,
    alternatives=None,
    stage_results=None,
    guardrail_results=None,
):
    database_path = _database_path_from_config()
    initialize_database(database_path)
    serialized_sources = json.dumps(
        [item for item in sources_used.split(",") if item]
        if isinstance(sources_used, str)
        else (sources_used or []),
        ensure_ascii=True,
    )
    serialized_guardrails = json.dumps(
        {"summary": guardrails} if isinstance(guardrails, str) else (guardrails or {}),
        ensure_ascii=True,
    )
    decision_id = str(uuid.uuid4())
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO decisions (
                decision_id, created_at, ticket_id, stage, prediction, confidence,
                threshold, action_taken, reason, sources_used, guardrails,
                prompt_version, requirement_ids, input_summary, model_name,
                prediction_json, alternatives_json, stage_results, guardrail_results
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision_id,
                datetime.now(timezone.utc).isoformat(),
                ticket_id,
                stage,
                prediction,
                confidence,
                threshold,
                action_taken,
                reason,
                serialized_sources,
                serialized_guardrails,
                prompt_version,
                json.dumps(requirement_ids if not isinstance(requirement_ids, str) else [item for item in requirement_ids.split(",") if item]),
                json.dumps(input_summary or {}, ensure_ascii=True),
                model_name,
                json.dumps(prediction_details or {}, ensure_ascii=True),
                json.dumps(alternatives or [], ensure_ascii=True),
                json.dumps(stage_results or {}, ensure_ascii=True),
                json.dumps(guardrail_results or {}, ensure_ascii=True),
            ),
        )
    return decision_id


def update_decision_for_kill_switch(decision_id, reason, stage_results, guardrail_results):
    """Keep the persisted route consistent if the kill switch activates after insertion."""
    if not decision_id:
        return
    database_path = _database_path_from_config()
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            UPDATE decisions
               SET action_taken = 'escalate',
                   reason = ?,
                   stage_results = ?,
                   guardrail_results = ?,
                   guardrails = ?
             WHERE decision_id = ?
            """,
            (
                reason,
                json.dumps(stage_results or {}, ensure_ascii=True),
                json.dumps(guardrail_results or {}, ensure_ascii=True),
                json.dumps({"summary": "kill_switch_active"}, ensure_ascii=True),
                decision_id,
            ),
        )


def main():
    initialize_database()
    print("Database initialized successfully.")


if __name__ == "__main__":
    main()
