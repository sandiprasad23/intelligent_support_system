import json
import sqlite3
from pathlib import Path

from evaluation.harness import calculate_metrics, run_evaluation
from src.classify import VALID_INTENTS, classify_ticket
from src.generate import generate_response
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


def test_supplied_datasets_match_guide_counts_and_references():
    data_directory = PROJECT_ROOT / "data"
    development = json.loads(
        (data_directory / "development_tickets.json").read_text(encoding="utf-8")
    )
    validation = json.loads(
        (data_directory / "validation_tickets.json").read_text(encoding="utf-8")
    )
    references = json.loads(
        (data_directory / "ground_truth_responses.json").read_text(encoding="utf-8")
    )
    documents = json.loads(
        (data_directory / "documentation.json").read_text(encoding="utf-8")
    )

    assert (len(development), len(validation), len(references), len(documents)) == (
        500,
        80,
        200,
        29,
    )
    document_ids = {document["doc_id"] for document in documents}
    development_by_id = {ticket["ticket_id"]: ticket for ticket in development}
    development_ids = set(development_by_id)
    validation_ids = {ticket["ticket_id"] for ticket in validation}
    reference_ids = [reference["ticket_id"] for reference in references]

    assert development_ids.isdisjoint(validation_ids)
    assert len(reference_ids) == len(set(reference_ids))
    assert set(reference_ids) <= development_ids

    required_ticket_fields = {
        "ticket_id", "channel", "subject", "body", "received_at", "customer_id",
        "customer_name", "customer_tier", "customer_region", "language_fluency",
        "labels", "history",
    }
    required_label_fields = {
        "intent", "urgency", "expected_route", "answerable_from_docs",
        "expected_doc_ids", "must_not_auto_respond",
    }
    required_history_fields = {
        "first_contact_resolution", "resolution_time_minutes", "csat_rating",
        "escalated", "repeat_contact",
    }
    allowed_channels = {"email", "chat", "docs_comment", "forum"}
    allowed_tiers = {"standard", "business", "enterprise"}
    allowed_regions = {"north_america", "europe", "asia_pacific", "latin_america"}
    allowed_fluencies = {"fluent", "non_fluent"}
    allowed_routes = {"auto_respond", "escalate"}
    allowed_urgencies = {"high", "medium", "low"}
    for ticket in [*development, *validation]:
        labels = ticket["labels"]
        history = ticket["history"]
        assert required_ticket_fields <= ticket.keys()
        assert required_label_fields <= labels.keys()
        assert required_history_fields <= history.keys()
        assert ticket["channel"] in allowed_channels
        assert ticket["customer_tier"] in allowed_tiers
        assert ticket["customer_region"] in allowed_regions
        assert ticket["language_fluency"] in allowed_fluencies
        assert labels["expected_route"] in allowed_routes
        assert labels["urgency"] in allowed_urgencies
        assert labels["intent"] in VALID_INTENTS
        assert isinstance(labels["answerable_from_docs"], bool)
        assert isinstance(labels["must_not_auto_respond"], bool)
        assert all(
            isinstance(history[field], bool)
            for field in ("first_contact_resolution", "escalated", "repeat_contact")
        )
        assert isinstance(history["resolution_time_minutes"], (int, float))
        assert history["resolution_time_minutes"] >= 0
        assert history["csat_rating"] in {1, 2, 3, 4, 5}
        assert labels["answerable_from_docs"] == bool(labels["expected_doc_ids"])
        assert set(labels["expected_doc_ids"]) <= document_ids
        if labels["must_not_auto_respond"]:
            assert labels["expected_route"] == "escalate"

    for reference in references:
        assert {
            "ticket_id", "intent", "expected_doc_ids", "reference_response",
            "must_mention", "must_not_claim", "written_by",
        } <= reference.keys()
        assert set(reference["expected_doc_ids"]) <= document_ids
        assert reference["intent"] == development_by_id[reference["ticket_id"]]["labels"]["intent"]

    for document in documents:
        assert {"doc_id", "title", "category", "applies_to", "content"} <= document.keys()
        assert set(document.get("related_docs", [])) <= document_ids


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


def test_evaluation_metrics_score_retrieval_and_safety():
    rows = [
        {
            "expected_intent": "api_key_issue",
            "predicted_intent": "api_key_issue",
            "expected_urgency": "high",
            "predicted_urgency": "high",
            "expected_route": "escalate",
            "predicted_route": "auto_respond",
            "must_not_auto_respond": True,
            "answerable_from_docs": True,
            "expected_doc_ids": ["DOC-A", "DOC-B"],
            "retrieved_doc_ids": ["DOC-A", "DOC-C"],
            "cited_doc_ids": ["DOC-A"],
            "guardrail_violations": [],
            "latency_seconds": 1.0,
        },
        {
            "expected_intent": "sso_configuration",
            "predicted_intent": "account_access",
            "expected_urgency": "low",
            "predicted_urgency": "medium",
            "expected_route": "auto_respond",
            "predicted_route": "auto_respond",
            "must_not_auto_respond": False,
            "answerable_from_docs": True,
            "expected_doc_ids": ["DOC-C"],
            "retrieved_doc_ids": ["DOC-C"],
            "cited_doc_ids": ["DOC-C"],
            "guardrail_violations": [],
            "latency_seconds": 3.0,
        },
    ]

    metrics = calculate_metrics(rows, top_k=3)

    assert metrics["tickets"] == 2
    assert metrics["intent_accuracy"] == 0.5
    assert metrics["urgency_accuracy"] == 0.5
    assert metrics["route_accuracy"] == 0.5
    assert metrics["answerability_accuracy"] == 1.0
    assert metrics["must_not_auto_respond_violations"] == 1
    assert metrics["retrieval_hit_at_3"] == 1.0
    assert metrics["retrieval_recall_at_3"] == 2 / 3


def test_evaluation_harness_accepts_input_and_output_paths(tmp_path, monkeypatch):
    import evaluation.harness as harness

    input_path = tmp_path / "hidden-like-tickets.json"
    output_path = tmp_path / "reports" / "evaluation.json"
    input_path.write_text(
        json.dumps(
            [
                {
                    "ticket_id": "HIDDEN-001",
                    "channel": "email",
                    "subject": "Key issue",
                    "body": "Key returns 401",
                    "labels": {
                        "intent": "api_key_issue",
                        "urgency": "medium",
                        "expected_route": "auto_respond",
                        "answerable_from_docs": True,
                        "expected_doc_ids": ["DOC-AUTH-004"],
                        "must_not_auto_respond": False,
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    from src import api

    seen_labels = []
    monkeypatch.setattr(
        api,
        "classify_ticket",
        lambda ticket: {
            "intent": "api_key_issue", "urgency": "medium", "confidence": 0.9,
            "provider_mode": "provider_completed", "prompt_version": "PR-01-v1.1",
        },
    )
    monkeypatch.setattr(
        api,
        "retrieve_docs",
        lambda ticket, intent: [
            {"doc_id": "DOC-AUTH-004", "title": "Key", "content": "Rotate it.",
             "score": 0.9, "review_age_days": 0, "retrieval_mode": "vector"}
        ],
    )

    def fake_route(ticket, classification, documents):
        seen_labels.append(ticket["raw"]["labels"])
        return {"action": "auto_respond", "reason": "test", "threshold": 0.8}

    monkeypatch.setattr(api, "determine_route", fake_route)
    monkeypatch.setattr(
        api,
        "generate_response",
        lambda ticket, documents: {"response_text": "[Doc ID: DOC-AUTH-004] Rotate it."},
    )
    run_log = tmp_path / "run_log.jsonl"

    report = run_evaluation(input_path, output_path, warm_up=False, run_log_path=run_log)

    assert "expected_route" not in seen_labels[0]
    assert seen_labels[0]["must_not_auto_respond"] is False
    assert report["decision_log"]["matches"]
    assert report["manifest"]["tickets"] == 1
    assert {item["measure"] for item in report["results_table"]} >= {
        "First contact resolution", "Latency, 95th percentile", "Confidence calibration",
    }
    assert report["caution_statement"].startswith("The figures above should be treated with caution because")
    assert len(run_log.read_text(encoding="utf-8").splitlines()) == 1

    assert output_path.exists()
    assert report["metrics"]["intent_accuracy"] == 1.0
    assert report["metrics"]["retrieval_recall_at_3"] == 1.0
    assert json.loads(output_path.read_text(encoding="utf-8"))["ticket_results"][0]["ticket_id"] == "HIDDEN-001"

    ground_truth_path = tmp_path / "ground-truth.json"
    ground_truth_path.write_text(
        json.dumps(
            [
                {
                    "ticket_id": "HIDDEN-001",
                    "must_mention": ["Rotate it"],
                    "must_not_claim": ["refund has been issued"],
                }
            ]
        ),
        encoding="utf-8",
    )
    quality_report = run_evaluation(
        input_path,
        tmp_path / "reports" / "quality.json",
        ground_truth_path=ground_truth_path,
        warm_up=False,
        run_log_path=run_log,
    )
    assert quality_report["metrics"]["reference_response_matches"] == 1
    assert quality_report["metrics"]["must_mention_recall"] == 1.0
    assert quality_report["metrics"]["must_not_claim_violation_count"] == 0


def test_versioned_prompt_files_load_in_offline_mode(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    ticket = {
        "ticket_id": "TEST-4",
        "channel": "email",
        "customer_tier": "standard",
        "language_fluency": "fluent",
        "subject": "API key error",
        "body": "Our key returns 401.",
    }
    documents = [{"doc_id": "DOC-AUTH-004", "title": "Key help", "content": "Rotate the key."}]

    classification = classify_ticket(ticket)
    response = generate_response(ticket, documents)

    assert classification["intent"] == "api_key_issue"
    assert response["cited_doc_ids"] == ["DOC-AUTH-004"]