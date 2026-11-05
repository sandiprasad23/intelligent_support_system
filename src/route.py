import os

def determine_route(ticket: dict, classification: dict, retrieved_docs: list) -> dict:
    """Evaluates auto-response vs escalation decision thresholds."""
    confidence_threshold = float(os.getenv("CONFIDENCE_THRESHOLD", "0.80"))
    
    intent = classification.get("intent")
    urgency = classification.get("urgency")
    confidence = classification.get("confidence", 0.0)
    
    # 1. Check intent-based hard constraints
    must_escalate_intents = ["security_incident", "database_issue"]
    if intent in must_escalate_intents:
        return {
            "action": "escalate",
            "reason": f"Intent '{intent}' requires Tier 2 engineer intervention.",
            "threshold": confidence_threshold
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
