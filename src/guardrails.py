import re

def check_pii_and_hallucinations(response_text: str, retrieved_docs: list) -> dict:
    """Validates PII masking and doc citation alignment."""
    passed = True
    violations = []
    
    # 1. PII Check for raw secret exposure (e.g. secret keys sk_live_...)
    if re.search(r'sk_[live|test]_[0-9a-zA-Z]{24,}', response_text):
        passed = False
        violations.append("Raw API secret key detected in response.")
        
    # 2. Citation Check
    valid_ids = [d["doc_id"] for d in retrieved_docs]
    if retrieved_docs and not any(doc_id in response_text for doc_id in valid_ids):
        # Warning if citation missing
        violations.append("Response lacks explicit Doc ID citation.")
        
    # Mask potential sensitive patterns if any
    clean_text = re.sub(r'(sk_[live|test]_[0-9a-zA-Z]{24,})', '[REDACTED_KEY]', response_text)
    
    return {
        "passed": passed,
        "violations": violations,
        "sanitized_response": clean_text
    }
