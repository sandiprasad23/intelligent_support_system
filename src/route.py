import os
import re
from math import isfinite
from pathlib import Path

from src.config import env_float, env_value


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FEATURE_REQUEST_PATTERNS = (
    re.compile(r"\bfeature\s+(?:request|suggestion)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:would|could|should|please|can you|we need|i need)\b"
        r".{0,80}\b(?:add|support|allow|enable|offer|introduce|provide|set|create)\b"
        r".{0,80}\b(?:feature|option|ability|cap|limit|setting|functionality)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:useful|helpful|great|nice)\b.{0,40}\bto\b"
        r".{0,60}\b(?:add|support|allow|enable|offer|introduce|provide|set|create)\b",
        re.IGNORECASE,
    ),
)


def auto_response_disabled():
    configured_path = Path(
        os.getenv("AUTO_RESPONSE_KILL_SWITCH_FILE", "storage/AUTO_RESPONSE_DISABLED")
    )
    if not configured_path.is_absolute():
        configured_path = PROJECT_ROOT / configured_path
    return configured_path.exists()


def _explicit_feature_request(ticket):
    text = " ".join((ticket.get("subject", ""), ticket.get("body", "")))
    return any(pattern.search(text) for pattern in FEATURE_REQUEST_PATTERNS)


def determine_route(ticket: dict, classification: dict, retrieved_docs: list) -> dict:
    """Evaluates auto-response vs escalation decision thresholds."""
    confidence_threshold = env_float("CONFIDENCE_THRESHOLD", safe_value=float("nan"))

    if not isfinite(confidence_threshold) or not 0.80 <= confidence_threshold <= 1.0:
        return {
            "action": "escalate",
            "reason": "Configured confidence threshold is outside the permitted 0.80-1.00 range.",
            "threshold": confidence_threshold,
        }
    
    intent = classification.get("intent")
    urgency = classification.get("urgency")
    confidence = classification.get("confidence", 0.0)

    if auto_response_disabled():
        return {
            "action": "escalate",
            "reason": "Automatic responses are disabled by the operator kill switch.",
            "threshold": confidence_threshold,
        }

    if _explicit_feature_request(ticket):
        return {
            "action": "escalate",
            "reason": "Ticket appears to request a product feature; human review is required.",
            "threshold": confidence_threshold,
        }
    
    # 1. Check intent-based hard constraints
    # Every development ticket with these intents is labelled must-escalate.
    must_escalate_intents = [
        "security_incident", "database_issue",
        "compliance_request", "feature_request", "unclear_request",
    ]
    if intent in must_escalate_intents:
        return {
            "action": "escalate",
            "reason": f"Intent '{intent}' requires human review.",
            "threshold": confidence_threshold
        }

    if classification.get("provider_mode") == "offline_rules":
        return {
            "action": "escalate",
            "reason": "Offline keyword classification is not eligible for automatic customer responses.",
            "threshold": confidence_threshold,
        }
        
    # 2. Check label/ticket constraints
    raw_labels = ticket.get("raw", {}).get("labels", {})
    if raw_labels.get("must_not_auto_respond") is True or raw_labels.get("expected_route") == "escalate":
        return {
            "action": "escalate",
            "reason": "Safety governance flag 'must_not_auto_respond' or explicit escalation label present.",
            "threshold": confidence_threshold
        }
        
    # 3. Check documentation coverage & confidence threshold
    if not retrieved_docs:
        return {
            "action": "escalate",
            "reason": "No relevant documentation found for inquiry (answerable_from_docs=False).",
            "threshold": confidence_threshold
        }

    if any(document.get("retrieval_mode") == "json_fallback" for document in retrieved_docs):
        return {
            "action": "escalate",
            "reason": "Vector retrieval is unavailable; unranked fallback evidence is not eligible for automatic response.",
            "threshold": confidence_threshold,
        }

    max_document_age = int(os.getenv("MAX_DOC_AGE_DAYS", "180"))
    stale_documents = [
        document
        for document in retrieved_docs
        if document.get("review_age_days") is None
        or document["review_age_days"] > max_document_age
    ]
    if stale_documents:
        stale_ids = ", ".join(document["doc_id"] for document in stale_documents)
        return {
            "action": "escalate",
            "reason": f"Retrieved documentation exceeds the {max_document_age}-day review limit: {stale_ids}.",
            "threshold": confidence_threshold,
        }

    minimum_retrieval_score = float(os.getenv("MIN_RETRIEVAL_SCORE", "0.30"))
    scores = [document.get("score") for document in retrieved_docs]
    if (
        not isfinite(minimum_retrieval_score)
        or not 0.0 <= minimum_retrieval_score <= 1.0
        or not scores
        or any(
            score is None
            or not isinstance(score, (int, float))
            or not isfinite(score)
            or not 0.0 <= score <= 1.0
            for score in scores
        )
    ):
        return {
            "action": "escalate",
            "reason": "Retrieval relevance or its configured threshold is invalid or unavailable.",
            "threshold": confidence_threshold,
        }
    if max(scores) < minimum_retrieval_score:
        return {
            "action": "escalate",
            "reason": f"Best retrieval score is below the {minimum_retrieval_score:.2f} evidence floor.",
            "threshold": confidence_threshold,
        }
        
    if confidence < confidence_threshold:
        return {
            "action": "escalate",
            "reason": f"Classification confidence ({confidence:.2f}) below threshold ({confidence_threshold:.2f}).",
            "threshold": confidence_threshold
        }
        
    return {
        "action": "auto_respond",
        "reason": f"High confidence ({confidence:.2f}) with valid document retrieval.",
        "threshold": confidence_threshold
    }
