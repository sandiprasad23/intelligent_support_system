import csv
import json

import pytest

from evaluation import metrics, review
from evaluation.harness import run_evaluation


def _ticket(ticket_id, subject="Key issue"):
    return {
        "ticket_id": ticket_id,
        "channel": "email",
        "subject": subject,
        "body": "Our API key returns 401 on every request.",
        "labels": {
            "intent": "api_key_issue",
            "urgency": "medium",
            "expected_route": "auto_respond",
            "answerable_from_docs": True,
            "expected_doc_ids": ["DOC-AUTH-004"],
            "must_not_auto_respond": False,
        },
    }


def _docs(ticket, intent):
    return [{"doc_id": "DOC-AUTH-004", "title": "Key", "content": "Rotate it.",
             "score": 0.9, "review_age_days": 0, "retrieval_mode": "vector"}]


def test_percentile_and_latency_summary():
    assert metrics.percentile([1, 2, 3, 4, 5], 0.95) == 5
    assert metrics.percentile([1, 2, 3, 4, 5], 0.5) == 3
    assert metrics.percentile([], 0.95) is None
    summary = metrics.latency_summary([1.0, 2.0, 3.0])
    assert summary["p95"] == 3.0


def test_metrics_report_when_expected_escalation_rate_exceeds_target():
    from evaluation.harness import calculate_metrics

    rows = [
        {
            "expected_route": "escalate" if index < 4 else "auto_respond",
            "predicted_route": "escalate",
            "expected_intent": "unclear_request",
            "predicted_intent": "unclear_request",
            "expected_urgency": "high",
            "predicted_urgency": "high",
            "answerable_from_docs": False,
            "predicted_answerable_from_docs": False,
            "must_not_auto_respond": index < 4,
            "expected_doc_ids": [],
            "retrieved_doc_ids": [],
            "cited_doc_ids": [],
            "response_returned": False,
            "latency_seconds": 0.1,
            "guardrail_violations": [],
            "private_data_in_response": [],
            "input_rejected": False,
            "reference_available": False,
        }
        for index in range(10)
    ]

    metrics = calculate_metrics(rows)

    assert metrics["expected_escalated_tickets"] == 4
    assert metrics["expected_escalation_rate_pct"] == 40


def test_wilson_interval_brackets_the_estimate():
    low, high = metrics.wilson_interval(8, 10)
    assert 0 < low < 0.8 < high < 1


def test_previous_runs_skips_invalid_and_non_object_entries(tmp_path):
    from evaluation.harness import _previous_runs

    log = tmp_path / "runs.jsonl"
    log.write_text('null\n42\n[]\ninvalid\n{"input_sha256":"match"}\n', encoding="utf-8")
    assert _previous_runs(log, "match") == 1


def test_classification_summary_reports_per_class_precision():
    summary = metrics.classification_summary(["a", "a", "b", "b"], ["a", "b", "b", "b"])
    assert summary["per_class"]["b"]["precision"] == pytest.approx(2 / 3)
    assert summary["per_class"]["a"]["recall"] == pytest.approx(0.5)


def test_cross_group_variation_flags_unreliable_small_groups():
    result = metrics.cross_group_variation({
        "tier": {
            "x": {"quality_score": 0.9, "tickets": 10},
            "y": {"quality_score": 0.5, "tickets": 10},
        }
    })
    assert result["max_gap_points"] == pytest.approx(40)
    assert result["fields"]["tier"]["worst_group"] == "y"
    assert result["reliable"] is False


def test_intent_escalation_is_categorized():
    from evaluation.harness import _escalation_category

    assert _escalation_category("Intent 'compliance_request' requires human review.") == "intent_requires_human_review"


def test_label_free_run_and_blank_subject(tmp_path, monkeypatch):
    from src import api

    seen = []
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(api, "retrieve_docs", _docs)
    monkeypatch.setattr(
        api, "classify_ticket",
        lambda ticket: {"intent": "api_key_issue", "urgency": "medium", "confidence": 0.9,
                        "provider_mode": "provider_completed", "prompt_version": "PR-01-v1.1"},
    )
    monkeypatch.setattr(
        api, "determine_route",
        lambda ticket, classification, documents: (
            seen.append(ticket["raw"]["labels"])
            or {"action": "auto_respond", "reason": "test", "threshold": 0.8}
        ),
    )
    monkeypatch.setattr(
        api, "generate_response",
        lambda ticket, documents: {"response_text": "Rotate the key. [Doc ID: DOC-AUTH-004]"},
    )
    path = tmp_path / "in.json"
    path.write_text(json.dumps([_ticket("T-1", subject="")]), encoding="utf-8")

    report = run_evaluation(path, tmp_path / "out.json", warm_up=False,
                            run_log_path=tmp_path / "log.jsonl")

    row = report["ticket_results"][0]
    assert not row["input_rejected"]
    assert row["predicted_route"] == "auto_respond"
    assert "expected_route" not in seen[0]
    assert set(row["stage_duration_seconds"]) == {"classification", "retrieval", "generation"}
    for stage in ("classification", "retrieval", "generation"):
        assert row["stage_duration_seconds"][stage] >= 0
        assert report["service"]["stage_latency_seconds"][stage]["count"] == 1


def test_simulated_outage_escalates_everything(tmp_path, monkeypatch):
    from src import api

    monkeypatch.setattr(api, "retrieve_docs", _docs)
    path = tmp_path / "in.json"
    path.write_text(json.dumps([_ticket("T-1"), _ticket("T-2")]), encoding="utf-8")

    report = run_evaluation(path, tmp_path / "out.json", simulate_provider_outage=True,
                            warm_up=False, run_log_path=tmp_path / "log.jsonl")

    assert all(row["predicted_route"] == "escalate" for row in report["ticket_results"])
    assert report["service"]["provider_failures"] == 2
    assert report["service"]["availability_pct"] == 100
    assert report["validity"]["valid_for_gate"] is False


def test_review_export_and_score_round_trip(tmp_path):
    rows = [
        {"ticket_id": f"T-{i}", "subject": "s", "body": "b", "response_returned": True,
         "response_text": "Rotate the key. [Doc ID: DOC-AUTH-004]", "retrieved_doc_ids": ["DOC-AUTH-004"]}
        for i in range(4)
    ]
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps({"ticket_results": rows}), encoding="utf-8")
    result = review.export_review(report_path, tmp_path / "review", sample_size=4)
    assert result["responses"] == 4 and result["citations"] == 4
    assert result["meets_minimum"] is False

    def fill(name, updates):
        file = tmp_path / "review" / name
        with file.open(newline="", encoding="utf-8") as handle:
            data = list(csv.DictReader(handle))
        for index, row in enumerate(data):
            row.update(updates(index))
        with file.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(data[0]))
            writer.writeheader()
            writer.writerows(data)

    fill(review.RESPONSES_FILE, lambda i: {
        "a_unsupported_claim": "yes" if i == 0 else "no", "b_unsupported_claim": "no",
        "a_satisfaction_1to5": "4", "b_satisfaction_1to5": "5"})
    fill(review.CITATIONS_FILE, lambda i: {"a_supports": "yes", "b_supports": "yes" if i else "no"})

    summary = review.score_review(tmp_path / "review")

    assert summary["responses_reviewed"] == 4
    assert summary["minimum_sample_met"] is False
    assert summary["hallucination_rate_pct"] == 25
    assert summary["citation_accuracy_pct"] == 75
    assert summary["satisfaction_mean"] == 4.5


def test_review_citation_parsing_handles_case_and_trailing_citations():
    assert review.citation_items(
        "Rotate the key.\n[doc id: DOC-AUTH-004]"
    ) == [("Rotate the key.", "DOC-AUTH-004")]


@pytest.mark.parametrize("intent", ["compliance_request", "feature_request", "unclear_request"])
def test_always_human_intents_escalate_even_when_confident(intent):
    from src.route import determine_route

    documents = [{"doc_id": "DOC-AUTH-004", "score": 0.9, "review_age_days": 0,
                  "retrieval_mode": "vector"}]
    result = determine_route(
        {"raw": {}}, {"intent": intent, "confidence": 0.95, "provider_mode": "provider_completed"}, documents
    )

    assert result["action"] == "escalate"


def test_explicit_feature_request_escalates_when_classifier_mislabels_it():
    from src.route import determine_route

    ticket = {
        "subject": "",
        "body": "It would be very useful to set spend caps per project rather than only for the whole organisation.",
        "raw": {"labels": {}},
    }
    documents = [{"doc_id": "DOC-BILL-003", "score": 0.9, "review_age_days": 0,
                  "retrieval_mode": "vector"}]

    result = determine_route(
        ticket,
        {"intent": "quota_or_overage", "confidence": 0.85, "provider_mode": "provider_completed"},
        documents,
    )

    assert result["action"] == "escalate"
    assert "feature" in result["reason"].lower()


def test_ordinary_quota_question_is_not_detected_as_feature_request():
    from src.route import determine_route

    ticket = {"subject": "", "body": "How much quota is included in our current plan?", "raw": {"labels": {}}}
    documents = [{"doc_id": "DOC-BILL-003", "score": 0.9, "review_age_days": 0,
                  "retrieval_mode": "vector"}]

    result = determine_route(
        ticket,
        {"intent": "quota_or_overage", "confidence": 0.85, "provider_mode": "provider_completed"},
        documents,
    )

    assert result["action"] == "auto_respond"
