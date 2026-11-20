import json
import requests
from src.config import PROJECT_ROOT, env_value
from src.logging_store import reserve_llm_call

VALID_INTENTS = [
    "data_residency", "data_export", "rollback_request", "deployment_failure",
    "security_incident", "compliance_request", "database_issue", "sso_configuration",
    "billing_query", "api_usage_question", "performance_degradation", "quota_or_overage",
    "account_access", "onboarding", "integration_help", "webhook_issue",
    "authentication_failure", "feature_request", "api_key_issue", "configuration_help",
    "unclear_request", "rate_limit"
]

CLASSIFY_PROMPT_VERSION = "PR-01-v1.2"


def _configured_api_key():
    api_key = env_value("OPENROUTER_API_KEY", allow_empty=True).strip()
    if not api_key or api_key.casefold() == "your_key_here" or api_key.casefold().startswith("enter your"):
        return None
    return api_key

def classify_ticket(ticket: dict) -> dict:
    """Classifies ticket intent, urgency, and extracts key entities."""
    api_key = _configured_api_key()
    model_name = env_value("MODEL_NAME")
    
    prompt_template = (PROJECT_ROOT / "prompts" / "classify_v1.txt").read_text(
        encoding="utf-8"
    )
    prompt = prompt_template.format(
        valid_intents=", ".join(VALID_INTENTS),
        channel=ticket["channel"],
        tier=ticket["customer_tier"],
        fluency=ticket["language_fluency"],
        subject=ticket["subject"],
        body=ticket["body"],
    )
    
    if not api_key:
        # Rule-based fallback for test/offline environments
        body_lower = ticket['body'].lower()
        if "rollback" in body_lower or "revert" in body_lower:
            intent = "rollback_request"
        elif "deploy" in body_lower or "build" in body_lower or "fail" in body_lower:
            intent = "deployment_failure"
        elif "key" in body_lower or "401" in body_lower or "auth" in body_lower:
            intent = "api_key_issue"
        elif "bill" in body_lower or "quota" in body_lower:
            intent = "billing_query"
        else:
            intent = "configuration_help"
            
        urgency = "high" if "production" in body_lower or "down" in body_lower else "medium"
        return {
            "intent": intent,
            "urgency": urgency,
            "confidence": 0.65,
            "extracted_entities": [],
            "prompt_version": CLASSIFY_PROMPT_VERSION,
            "provider_mode": "offline_rules",
            "alternatives": [],
        }

    if not reserve_llm_call():
        return {
            "intent": "unclear_request",
            "urgency": "medium",
            "confidence": 0.0,
            "extracted_entities": [],
            "prompt_version": CLASSIFY_PROMPT_VERSION,
            "provider_error": "daily_provider_call_budget_exhausted",
        }

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 500,
    }
    
    try:
        res = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=15)
        res.raise_for_status()
        res_json = res.json()
        content = res_json["choices"][0]["message"]["content"]
        # Parse json structure
        match = json.loads(content[content.find('{'):content.rfind('}')+1])
        if match.get("intent") not in VALID_INTENTS:
            raise ValueError("Classifier returned an unsupported intent.")
        if match.get("urgency") not in {"high", "medium", "low"}:
            raise ValueError("Classifier returned an unsupported urgency.")
        confidence = float(match.get("confidence", -1))
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("Classifier returned an invalid confidence score.")
        alternatives = []
        raw_alternatives = match.get("alternatives", [])
        if isinstance(raw_alternatives, list):
            for alternative in raw_alternatives[:3]:
                if not isinstance(alternative, dict) or alternative.get("intent") not in VALID_INTENTS:
                    continue
                try:
                    alternative_confidence = float(alternative.get("confidence", -1))
                except (TypeError, ValueError):
                    continue
                if 0.0 <= alternative_confidence <= 1.0:
                    alternatives.append(
                        {
                            "intent": alternative["intent"],
                            "confidence": alternative_confidence,
                        }
                    )
        match["confidence"] = confidence
        match["alternatives"] = alternatives
        match["prompt_version"] = CLASSIFY_PROMPT_VERSION
        return match
    except Exception as error:
        return {
            "intent": "unclear_request",
            "urgency": "medium",
            "confidence": 0.0,
            "extracted_entities": [],
            "prompt_version": CLASSIFY_PROMPT_VERSION,
            "provider_error": type(error).__name__,
        }
