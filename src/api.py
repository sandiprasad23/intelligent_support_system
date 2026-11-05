import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel, Field
from src.ingest import normalize_ticket
from src.classify import classify_ticket
from src.retrieve import retrieve_docs
from src.route import determine_route
from src.generate import generate_response
from src.guardrails import check_pii_and_hallucinations
from src.logging_store import log_decision
from src.monitoring import (
    TICKETS_PROCESSED,
    RESPONSE_LATENCY,
    GUARDRAIL_BLOCKS,
    start_metrics_server,
)


@asynccontextmanager
async def lifespan(app):
    start_metrics_server()
    yield


app = FastAPI(
    title="CloudServe Grounded Support Engine API",
    version="1.0",
    lifespan=lifespan,
)

class TicketRequest(BaseModel):
    ticket_id: str
    channel: str = "email"
    subject: str
    body: str
    customer_tier: str = "standard"
    language_fluency: str = "fluent"
    labels: dict = Field(default_factory=dict)

@app.post("/process_ticket")
def process_ticket_endpoint(ticket: TicketRequest):
    start_time = time.time()
    t_dict = ticket.model_dump()
    t_dict["raw"] = {"labels": ticket.labels}
    
    # 1. Ingest & Normalize
    norm_ticket = normalize_ticket(t_dict)
    
    # 2. Classify Intent & Urgency
    cls = classify_ticket(norm_ticket)
    
    # 3. Retrieve Docs
    docs = retrieve_docs(norm_ticket, cls["intent"])
    
    # 4. Route Decision
    route = determine_route(norm_ticket, cls, docs)
    
    response_payload = {
        "ticket_id": ticket.ticket_id,
        "intent": cls["intent"],
        "urgency": cls["urgency"],
        "action": route["action"],
        "reason": route["reason"],
        "response_text": None,
        "sources_used": [d["doc_id"] for d in docs]
    }
    
    if route["action"] == "auto_respond":
        # 5. Generate Response
        gen = generate_response(norm_ticket, docs)
        
        # 6. Guardrail Checks
        g_check = check_pii_and_hallucinations(gen["response_text"], docs)
        if not g_check["passed"]:
            GUARDRAIL_BLOCKS.labels(guardrail="PII_Secret_Check").inc()
            route["action"] = "escalate"
            response_payload["action"] = "escalate"
            response_payload["reason"] = f"Guardrail blocked response: {', '.join(g_check['violations'])}"
        else:
            response_payload["response_text"] = g_check["sanitized_response"]
            
    # 7. Log Decision
    log_decision(
        ticket_id=ticket.ticket_id,
        stage="pipeline_complete",
        prediction=cls["intent"],
        confidence=cls["confidence"],
        threshold=route["threshold"],
        action_taken=response_payload["action"],
        reason=response_payload["reason"],
        sources_used=",".join(response_payload["sources_used"]),
        prompt_version=cls.get("prompt_version", ""),
        requirement_ids="FR-01,FR-02,FR-03,FR-05"
    )
    
    elapsed = time.time() - start_time
    RESPONSE_LATENCY.observe(elapsed)
    TICKETS_PROCESSED.labels(channel=ticket.channel, outcome=response_payload["action"]).inc()
    
    return response_payload

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=os.getenv("API_HOST", "127.0.0.1"),
        port=int(os.getenv("API_PORT", "8000")),
    )
