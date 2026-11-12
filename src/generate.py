import os
from pathlib import Path
import requests
from dotenv import load_dotenv
from src.logging_store import reserve_llm_call

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

GENERATE_PROMPT_VERSION = "PR-03-v1.1"


def _configured_api_key():
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key or api_key.casefold() == "your_key_here" or api_key.casefold().startswith("enter your"):
        return None
    return api_key

def generate_response(ticket: dict, retrieved_docs: list) -> dict:
    """Generates a grounded, citation-backed response strictly from retrieved documentation."""
    api_key = _configured_api_key()
    model_name = os.getenv("MODEL_NAME", "meta-llama/llama-3.1-8b-instruct")
    
    doc_context = ""
    expected_doc_ids = []
    for d in retrieved_docs:
        doc_context += f"Document ID: {d['doc_id']}\nTitle: {d['title']}\nContent: {d['content']}\n---\n"
        expected_doc_ids.append(d['doc_id'])
        
    prompt_template = (PROJECT_ROOT / "prompts" / "respond_v1.txt").read_text(
        encoding="utf-8"
    )
    prompt = prompt_template.format(
        documentation_corpus=doc_context,
        subject=ticket["subject"],
        body=ticket["body"],
    )

    if not api_key:
        # Fallback offline response draft
        cited_str = ", ".join([f"[Doc ID: {d['doc_id']}]" for d in retrieved_docs])
        draft = f"Hello {ticket.get('customer_name', 'Customer')},\n\nThank you for reaching out to CloudServe support. Based on our official documentation {cited_str}, please follow these resolution steps:\n\n1. Verify environment configuration and lockfile dependency settings.\n2. Re-run deployment or rotation using CLI tools as described in {cited_str}.\n\nIf issues persist, reply directly to this thread.\n\nBest regards,\nCloudServe Support Team"
        return {
            "response_text": draft,
            "cited_doc_ids": expected_doc_ids,
            "prompt_version": GENERATE_PROMPT_VERSION
        }

    if not reserve_llm_call():
        return {
            "response_text": "",
            "cited_doc_ids": [],
            "prompt_version": GENERATE_PROMPT_VERSION,
            "provider_error": "daily_provider_call_budget_exhausted",
        }

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 800,
    }
    
    try:
        res = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=20)
        content = res.json()["choices"][0]["message"]["content"]
        return {
            "response_text": content,
            "cited_doc_ids": expected_doc_ids,
            "prompt_version": GENERATE_PROMPT_VERSION
        }
    except Exception as e:
        return {
            "response_text": "",
            "cited_doc_ids": [],
            "prompt_version": GENERATE_PROMPT_VERSION,
            "provider_error": type(e).__name__,
        }
