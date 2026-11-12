import re


SECRET_PATTERNS = (
    re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9_-]{16,}\b", re.IGNORECASE),
    re.compile(r"\bsk-or-v1-[A-Fa-f0-9]{16,}\b", re.IGNORECASE),
)
PRIVATE_DATA_PATTERNS = {
    "email_address": re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    "customer_identifier": re.compile(r"\bCUST-\d{3,}\b", re.IGNORECASE),
    "payment_card_number": re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
    "phone_number": re.compile(r"(?<!\w)\+?\d[\d(). -]{7,}\d(?!\w)"),
}
INJECTION_PATTERNS = (
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions", re.IGNORECASE),
    re.compile(r"reveal\s+(the\s+)?(system|developer)\s+prompt", re.IGNORECASE),
    re.compile(r"disregard\s+(the\s+)?(system|developer|safety)\s+instructions", re.IGNORECASE),
    re.compile(r"bypass\s+(the\s+)?(guardrails|safety|policy)", re.IGNORECASE),
)
CITATION_PATTERN = re.compile(r"\[Doc ID:\s*([^\]]+)\]", re.IGNORECASE)
UNSUPPORTED_CLAIM_PATTERNS = (
    re.compile(r"\b(?:we have|we've)\s+(?:already\s+)?(?:issued|processed)\s+(?:your\s+)?refund\b", re.IGNORECASE),
    re.compile(r"\bthe issue has been fixed on our side\b", re.IGNORECASE),
    re.compile(r"\b(?:will|shall) be fixed by\s+(?:\d{4}-\d{2}-\d{2}|tomorrow|today)\b", re.IGNORECASE),
)
PROMPT_DISCLOSURE_PATTERNS = (
    re.compile(r"\b(?:system|developer) prompt\s*[:=]", re.IGNORECASE),
    re.compile(r"\bhere are my (?:system|developer) instructions\b", re.IGNORECASE),
    re.compile(r"\bi have ignored (?:the )?(?:system|developer|safety) instructions\b", re.IGNORECASE),
)


def detect_instruction_injection(ticket_text: str) -> list[str]:
    """Return matched governance codes for common attempts to override system instructions."""
    matches = []
    for pattern in INJECTION_PATTERNS:
        if pattern.search(ticket_text):
            matches.append("instruction_override_attempt")
            break
    return matches


def detect_private_data(text: str) -> list[str]:
    findings = []
    if any(pattern.search(text) for pattern in SECRET_PATTERNS):
        findings.append("secret")
    for name, pattern in PRIVATE_DATA_PATTERNS.items():
        if pattern.search(text):
            findings.append(name)
    return findings


def check_pii_and_hallucinations(response_text: str, retrieved_docs: list) -> dict:
    """Fail closed when output exposes private data, lacks valid citations, or makes blocked claims."""
    violations = []
    sanitized_text = response_text

    for pattern in SECRET_PATTERNS:
        if pattern.search(response_text):
            sanitized_text = pattern.sub("[REDACTED_SECRET]", sanitized_text)

    for name, pattern in PRIVATE_DATA_PATTERNS.items():
        if pattern.search(response_text):
            sanitized_text = pattern.sub("[REDACTED_PRIVATE_DATA]", sanitized_text)

    violations.extend(
        f"private_data:{finding}" for finding in detect_private_data(response_text)
    )

    expected_doc_ids = {str(document.get("doc_id", "")) for document in retrieved_docs}
    citations = set(CITATION_PATTERN.findall(response_text))
    if retrieved_docs and not citations:
        violations.append("grounding:citation_missing")
    elif citations - expected_doc_ids:
        violations.append("grounding:unknown_document_citation")

    for pattern in UNSUPPORTED_CLAIM_PATTERNS:
        if pattern.search(response_text):
            violations.append("tone_scope:unsupported_commitment")
            break

    for pattern in PROMPT_DISCLOSURE_PATTERNS:
        if pattern.search(response_text):
            violations.append("instruction_integrity:prompt_disclosure")
            break

    return {
        "passed": not violations,
        "violations": violations,
        "results": {
            "private_data": not any(item.startswith("private_data:") for item in violations),
            "grounding": not any(item.startswith("grounding:") for item in violations),
            "tone_scope": not any(item.startswith("tone_scope:") for item in violations),
            "instruction_integrity": not any(
                item.startswith("instruction_integrity:") for item in violations
            ),
        },
        "sanitized_response": sanitized_text,
    }
