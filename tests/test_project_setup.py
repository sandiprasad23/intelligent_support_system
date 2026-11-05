import json
import sqlite3
from pathlib import Path

from src.ingest import normalize_ticket
from src.logging_store import initialize_database, log_decision


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_documentation_records_have_chunking_fields():
    documentation_path = PROJECT_ROOT / "data" / "documentation.json"
    documents = json.loads(documentation_path.read_text(encoding="utf-8"))

    assert documents
    assert all({"doc_id", "title", "content"} <= document.keys() for document in documents)
    document_ids = [document["doc_id"] for document in documents]
    assert len(document_ids) == len(set(document_ids))


def test_init_db_creates_decisions_table_in_clean_directory(tmp_path):
    database_path = tmp_path / "storage" / "decisions.db"
    initialize_database(database_path)

    with sqlite3.connect(database_path) as connection:
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='decisions'"
        ).fetchone()

    assert table is not None


def test_normalize_ticket_preserves_labels_and_cleans_text():
    ticket = normalize_ticket(
        {
            "ticket_id": "TEST-2",
            "channel": "chat",
            "subject": "  Help  ",
            "body": "  Please\n help  ",
            "labels": {"must_not_auto_respond": True},
        }
    )

    assert ticket["subject"] == "Help"
    assert ticket["body"] == "Please help"
    assert ticket["raw"]["labels"]["must_not_auto_respond"] is True


def test_log_decision_inserts_a_record(tmp_path, monkeypatch):
    database_path = tmp_path / "storage" / "decisions.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path}")

    log_decision(
        ticket_id="TEST-3",
        stage="pipeline_complete",
        prediction="configuration_help",
        confidence=0.9,
        threshold=0.8,
        action_taken="auto_respond",
        reason="test record",
    )

    with sqlite3.connect(database_path) as connection:
        record = connection.execute(
            "SELECT ticket_id, action_taken FROM decisions"
        ).fetchone()

    assert record == ("TEST-3", "auto_respond")