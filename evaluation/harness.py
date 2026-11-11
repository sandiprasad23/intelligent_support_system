import argparse
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from src.classify import classify_ticket
from src.generate import generate_response
from src.guardrails import check_pii_and_hallucinations
from src.ingest import normalize_ticket
from src.retrieve import retrieve_docs
from src.route import determine_route


DEFAULT_TOP_K = 3
REQUIRED_TICKET_FIELDS = {"ticket_id", "channel", "subject", "body", "labels"}
REQUIRED_LABEL_FIELDS = {
    "intent",
    "urgency",
    "expected_route",
    "answerable_from_docs",
    "expected_doc_ids",
    "must_not_auto_respond",
}
CITATION_PATTERN = re.compile(r"\[Doc ID:\s*([^\]]+)\]")


def _load_tickets(input_path):
    input_path = Path(input_path)
    tickets = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(tickets, list):
        raise ValueError("Input JSON must contain a list of ticket objects.")

    for index, ticket in enumerate(tickets):
        if not isinstance(ticket, dict):
            raise ValueError(f"Ticket at index {index} must be a JSON object.")
        missing = REQUIRED_TICKET_FIELDS - ticket.keys()
        if missing:
            raise ValueError(
                f"Ticket at index {index} is missing fields: {', '.join(sorted(missing))}"
            )
        labels = ticket["labels"]
        if not isinstance(labels, dict):
            raise ValueError(f"Ticket at index {index} has non-object labels.")
        missing_labels = REQUIRED_LABEL_FIELDS - labels.keys()
        if missing_labels:
            raise ValueError(
                f"Ticket at index {index} is missing label fields: "
                f"{', '.join(sorted(missing_labels))}"
            )
        if not isinstance(labels["expected_doc_ids"], list):
            raise ValueError(
                f"Ticket at index {index} labels.expected_doc_ids must be a list."
            )

    return input_path, tickets


def _evaluate_ticket(ticket, top_k, reference=None):
    started = perf_counter()
    normalized_ticket = normalize_ticket(ticket)
    classification = classify_ticket(normalized_ticket)
    documents = retrieve_docs(normalized_ticket, classification["intent"], top_k=top_k)
    route = determine_route(normalized_ticket, classification, documents)

    action = route["action"]
    response_text = ""
    guardrail_violations = []
    if action == "auto_respond":
        generated = generate_response(normalized_ticket, documents)
        guardrail = check_pii_and_hallucinations(generated["response_text"], documents)
        guardrail_violations = guardrail["violations"]
        if guardrail["passed"]:
            response_text = guardrail["sanitized_response"]
        else:
            action = "escalate"

    must_mention = reference.get("must_mention", []) if reference else []
    must_not_claim = reference.get("must_not_claim", []) if reference else []
    response_lower = response_text.lower()

    expected_doc_ids = ticket["labels"]["expected_doc_ids"]
    retrieved_doc_ids = [document["doc_id"] for document in documents]
    cited_doc_ids = CITATION_PATTERN.findall(response_text)

    return {
        "ticket_id": ticket["ticket_id"],
        "channel": ticket["channel"],
        "customer_tier": ticket.get("customer_tier", "unknown"),
        "customer_region": ticket.get("customer_region", "unknown"),
        "language_fluency": ticket.get("language_fluency", "unknown"),
        "expected_intent": ticket["labels"]["intent"],
        "predicted_intent": classification.get("intent"),
        "expected_urgency": ticket["labels"]["urgency"],
        "predicted_urgency": classification.get("urgency"),
        "expected_route": ticket["labels"]["expected_route"],
        "predicted_route": action,
        "answerable_from_docs": ticket["labels"]["answerable_from_docs"],
        "predicted_answerable_from_docs": bool(documents),
        "must_not_auto_respond": ticket["labels"]["must_not_auto_respond"],
        "expected_doc_ids": expected_doc_ids,
        "retrieved_doc_ids": retrieved_doc_ids,
        "cited_doc_ids": cited_doc_ids,
        "response_returned": bool(response_text),
        "reference_available": reference is not None,
        "must_mention_total": len(must_mention),
        "must_mention_found": sum(term.lower() in response_lower for term in must_mention),
        "must_not_claim_violations": [
            phrase for phrase in must_not_claim if phrase.lower() in response_lower
        ],
        "guardrail_violations": guardrail_violations,
        "latency_seconds": perf_counter() - started,
    }


def _accuracy(rows, expected_key, predicted_key):
    if not rows:
        return 0.0
    return sum(row[expected_key] == row[predicted_key] for row in rows) / len(rows)


def _citation_metrics(rows):
    response_rows = [row for row in rows if row.get("response_returned", True)]
    expected_count = sum(len(row["expected_doc_ids"]) for row in response_rows)
    matches = sum(
        len(set(row["expected_doc_ids"]) & set(row["cited_doc_ids"]))
        for row in response_rows
    )
    cited_count = sum(len(set(row["cited_doc_ids"])) for row in response_rows)
    return {
        "citation_precision": matches / cited_count if cited_count else 0.0,
        "citation_recall": matches / expected_count if expected_count else 0.0,
    }


def calculate_metrics(rows, top_k=DEFAULT_TOP_K):
    answerable_rows = [
        row for row in rows if row["answerable_from_docs"] and row["expected_doc_ids"]
    ]
    expected_doc_count = sum(len(row["expected_doc_ids"]) for row in answerable_rows)
    found_doc_count = sum(
        len(set(row["expected_doc_ids"]) & set(row["retrieved_doc_ids"]))
        for row in answerable_rows
    )
    hit_count = sum(
        bool(set(row["expected_doc_ids"]) & set(row["retrieved_doc_ids"]))
        for row in answerable_rows
    )

    citation_metrics = _citation_metrics(rows)
    protected_rows = [row for row in rows if row["must_not_auto_respond"]]
    reference_rows = [row for row in rows if row.get("reference_available", False)]
    mention_total = sum(row.get("must_mention_total", 0) for row in reference_rows)
    mention_found = sum(row.get("must_mention_found", 0) for row in reference_rows)

    return {
        "tickets": len(rows),
        "intent_accuracy": _accuracy(rows, "expected_intent", "predicted_intent"),
        "urgency_accuracy": _accuracy(rows, "expected_urgency", "predicted_urgency"),
        "route_accuracy": _accuracy(rows, "expected_route", "predicted_route"),
        "answerability_accuracy": (
            sum(
                row["answerable_from_docs"] == row["predicted_answerable_from_docs"]
                if "predicted_answerable_from_docs" in row
                else row["answerable_from_docs"] == bool(row["retrieved_doc_ids"])
                for row in rows
            ) / len(rows)
            if rows
            else 0.0
        ),
        "must_not_auto_respond_violations": sum(
            row["predicted_route"] == "auto_respond" for row in protected_rows
        ),
        "answerable_queries": len(answerable_rows),
        f"retrieval_hit_at_{top_k}": (
            hit_count / len(answerable_rows) if answerable_rows else 0.0
        ),
        f"retrieval_recall_at_{top_k}": (
            found_doc_count / expected_doc_count if expected_doc_count else 0.0
        ),
        **citation_metrics,
        "mean_latency_seconds": (
            sum(row["latency_seconds"] for row in rows) / len(rows) if rows else 0.0
        ),
        "guardrail_violation_count": sum(len(row["guardrail_violations"]) for row in rows),
        "reference_response_matches": len(reference_rows),
        "must_mention_recall": mention_found / mention_total if mention_total else None,
        "must_not_claim_violation_count": sum(
            len(row.get("must_not_claim_violations", [])) for row in reference_rows
        ),
    }


def calculate_segment_metrics(rows, segment_fields=("customer_tier", "customer_region", "language_fluency")):
    output = {}
    for field in segment_fields:
        groups = defaultdict(list)
        for row in rows:
            groups[str(row[field])].append(row)
        output[field] = {
            value: {
                "tickets": len(group),
                "intent_accuracy": _accuracy(group, "expected_intent", "predicted_intent"),
                "route_accuracy": _accuracy(group, "expected_route", "predicted_route"),
                "answerability_accuracy": (
                    sum(
                        row["answerable_from_docs"] == row["predicted_answerable_from_docs"]
                        if "predicted_answerable_from_docs" in row
                        else row["answerable_from_docs"] == bool(row["retrieved_doc_ids"])
                        for row in group
                    ) / len(group)
                ),
                **_citation_metrics(group),
                "must_not_auto_respond_violations": sum(
                    row["must_not_auto_respond"]
                    and row["predicted_route"] == "auto_respond"
                    for row in group
                ),
            }
            for value, group in sorted(groups.items())
        }
    return output


def run_evaluation(input_path, output_path, top_k=DEFAULT_TOP_K, ground_truth_path=None):
    input_path, tickets = _load_tickets(input_path)
    output_path = Path(output_path)
    if input_path.resolve() == output_path.resolve():
        raise ValueError("Input and output paths must be different files.")

    references = {}
    if ground_truth_path is not None:
        ground_truth_path = Path(ground_truth_path)
        if output_path.resolve() == ground_truth_path.resolve():
            raise ValueError("Output path must not overwrite the ground-truth file.")
        ground_truth = json.loads(ground_truth_path.read_text(encoding="utf-8"))
        if not isinstance(ground_truth, list):
            raise ValueError("Ground-truth JSON must contain a list of response objects.")
        references = {item["ticket_id"]: item for item in ground_truth}

    results = [
        _evaluate_ticket(ticket, top_k, references.get(ticket["ticket_id"]))
        for ticket in tickets
    ]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_file": input_path.name,
        "metrics": calculate_metrics(results, top_k),
        "segments": calculate_segment_metrics(results),
        "ticket_results": results,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Evaluate support-ticket predictions against a labeled ticket JSON file."
    )
    parser.add_argument("input_path", help="Path to a JSON list of labeled tickets")
    parser.add_argument("output_path", help="Path where the JSON evaluation report is written")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument(
        "--ground-truth",
        help="Optional path to reference responses for must-mention/must-not-claim scoring",
    )
    arguments = parser.parse_args(argv)
    if arguments.top_k < 1:
        parser.error("--top-k must be at least 1")

    report = run_evaluation(
        arguments.input_path,
        arguments.output_path,
        arguments.top_k,
        arguments.ground_truth,
    )
    print(json.dumps(report["metrics"], indent=2))
    print(f"Detailed results: {arguments.output_path}")


if __name__ == "__main__":
    main()
