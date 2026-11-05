import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

VALID_INTENTS = [
    "data_residency", "data_export", "rollback_request", "deployment_failure",
    "security_incident", "compliance_request", "database_issue", "sso_configuration",
    "billing_query", "api_usage_question", "performance_degradation", "quota_or_overage",
    "account_access", "onboarding", "integration_help", "webhook_issue",
    "authentication_failure", "feature_request", "api_key_issue", "configuration_help",
    "unclear_request", "rate_limit"
]

CLASSIFY_PROMPT_VERSION = "PR-01-v1.0"

def classify_ticket(ticket: dict) -> dict:
    """Classifies ticket intent, urgency, and extracts key entities."""
    api_key = os.getenv("OPENROUTER_API_KEY")
    model_name = os.getenv("MODEL_NAME", "meta-llama/llama-3.1-8b-instruct")
    
    prompt = f"""You are an expert support classifier for CloudServe Solutions.
Analyze the following customer ticket and output JSON with:
1. "intent": string (must be one of {VALID_INTENTS})
2. "urgency": string ("high", "medium", "low")
3. "confidence": float between 0.0 and 1.0
4. "extracted_entities": list of extracted parameters (e.g. error codes, service names)

Ticket Context:
Channel: {ticket['channel']}
Tier: {ticket['customer_tier']}
Fluency: {ticket['language_fluency']}
Subject: {ticket['subject']}
Body: <customer_ticket>{ticket['body']}</customer_ticket>
"""
    
    if not api_key or api_key == "your_key_here":
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
            "confidence": 0.85,
            "extracted_entities": [],
            "prompt_version": CLASSIFY_PROMPT_VERSION
        }

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0
    }
    
    try:
        res = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=15)
        res_json = res.json()
        content = res_json["choices"][0]["message"]["content"]
        # Parse json structure
        match = json.loads(content[content.find('{'):content.rfind('}')+1])
        match["prompt_version"] = CLASSIFY_PROMPT_VERSION
        return match
    except Exception as e:
        return {
            "intent": "unclear_request",
            "urgency": "medium",
            "confidence": 0.50,
            "extracted_entities": [],
            "prompt_version": CLASSIFY_PROMPT_VERSION
        }
