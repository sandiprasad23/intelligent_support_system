import json
import sqlite3
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_documentation_records_have_chunking_fields():
    documentation_path = PROJECT_ROOT / "data" / "documentation.json"
    documents = json.loads(documentation_path.read_text(encoding="utf-8"))

    assert documents
    assert all({"doc_id", "title", "content"} <= document.keys() for document in documents)
    document_ids = [document["doc_id"] for document in documents]
    assert len(document_ids) == len(set(document_ids))


def test_init_db_creates_decisions_table_in_clean_directory(tmp_path):
    subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "init_db.py")],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    database_path = tmp_path / "storage" / "decisions.db"
    with sqlite3.connect(database_path) as connection:
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='decisions'"
        ).fetchone()

    assert table is not None