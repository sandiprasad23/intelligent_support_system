from src import api


def test_process_ticket_pipeline(monkeypatch):
    monkeypatch.setattr(
        api,
        "normalize_ticket",
        lambda ticket: {
            "ticket_id": ticket["ticket_id"],
            "channel": ticket["channel"],
            "subject": ticket["subject"],
            "body": ticket["body"],
        },
    )
    monkeypatch.setattr(
        api,
        "classify_ticket",
        lambda ticket: {
            "intent": "configuration_help",
            "urgency": "low",
            "confidence": 0.9,
        },
    )
    monkeypatch.setattr(
        api,
        "retrieve_docs",
        lambda ticket, intent: [{"doc_id": "DOC-TEST", "title": "Test", "content": "Test"}],
    )
    monkeypatch.setattr(
        api,
        "determine_route",
        lambda ticket, classification, documents: {
            "action": "auto_respond",
            "reason": "test",
            "threshold": 0.8,
        },
    )
    monkeypatch.setattr(
        api,
        "generate_response",
        lambda ticket, documents: {"response_text": "[Doc ID: DOC-TEST] test"},
    )
    monkeypatch.setattr(
        api,
        "check_pii_and_hallucinations",
        lambda response, documents: {
            "passed": True,
            "violations": [],
            "sanitized_response": response,
        },
    )
    logged_decisions = []
    monkeypatch.setattr(api, "log_decision", lambda **decision: logged_decisions.append(decision))

    result = api.process_ticket_endpoint(
        api.TicketRequest(ticket_id="TEST-1", subject="Test", body="Test body")
    )

    assert result["action"] == "auto_respond"
    assert result["sources_used"] == ["DOC-TEST"]
    assert result["response_text"] == "[Doc ID: DOC-TEST] test"
    assert len(logged_decisions) == 1