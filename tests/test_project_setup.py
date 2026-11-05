import json
import sqlite3
from pathlib import Path

from src.logging_store import initialize_database


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