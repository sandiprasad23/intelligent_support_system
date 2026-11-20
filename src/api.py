import os
from contextlib import asynccontextmanager
from time import perf_counter
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.classify import CLASSIFY_PROMPT_VERSION, classify_ticket
from src.config import env_value
from src.generate import GENERATE_PROMPT_VERSION, generate_response
from src.guardrails import (
    check_pii_and_hallucinations,
    detect_instruction_injection,
    detect_private_data,
)
from src.ingest import normalize_ticket
from src.logging_store import log_decision
from src.monitoring import (
    CLASSIFICATION_CONFIDENCE,
    GUARDRAIL_BLOCKS,
    RESPONSE_LATENCY,
    TICKETS_PROCESSED,
    start_metrics_server,
)
from src.retrieve import RETRIEVE_PROMPT_VERSION, retrieve_docs, warm_up_retriever
from src.route import auto_response_disabled, determine_route


@asynccontextmanager
async def lifespan(app):
    warm_up_retriever()
    start_metrics_server()
    yield


app = FastAPI(
    title="CloudServe Grounded Support Engine API",
    version="1.0",
    lifespan=lifespan,
)


class TicketLabels(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str | None = None
    urgency: Literal["high", "medium", "low"] | None = None
    expected_route: Literal["auto_respond", "escalate"] | None = None
    answerable_from_docs: bool | None = None
    expected_doc_ids: list[str] = Field(default_factory=list)
    must_not_auto_respond: bool = False


class TicketRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_id: str = Field(examples=["DEV-0001"])
    channel: Literal["email", "chat", "docs_comment", "forum"] = "email"
    subject: str = Field(default="", examples=["Cannot log in to the console"])
    body: str = Field(examples=["I cannot sign in to my account."])
    customer_tier: Literal["standard", "business", "enterprise"] = "standard"
    customer_region: Literal["north_america", "europe", "asia_pacific", "latin_america"] | None = None
    language_fluency: Literal["fluent", "non_fluent"] = "fluent"
    labels: TicketLabels = Field(default_factory=TicketLabels)

    @field_validator("ticket_id", "subject", "body")
    @classmethod
    def reject_blank_or_placeholder_text(cls, value, info):
        value = value.strip()
        # Chat tickets legitimately have no subject.
        if value.casefold() == "string" or (not value and info.field_name != "subject"):
            raise ValueError("Provide a real value instead of a blank or Swagger placeholder.")
        return value


def _unique_sources(documents):
    best_scores = {}
    for document in documents:
        doc_id = document["doc_id"]
        score = document.get("score")
        if doc_id not in best_scores:
            best_scores[doc_id] = score
        elif score is not None and (best_scores[doc_id] is None or score > best_scores[doc_id]):
            best_scores[doc_id] = score
    return [{"doc_id": doc_id, "score": score} for doc_id, score in best_scores.items()]


def _log_ticket_decision(
    ticket,
    classification,
    documents,
    route,
    stage_results,
    guardrail_results,
    stage="pipeline_complete",
):
    source_records = _unique_sources(documents)
    prompt_versions = []
    classification_stage = stage_results.get("classification")
    if classification_stage and classification_stage != "skipped_input_safeguard":
        prompt_versions.append(classification.get("prompt_version", CLASSIFY_PROMPT_VERSION))
    if isinstance(stage_results.get("retrieval"), dict):
        prompt_versions.append(RETRIEVE_PROMPT_VERSION)
    if stage_results.get("generation") not in (None, "not_run_escalated"):
        prompt_versions.append(GENERATE_PROMPT_VERSION)
    if classification_stage == "skipped_input_safeguard":
        model_name = "not-called-input-screen"
    elif not classification_stage:
        model_name = "not-called-pipeline-failure"
    elif classification.get("provider_mode") == "offline_rules":
        model_name = "offline-rules"
    elif classification.get("provider_error") == "daily_provider_call_budget_exhausted":
        model_name = "provider-budget-exhausted"
    else:
        model_name = env_value("MODEL_NAME")
    log_decision(
        ticket_id=ticket.ticket_id,
        stage=stage,
        prediction=classification.get("intent", "unclear_request"),
        confidence=classification.get("confidence", 0.0),
        threshold=route.get("threshold", float(env_value("CONFIDENCE_THRESHOLD"))),
        action_taken=route["action"],
        reason=route["reason"],
        sources_used=source_records,
        guardrails=guardrail_results,
        prompt_version=";".join(prompt_versions),
        requirement_ids=["GOV-LOG-01", "GOV-ROUTE-01", "GOV-PRIV-01", "GOV-KILL-01"],
        input_summary={
            "ticket_id": ticket.ticket_id,
            "channel": ticket.channel,
            "customer_tier": ticket.customer_tier,
            "customer_region": ticket.customer_region or "unknown",
            "language_fluency": ticket.language_fluency,
            "subject_characters": len(ticket.subject),
            "body_characters": len(ticket.body),
        },
        model_name=model_name,
        prediction_details={
            "intent": classification.get("intent"),
            "urgency": classification.get("urgency"),
            "confidence": classification.get("confidence", 0.0),
        },
        alternatives=classification.get("alternatives", []),
        stage_results=stage_results,
        guardrail_results=guardrail_results,
    )


@app.post("/process_ticket")
def process_ticket_endpoint(ticket: TicketRequest):
    audit_state = {"written": False}
    try:
        return _process_valid_ticket(ticket, audit_state)
    except Exception as error:
        failure_classification = {
            "intent": "unclear_request",
            "urgency": "high",
            "confidence": 0.0,
            "prompt_version": CLASSIFY_PROMPT_VERSION,
            "alternatives": [],
        }
        failure_route = {
            "action": "escalate",
            "reason": "A processing failure occurred; no response was sent.",
            "threshold": float(env_value("CONFIDENCE_THRESHOLD")),
        }
        if not audit_state["written"]:
            try:
                _log_ticket_decision(
                    ticket,
                    failure_classification,
                    [],
                    failure_route,
                    {"pipeline_failure": type(error).__name__},
                    {"processing": "failed_closed"},
                    stage="pipeline_failure",
                )
            except Exception as audit_error:
                raise HTTPException(
                    status_code=503,
                    detail="Processing failed and the decision could not be recorded; no response was sent.",
                ) from audit_error
        try:
            TICKETS_PROCESSED.labels(channel=ticket.channel, outcome="escalate").inc()
        except Exception:
            pass
        return {
            "ticket_id": ticket.ticket_id,
            "intent": "unclear_request",
            "urgency": "high",
            "confidence": 0.0,
            "action": "escalate",
            "reason": failure_route["reason"],
            "response_text": None,
            "sources_used": [],
            "guardrails": {"processing": "failed_closed"},
        }


def _process_valid_ticket(ticket, audit_state):
    started = perf_counter()
    normalized_ticket = normalize_ticket(ticket.model_dump())
    normalized_ticket["raw"]["labels"] = ticket.labels.model_dump(exclude_none=True)
    injection_findings = detect_instruction_injection(ticket.subject + "\n" + ticket.body)
    privacy_findings = detect_private_data(ticket.subject + "\n" + ticket.body)
    kill_switch_was_active = auto_response_disabled()

    stage_results = {
        "normalization": "passed",
        "instruction_integrity": "passed",
        "privacy_screening": "passed",
    }
    documents = []
    generated_response = None
    guardrail_results = {
        "input_private_data": "passed",
        "outbound_private_data": "not_run",
        "grounding": "not_run",
        "tone_scope": "not_run",
        "instruction_integrity": "passed",
    }

    if injection_findings or privacy_findings or kill_switch_was_active:
        classification = {
            "intent": "unclear_request",
            "urgency": "high",
            "confidence": 0.0,
            "alternatives": [],
            "prompt_version": CLASSIFY_PROMPT_VERSION,
        }
        route = {
            "action": "escalate",
            "reason": (
                "Automatic responses are disabled by the operator kill switch."
                if kill_switch_was_active
                else "Ticket contains a suspected attempt to override system instructions."
                if injection_findings
                else "Ticket contains a recognized sensitive-data pattern; it was not sent to the model provider."
            ),
            "threshold": float(env_value("CONFIDENCE_THRESHOLD")),
        }
        skip_reason = "skipped_input_safeguard"
        stage_results["classification"] = skip_reason
        stage_results["retrieval"] = skip_reason
        stage_results["privacy_screening"] = "blocked" if privacy_findings else "passed"
        if kill_switch_was_active:
            stage_results["kill_switch"] = "active_before_processing"
        guardrail_results["instruction_integrity"] = (
            "blocked" if injection_findings else "passed"
        )
        guardrail_results["input_private_data"] = (
            "blocked" if privacy_findings else "passed"
        )
        if kill_switch_was_active:
            guardrail_results["kill_switch"] = "active"
        guardrail_results["findings"] = {
            "instruction_integrity": injection_findings,
            "private_data": privacy_findings,
        }
    else:
        stage_timings = {}
        stage_started = perf_counter()
        classification = classify_ticket(normalized_ticket)
        stage_timings["classification"] = perf_counter() - stage_started
        stage_results["classification"] = (
            "provider_failed"
            if classification.get("provider_error")
            else classification.get("provider_mode", "provider_completed")
        )
        stage_started = perf_counter()
        documents = retrieve_docs(normalized_ticket, classification["intent"])
        stage_timings["retrieval"] = perf_counter() - stage_started
        stage_results["duration_seconds"] = stage_timings
        stage_results["retrieval"] = {
            "status": "completed" if documents else "no_evidence",
            "documents_found": len(documents),
            "retrieval_mode": documents[0].get("retrieval_mode", "unknown") if documents else "none",
        }
        route = determine_route(normalized_ticket, classification, documents)
        if classification.get("provider_error"):
            route["action"] = "escalate"
            route["reason"] = "Classification provider failed or returned invalid output; ticket escalated."
            guardrail_results["provider"] = "failed"
            guardrail_results["provider_error"] = classification["provider_error"]

        if route["action"] == "auto_respond":
            stage_started = perf_counter()
            generated_response = generate_response(normalized_ticket, documents)
            stage_timings["generation"] = perf_counter() - stage_started
            if generated_response.get("provider_error"):
                guardrail_results["provider"] = "failed"
                guardrail_results["provider_error"] = generated_response["provider_error"]
                guardrail_results["grounding"] = False
                route["action"] = "escalate"
                route["reason"] = "Response generation failed; no draft was sent and the ticket was escalated."
            else:
                checked = check_pii_and_hallucinations(
                    generated_response.get("response_text", ""), documents
                )
                guardrail_results["outbound_private_data"] = (
                    "passed" if checked["results"]["private_data"] else "blocked"
                )
                guardrail_results["grounding"] = (
                    "passed" if checked["results"]["grounding"] else "blocked"
                )
                guardrail_results["tone_scope"] = (
                    "passed" if checked["results"]["tone_scope"] else "blocked"
                )
                guardrail_results["instruction_integrity"] = (
                    "passed"
                    if checked["results"]["instruction_integrity"]
                    else "blocked"
                )
                guardrail_results["violations"] = checked["violations"]
                if not checked["passed"]:
                    route["action"] = "escalate"
                    route["reason"] = (
                        "Guardrail blocked the draft; no response was sent. "
                        + ", ".join(checked["violations"])
                    )
                else:
                    generated_response["response_text"] = checked["sanitized_response"]
            stage_results["generation"] = "completed" if not generated_response.get("provider_error") else "failed"
            stage_results["validation"] = "passed" if route["action"] == "auto_respond" else "blocked"
        else:
            stage_results["generation"] = "not_run_escalated"
            stage_results["validation"] = "not_applicable"

    if injection_findings or privacy_findings:
        if injection_findings:
            GUARDRAIL_BLOCKS.labels(guardrail="instruction_integrity").inc()
        if privacy_findings:
            GUARDRAIL_BLOCKS.labels(guardrail="private_data").inc()
    elif kill_switch_was_active:
        GUARDRAIL_BLOCKS.labels(guardrail="kill_switch").inc()
    elif guardrail_results.get("violations") or guardrail_results.get("provider") == "failed":
        for violation in guardrail_results.get("violations", []):
            guardrail_name = violation.split(":", 1)[0]
            GUARDRAIL_BLOCKS.labels(guardrail=guardrail_name).inc()
        if guardrail_results.get("provider") == "failed":
            GUARDRAIL_BLOCKS.labels(guardrail="provider").inc()

    if route["action"] == "auto_respond" and auto_response_disabled():
        route["action"] = "escalate"
        route["reason"] = "Automatic responses were disabled before the draft could be returned."
        guardrail_results["kill_switch"] = "active"
        stage_results["kill_switch"] = "blocked_before_response"
        generated_response = None
        GUARDRAIL_BLOCKS.labels(guardrail="kill_switch").inc()

    stage_results["routing"] = {
        "action": route["action"],
        "threshold": route.get("threshold"),
        "reason": route["reason"],
    }

    confidence = float(classification.get("confidence", 0.0))
    _log_ticket_decision(
        ticket,
        classification,
        documents,
        route,
        stage_results,
        guardrail_results,
    )
    audit_state["written"] = True
    try:
        TICKETS_PROCESSED.labels(channel=ticket.channel, outcome=route["action"]).inc()
        RESPONSE_LATENCY.observe(perf_counter() - started)
        CLASSIFICATION_CONFIDENCE.observe(max(0.0, min(1.0, confidence)))
    except Exception:
        pass

    return {
        "ticket_id": ticket.ticket_id,
        "intent": classification.get("intent", "unclear_request"),
        "urgency": classification.get("urgency", "medium"),
        "confidence": confidence,
        "action": route["action"],
        "reason": route["reason"],
        "response_text": (
            generated_response.get("response_text")
            if route["action"] == "auto_respond" and generated_response
            else None
        ),
        "sources_used": [source["doc_id"] for source in _unique_sources(documents)],
        "guardrails": guardrail_results,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("API_HOST", "127.0.0.1"),
        port=int(os.getenv("API_PORT", "8000")),
    )
