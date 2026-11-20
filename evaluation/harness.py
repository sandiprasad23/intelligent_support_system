import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

import requests
from pydantic import ValidationError

from evaluation import metrics as stats
from evaluation.metrics import CITATION_PATTERN
from src import api
from src.config import env_value
from src.classify import CLASSIFY_PROMPT_VERSION, _configured_api_key
from src.generate import GENERATE_PROMPT_VERSION
from src.guardrails import detect_private_data
from src.retrieve import RETRIEVE_PROMPT_VERSION, warm_up_retriever


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN_LOG = PROJECT_ROOT / "evaluation" / "results" / "run_log.jsonl"
DEFAULT_TOP_K = 3  # retrieval depth used by the API pipeline
REQUIRED_TICKET_FIELDS = {"ticket_id", "channel", "subject", "body", "labels"}
REQUIRED_LABEL_FIELDS = {
    "intent",
    "urgency",
    "expected_route",
    "answerable_from_docs",
    "expected_doc_ids",
    "must_not_auto_respond",
}
ESCALATION_CATEGORIES = (
    ("evidence floor", "retrieval_score_below_floor"),
    ("Retrieval relevance", "retrieval_unavailable"),
    ("Vector retrieval", "retrieval_unavailable"),
    ("No relevant documentation", "no_documentation"),
    ("review limit", "stale_documentation"),
    ("kill switch", "kill_switch"),
    ("disabled", "kill_switch"),
    ("Offline keyword", "offline_classification"),
    ("below threshold", "low_confidence"),
    ("Guardrail blocked", "guardrail_blocked"),
    ("generation failed", "provider_failure"),
    ("provider failed", "provider_failure"),
    ("processing failure", "processing_failure"),
    ("override", "instruction_override"),
    ("sensitive-data", "private_data_in_ticket"),
    ("Safety governance", "labeled_must_not_auto_respond"),
    ("requires human review", "intent_requires_human_review"),
    ("Tier 2", "high_risk_intent"),
)


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


def _build_request(ticket):
    # Ground-truth labels are deliberately not passed: they would leak the answer into routing.
    return api.TicketRequest(
        ticket_id=ticket["ticket_id"],
        channel=ticket["channel"],
        subject=ticket["subject"],
        body=ticket["body"],
        customer_tier=ticket.get("customer_tier", "standard"),
        customer_region=ticket.get("customer_region"),
        language_fluency=ticket.get("language_fluency", "fluent"),
    )


def _escalation_category(reason):
    for needle, category in ESCALATION_CATEGORIES:
        if needle in reason:
            return category
    return "other"


def _evaluate_ticket(ticket, reference=None):
    labels = ticket["labels"]
    subject = ticket.get("subject", "")
    body = ticket.get("body", "")
    row = {
        "ticket_id": ticket["ticket_id"],
        "channel": ticket["channel"],
        "customer_tier": ticket.get("customer_tier", "unknown"),
        "customer_region": ticket.get("customer_region", "unknown"),
        "language_fluency": ticket.get("language_fluency", "unknown"),
        "ticket_length": "short" if len(subject) + len(body) <= 500 else "long",
        "subject": subject,
        "body": body,
        "expected_intent": labels["intent"],
        "expected_urgency": labels["urgency"],
        "expected_route": labels["expected_route"],
        "answerable_from_docs": labels["answerable_from_docs"],
        "must_not_auto_respond": labels["must_not_auto_respond"],
        "expected_doc_ids": labels["expected_doc_ids"],
        "reference_available": reference is not None,
    }

    payload, failure = None, None
    started = perf_counter()
    try:
        payload = api.process_ticket_endpoint(_build_request(ticket))
    except ValidationError:
        failure = "input_rejected"
    except Exception as error:
        failure = f"service_error:{type(error).__name__}"
    row["latency_seconds"] = perf_counter() - started

    payload = payload or {}
    response_text = payload.get("response_text") or ""
    guardrails = payload.get("guardrails", {})
    findings = guardrails.get("findings", {}) if isinstance(guardrails, dict) else {}
    violations = list(guardrails.get("violations", [])) if isinstance(guardrails, dict) else []
    for category, found in findings.items():
        violations.extend(f"{category}:{item}" for item in found)

    must_mention = reference.get("must_mention", []) if reference else []
    must_not_claim = reference.get("must_not_claim", []) if reference else []
    response_lower = response_text.lower()
    action = payload.get("action", "escalate")
    reason = payload.get("reason") or failure or "no result"

    row.update(
        {
            "predicted_intent": payload.get("intent", "unclear_request"),
            "predicted_urgency": payload.get("urgency", "high"),
            "confidence": payload.get("confidence", 0.0),
            "predicted_route": action,
            "reason": reason,
            "escalation_category": _escalation_category(reason) if action != "auto_respond" else None,
            "retrieved_doc_ids": payload.get("sources_used", []),
            "predicted_answerable_from_docs": bool(payload.get("sources_used")),
            "response_text": response_text,
            "cited_doc_ids": CITATION_PATTERN.findall(response_text),
            "response_returned": bool(response_text),
            "private_data_in_response": detect_private_data(response_text),
            "must_mention_total": len(must_mention),
            "must_mention_found": sum(term.lower() in response_lower for term in must_mention),
            "must_not_claim_violations": [
                phrase for phrase in must_not_claim if phrase.lower() in response_lower
            ],
            "guardrail_violations": violations,
            "input_rejected": failure == "input_rejected",
            "service_error": failure if failure and failure != "input_rejected" else None,
        }
    )
    return row


def _read_decisions(database_path):
    if not Path(database_path).exists():
        return []

    def _parse(value, default):
        try:
            return json.loads(value) if value else default
        except (TypeError, ValueError):
            return default

    with sqlite3.connect(database_path) as connection:
        records = connection.execute(
            "SELECT ticket_id, stage, stage_results, sources_used FROM decisions"
        ).fetchall()
    return [
        {
            "ticket_id": ticket_id,
            "stage": stage,
            "stage_results": _parse(stage_results, {}),
            "sources": _parse(sources, []),
        }
        for ticket_id, stage, stage_results, sources in records
    ]


def _apply_audit(rows, decisions):
    by_ticket = defaultdict(list)
    for decision in decisions:
        by_ticket[decision["ticket_id"]].append(decision)

    for row in rows:
        entries = by_ticket.get(row["ticket_id"])
        if row["input_rejected"] or not entries:
            row["classification_mode"] = "rejected" if row["input_rejected"] else "unlogged"
            row["classification_ran"] = False
            row["best_retrieval_score"] = None
            row["decision_stage"] = None
            continue
        entry = entries[-1]
        stages = entry["stage_results"] if isinstance(entry["stage_results"], dict) else {}
        mode = stages.get("classification") or "none"
        scores = [
            source["score"]
            for source in entry["sources"]
            if isinstance(source, dict) and source.get("score") is not None
        ]
        row["classification_mode"] = mode
        row["classification_ran"] = mode in ("offline_rules", "provider_completed")
        row["stage_duration_seconds"] = stages.get("duration_seconds", {})
        row["best_retrieval_score"] = max(scores) if scores else None
        row["decision_stage"] = entry["stage"]
        row["provider_failed"] = mode == "provider_failed" or stages.get("generation") == "failed"


def _decision_coverage(rows, decisions):
    expected = Counter(row["ticket_id"] for row in rows if not row["input_rejected"])
    logged = Counter(decision["ticket_id"] for decision in decisions)
    missing = sorted(t for t, count in expected.items() if logged[t] < count)
    duplicated = sorted(t for t, count in expected.items() if logged[t] > count)
    unexpected = sorted(t for t in logged if t not in expected)
    return {
        "tickets_processed": sum(expected.values()),
        "decisions_logged": len(decisions),
        "missing": missing,
        "duplicated": duplicated,
        "unexpected": unexpected,
        "matches": not (missing or duplicated or unexpected),
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


def _row_quality_score(row):
    intent_correct = row["expected_intent"] == row["predicted_intent"]
    urgency_correct = row["expected_urgency"] == row["predicted_urgency"]
    route_correct = row["expected_route"] == row["predicted_route"]
    answerability_correct = row["answerable_from_docs"] == row.get(
        "predicted_answerable_from_docs", bool(row["retrieved_doc_ids"])
    )
    safety_correct = not (
        row["must_not_auto_respond"] and row["predicted_route"] == "auto_respond"
    )
    if row["expected_route"] == "auto_respond" and row["expected_doc_ids"]:
        cited = set(row["cited_doc_ids"])
        citation_score = len(set(row["expected_doc_ids"]) & cited) / len(
            set(row["expected_doc_ids"])
        )
    else:
        citation_score = 1.0
    components = (
        intent_correct,
        urgency_correct,
        route_correct,
        answerability_correct,
        safety_correct,
        citation_score,
    )
    return sum(components) / len(components)


def _resolution_rate(rows):
    eligible = [
        row
        for row in rows
        if row["answerable_from_docs"]
        and row["expected_route"] == "auto_respond"
        and row["expected_doc_ids"]
    ]
    if not eligible:
        return None
    resolved = sum(
        row["predicted_route"] == "auto_respond"
        and row.get("response_returned", True)
        and set(row["expected_doc_ids"]) <= set(row["cited_doc_ids"])
        and not row["guardrail_violations"]
        for row in eligible
    )
    return resolved / len(eligible)


def _is_correct_resolution(row):
    return (
        row["predicted_route"] == "auto_respond"
        and row.get("response_returned", True)
        and row["expected_route"] == "auto_respond"
        and bool(set(row["expected_doc_ids"]) & set(row["cited_doc_ids"]))
        and not row["guardrail_violations"]
    )


def calculate_metrics(rows, top_k=DEFAULT_TOP_K):
    total = len(rows)
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

    protected_rows = [row for row in rows if row["must_not_auto_respond"]]
    reference_rows = [row for row in rows if row.get("reference_available", False)]
    mention_total = sum(row.get("must_mention_total", 0) for row in reference_rows)
    mention_found = sum(row.get("must_mention_found", 0) for row in reference_rows)
    auto_rows = [row for row in rows if row["predicted_route"] == "auto_respond"]
    all_latencies = [row["latency_seconds"] for row in rows]
    auto_latencies = [row["latency_seconds"] for row in auto_rows]

    return {
        "tickets": total,
        "intent_accuracy": _accuracy(rows, "expected_intent", "predicted_intent"),
        "urgency_accuracy": _accuracy(rows, "expected_urgency", "predicted_urgency"),
        "route_accuracy": _accuracy(rows, "expected_route", "predicted_route"),
        "answerability_accuracy": (
            sum(
                row["answerable_from_docs"] == row["predicted_answerable_from_docs"]
                if "predicted_answerable_from_docs" in row
                else row["answerable_from_docs"] == bool(row["retrieved_doc_ids"])
                for row in rows
            ) / total
            if total
            else 0.0
        ),
        "auto_responded_tickets": len(auto_rows),
        "escalated_tickets": total - len(auto_rows),
        "expected_escalated_tickets": sum(row["expected_route"] == "escalate" for row in rows),
        "expected_escalation_rate_pct": (
            100 * sum(row["expected_route"] == "escalate" for row in rows) / total
            if total
            else 0.0
        ),
        "first_contact_resolution_pct": 100 * len(auto_rows) / total if total else 0.0,
        "escalation_rate_pct": 100 * (total - len(auto_rows)) / total if total else 0.0,
        "correct_resolution_pct": (
            100 * sum(_is_correct_resolution(row) for row in rows) / total if total else 0.0
        ),
        "must_not_auto_respond_violations": sum(
            row["predicted_route"] == "auto_respond" for row in protected_rows
        ),
        "protected_tickets": len(protected_rows),
        "answerable_queries": len(answerable_rows),
        f"retrieval_hit_at_{top_k}": (
            hit_count / len(answerable_rows) if answerable_rows else 0.0
        ),
        f"retrieval_recall_at_{top_k}": (
            found_doc_count / expected_doc_count if expected_doc_count else 0.0
        ),
        **_citation_metrics(rows),
        "mean_latency_seconds": sum(all_latencies) / total if total else 0.0,
        "system_latency_seconds": stats.latency_summary(all_latencies),
        "auto_reply_latency_seconds": stats.latency_summary(auto_latencies),
        "guardrail_violation_count": sum(len(row["guardrail_violations"]) for row in rows),
        "private_data_in_returned_responses": sum(
            1 for row in rows if row.get("private_data_in_response")
        ),
        "reference_response_matches": len(reference_rows),
        "must_mention_recall": mention_found / mention_total if mention_total else None,
        "must_not_claim_violation_count": sum(
            len(row.get("must_not_claim_violations", [])) for row in reference_rows
        ),
    }


def calculate_segment_metrics(
    rows,
    segment_fields=(
        "customer_tier",
        "customer_region",
        "language_fluency",
        "ticket_length",
    ),
):
    output = {}
    for field in segment_fields:
        groups = defaultdict(list)
        for row in rows:
            groups[str(row[field])].append(row)
        field_groups = {
            value: {
                "tickets": len(group),
                "resolution_rate": _resolution_rate(group),
                "quality_score": (
                    sum(_row_quality_score(row) for row in group) / len(group)
                ),
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
        best_quality_score = max(
            item["quality_score"] for item in field_groups.values()
        ) if field_groups else 0.0
        for item in field_groups.values():
            item["variation_from_best"] = best_quality_score - item["quality_score"]
            item["explanation"] = (
                "Small sample; interpret cautiously."
                if item["tickets"] < 20
                else "Quality score is a proxy combining intent, urgency, route, answerability, safety, and citation checks."
            )
        output[field] = field_groups
    return output


def _retrieval_score_summary(rows):
    floor = float(os.getenv("MIN_RETRIEVAL_SCORE", "0.30"))
    scores = [
        row["best_retrieval_score"]
        for row in rows
        if row["answerable_from_docs"] and row.get("best_retrieval_score") is not None
    ]
    return {
        "score_floor": floor,
        "answerable_tickets_with_scores": len(scores),
        "p10": stats.percentile(scores, 0.10),
        "median": stats.percentile(scores, 0.50),
        "p90": stats.percentile(scores, 0.90),
        "share_below_floor": (
            sum(score < floor for score in scores) / len(scores) if scores else None
        ),
    }


def _git_info():
    def _run(*arguments):
        return subprocess.run(
            ["git", *arguments], cwd=PROJECT_ROOT, capture_output=True, text=True,
            timeout=10, check=True,
        ).stdout.strip()

    try:
        return {
            "commit": _run("rev-parse", "--short", "HEAD"),
            "uncommitted_changes_in_system_code": bool(
                _run("status", "--porcelain", "--untracked-files=no", "--",
                     "src", "prompts", "evaluation", "requirements.txt")
            ),
        }
    except Exception:
        return {"commit": "unknown", "uncommitted_changes_in_system_code": None}


def _previous_runs(run_log_path, input_sha):
    run_log_path = Path(run_log_path)
    if not run_log_path.exists():
        return 0
    count = 0
    for line in run_log_path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            count += entry.get("input_sha256") == input_sha
    return count


@contextmanager
def _evaluation_environment(decision_db, simulate_outage):
    overrides = {
        "DATABASE_URL": f"sqlite:///{Path(decision_db).resolve()}",
        "LLM_USAGE_DATABASE_URL": env_value("DATABASE_URL"),
    }
    if simulate_outage:
        overrides["OPENROUTER_API_KEY"] = "simulated-outage-key"
    previous = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)
    try:
        with ExitStack() as stack:
            if simulate_outage:
                stack.enter_context(
                    patch("requests.post", side_effect=requests.ConnectionError("simulated provider outage"))
                )
            yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _interval_text(successes, total):
    low, high = stats.wilson_interval(successes, total)
    return f"95% interval {low * 100:.0f}-{high * 100:.0f}% (n={total})"


def _status(value, target, higher_is_better=True):
    if value is None:
        return "not_measured"
    return "pass" if (value >= target if higher_is_better else value <= target) else "fail"


def _build_results_table(metrics, classification, calibration, variation, coverage, review, service):
    total = metrics["tickets"]
    auto_stats = metrics["auto_reply_latency_seconds"]
    all_stats = metrics["system_latency_seconds"]
    row = lambda measure, tier, baseline, target, achieved, status, confidence, notes="": {  # noqa: E731
        "measure": measure, "tier": tier, "baseline": baseline, "target": target,
        "achieved": achieved, "status": status, "confidence_in_figure": confidence, "notes": notes,
    }
    needs_review = "needs_human_review"
    review_size = review.get("responses_reviewed", 0) if review else 0
    review_ok = bool(review) and review.get("minimum_sample_met", False)

    top_reasons = ", ".join(
        f"{name} ({count})" for name, count in service["escalation_reason_counts"].most_common(3)
    ) or "none"
    below = classification["classes_below_precision_target"]
    weighted = classification["weighted_precision"]
    gap = variation["max_gap_points"]
    privacy_count = metrics["private_data_in_returned_responses"]
    returned = metrics["auto_responded_tickets"]

    table = [
        row("First contact resolution", "business", "42%", "60% or better",
            f"{metrics['first_contact_resolution_pct']:.1f}%",
            _status(metrics["first_contact_resolution_pct"], 60),
            _interval_text(metrics["auto_responded_tickets"], total),
            f"Proxy: a returned automatic response counts as closed. Responses citing an expected "
            f"document: {metrics['correct_resolution_pct']:.1f}%."),
        row("Mean time to first reply", "business", "8 to 12 hours", "Under 5 minutes",
            (f"mean {auto_stats['mean']:.1f} s, median {auto_stats['median']:.1f} s "
             f"({auto_stats['count']} auto-replied tickets)") if auto_stats["count"] else "no automatic replies",
            _status(auto_stats["mean"] / 60 if auto_stats["count"] else None, 5, higher_is_better=False),
            "Auto-replied tickets only; escalated tickets wait for a human and are not measured here.",
            f"p95 {auto_stats['p95']:.1f} s." if auto_stats["count"] else ""),
        row("Customer satisfaction proxy", "business", "3.2 / 5", "4.0 or better",
            f"{review['satisfaction_mean']:.2f} / 5" if review and review.get("satisfaction_mean") is not None else "not scored",
            _status(review.get("satisfaction_mean"), 4.0) if review_ok else needs_review,
            f"{review_size} responses, two assessors" if review else "no human review supplied",
            "Human rubric score; dataset csat_rating is the human baseline, not this system."),
        row("Escalation rate", "business", "58%", "30% or lower",
            f"{metrics['escalation_rate_pct']:.1f}%",
            _status(metrics["escalation_rate_pct"], 30, higher_is_better=False),
            _interval_text(metrics["escalated_tickets"], total),
            (
                f"Dataset labels expect {metrics['expected_escalation_rate_pct']:.1f}% escalation, "
                "which exceeds the system target; reconcile target and evaluation population. "
                if metrics["expected_escalation_rate_pct"] > 30
                else ""
            ) + f"Top reasons: {top_reasons}."),
        row("Repeat contacts", "business", "Not measured", "Halved", "not measurable",
            "not_measurable", "n/a",
            "Needs live customers over time; dataset history.repeat_contact reflects human handling."),
        row("Classification precision (weighted)", "technical", "-", "85% or better",
            f"{weighted * 100:.1f}%" if weighted is not None else "n/a",
            _status(weighted, 0.85),
            f"{classification['samples']} classified tickets; small per-class support",
            f"Classes below 85%: {', '.join(below[:8]) or 'none'}."),
        row("Hallucination rate", "technical", "-", "5% or lower",
            f"{review['hallucination_rate_pct']:.1f}%" if review and review.get("hallucination_rate_pct") is not None else "not scored",
            _status(review.get("hallucination_rate_pct"), 5, higher_is_better=False) if review_ok else needs_review,
            (f"{review_size} responses; assessor agreement {review.get('hallucination_agreement_pct', 0):.0f}%" if review else "no human review supplied"),
            "Needs at least 50 responses and two assessors."),
        row("Citation accuracy", "technical", "-", "95% or better",
            f"{review['citation_accuracy_pct']:.1f}%" if review and review.get("citation_accuracy_pct") is not None else "not scored",
            _status(review.get("citation_accuracy_pct"), 95) if review and review.get("citations_reviewed", 0) >= 1 else needs_review,
            f"{review.get('citations_reviewed', 0)} citations" if review else "no human review supplied",
            f"Automated proxy (right document cited, not sentence support): precision {metrics['citation_precision']:.2f}."),
        row("Latency, 95th percentile", "technical", "-", "Under 3 seconds",
            f"{all_stats['p95']:.2f} s (median {all_stats['median']:.2f} s, mean {all_stats['mean']:.2f} s)" if all_stats["count"] else "n/a",
            _status(all_stats["p95"], 3, higher_is_better=False),
            f"{all_stats['count']} tickets, in-process, includes retrieval and model calls",
            f"Embedding model warm-up took {service['warm_up_seconds']:.1f} s before timing." if service["warm_up_seconds"] is not None else "No warm-up performed."),
        row("Availability", "technical", "-", "99.5% or better",
            f"{service['availability_pct']:.2f}%",
            _status(service["availability_pct"], 99.5),
            f"{service['tickets_processed']} tickets; {service['provider_failures']} provider failures handled by escalation",
            "Provider-failure behaviour was simulated in this run." if service["simulated_outage"]
            else "Provider failure not exercised; rerun with --simulate-provider-outage."),
        row("Private data in outbound text", "governance", "-", "Zero occurrences",
            f"{privacy_count} of {returned} returned responses",
            "pass" if privacy_count == 0 else "fail",
            f"Rule of three: true rate under {3 / returned * 100:.1f}% at 95%" if returned and privacy_count == 0 else "n/a",
            "Automated pattern scan only; manually review a sample too."),
        row("Quality across customer groups", "governance", "-", "Under 5 points",
            f"{gap:.1f} points" if gap is not None else "n/a",
            _status(gap, 5, higher_is_better=False) if gap is not None else "not_measured",
            "Reliable" if variation["reliable"] else "Some segments have fewer than 20 tickets",
            "; ".join(f"{field}: {item['best_group']} vs {item['worst_group']} {item['gap_points']:.1f}"
                      for field, item in variation["fields"].items())),
        row("Decision logging", "governance", "-", "Complete coverage",
            f"{coverage['decisions_logged']} logged / {coverage['tickets_processed']} processed",
            "pass" if coverage["matches"] else "fail", "Exact count reconciliation",
            f"Missing {len(coverage['missing'])}, duplicated {len(coverage['duplicated'])}, unexpected {len(coverage['unexpected'])}."),
        row("Confidence calibration", "governance", "-", "Within 5 points",
            f"max gap {calibration['max_abs_gap_points']:.1f} points" if calibration["max_abs_gap_points"] is not None else "n/a",
            {True: "pass", False: "fail", None: "not_measured"}[calibration["passes"]],
            f"{calibration['samples']} predictions; bands under 10 tickets are ignored",
            "Offline rule-based confidence is constant and excluded."),
        row("Protected-ticket auto-responses", "governance", "-", "Zero",
            f"{metrics['must_not_auto_respond_violations']}",
            "pass" if metrics["must_not_auto_respond_violations"] == 0 else "fail",
            f"{metrics['protected_tickets']} labelled must-not-auto-respond tickets",
            "Labels are withheld from the system, so this is a genuine test."),
    ]
    return table


def _caution_statement(manifest, metrics, variation, review, validity, previous_runs):
    reasons = [
        f"it covers only {metrics['tickets']} tickets, so single percentages carry wide uncertainty",
        "first contact resolution and the quality score are automated proxies rather than measured customer outcomes",
        "time to first reply excludes escalated tickets, which wait for a human",
        "private-data and injection checks are pattern-based and miss names and unusual formats",
    ]
    if not review:
        reasons.append("no human review was supplied, so hallucination rate, citation accuracy and satisfaction are unmeasured")
    if not variation["reliable"]:
        reasons.append("some customer segments contain fewer than 20 tickets")
    if previous_runs:
        reasons.append(f"this dataset had already been evaluated {previous_runs} time(s), which can overfit decisions to it")
    reasons.extend(validity["reasons"])
    return "The figures above should be treated with caution because " + "; ".join(reasons) + "."


def run_evaluation(
    input_path,
    output_path,
    ground_truth_path=None,
    human_review_path=None,
    simulate_provider_outage=False,
    warm_up=True,
    run_log_path=DEFAULT_RUN_LOG,
):
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

    review = None
    if human_review_path is not None:
        review = json.loads(Path(human_review_path).read_text(encoding="utf-8"))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    decision_db = output_path.with_suffix(".decisions.db").resolve()
    decision_db.unlink(missing_ok=True)
    input_sha = hashlib.sha256(input_path.read_bytes()).hexdigest()
    previous_runs = _previous_runs(run_log_path, input_sha)
    provider_mode = "provider" if (_configured_api_key() or simulate_provider_outage) else "offline_rules"
    kill_switch_active = api.auto_response_disabled()

    started_at = datetime.now(timezone.utc)
    with _evaluation_environment(decision_db, simulate_provider_outage):
        warm_up_seconds = warm_up_retriever() if warm_up else None
        rows = [
            _evaluate_ticket(ticket, references.get(ticket["ticket_id"])) for ticket in tickets
        ]

    decisions = _read_decisions(decision_db)
    _apply_audit(rows, decisions)
    coverage = _decision_coverage(rows, decisions)

    metrics = calculate_metrics(rows)
    segments = calculate_segment_metrics(rows)
    variation = stats.cross_group_variation(segments)
    classified = [row for row in rows if row.get("classification_ran")]
    classification = stats.classification_summary(
        [row["expected_intent"] for row in classified],
        [row["predicted_intent"] for row in classified],
    )
    calibration = stats.calibration_table(
        [
            (min(max(row["confidence"], 0.0), 1.0), row["expected_intent"] == row["predicted_intent"])
            for row in rows
            if row.get("classification_mode") == "provider_completed"
        ]
    )

    processed = [row for row in rows if not row["input_rejected"]]
    service_errors = sum(
        1 for row in processed if row["service_error"] or row.get("decision_stage") == "pipeline_failure"
    )
    service = {
        "tickets_processed": len(processed),
        "rejected_inputs": len(rows) - len(processed),
        "service_errors": service_errors,
        "provider_failures": sum(1 for row in rows if row.get("provider_failed")),
        "availability_pct": 100 * (len(processed) - service_errors) / len(processed) if processed else 0.0,
        "simulated_outage": simulate_provider_outage,
        "warm_up_seconds": warm_up_seconds,
        "stage_latency_seconds": {
            stage: stats.latency_summary([
                row["stage_duration_seconds"][stage]
                for row in rows
                if stage in row.get("stage_duration_seconds", {})
            ])
            for stage in ("classification", "retrieval", "generation")
        },
        "escalation_reason_counts": Counter(
            row["escalation_category"] for row in rows if row["escalation_category"]
        ),
    }

    invalid_reasons = []
    if provider_mode == "offline_rules":
        invalid_reasons.append("no provider API key was configured, so offline keyword classification (which always escalates) was used")
    if simulate_provider_outage:
        invalid_reasons.append("the model provider was deliberately failed for this run")
    if kill_switch_active:
        invalid_reasons.append("the auto-response kill switch was active")
    validity = {"valid_for_gate": not invalid_reasons, "reasons": invalid_reasons}

    results_table = _build_results_table(
        metrics, classification, calibration, variation, coverage, review, service
    )
    gate_failures = [item["measure"] for item in results_table if item["status"] == "fail"]
    manifest = {
        "run_at": started_at.isoformat(),
        "input_file": input_path.name,
        "input_sha256": input_sha,
        "tickets": len(tickets),
        "previous_runs_on_this_input": previous_runs,
        "provider_mode": provider_mode,
        "model_name": env_value("MODEL_NAME"),
        "prompt_versions": {
            "classify": CLASSIFY_PROMPT_VERSION,
            "retrieve": RETRIEVE_PROMPT_VERSION,
            "generate": GENERATE_PROMPT_VERSION,
        },
        "thresholds": {
            "confidence": env_value("CONFIDENCE_THRESHOLD"),
            "min_retrieval_score": os.getenv("MIN_RETRIEVAL_SCORE", "0.30"),
            "max_doc_age_days": os.getenv("MAX_DOC_AGE_DAYS", "180"),
        },
        "retrieval_depth": DEFAULT_TOP_K,
        "system_version": _git_info(),
        "human_review_supplied": review is not None,
    }
    report = {
        "generated_at": started_at.isoformat(),
        "input_file": input_path.name,
        "manifest": manifest,
        "validity": validity,
        "gate": {"passed": validity["valid_for_gate"] and not gate_failures, "failed_measures": gate_failures},
        "results_table": results_table,
        "caution_statement": _caution_statement(manifest, metrics, variation, review, validity, previous_runs),
        "metrics": metrics,
        "segments": segments,
        "cross_group_variation": variation,
        "classification": classification,
        "calibration": calibration,
        "decision_log": coverage,
        "service": {**service, "escalation_reason_counts": dict(service["escalation_reason_counts"])},
        "retrieval_scores": _retrieval_score_summary(rows),
        "ticket_results": rows,
    }
    output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    run_log_path = Path(run_log_path)
    run_log_path.parent.mkdir(parents=True, exist_ok=True)
    with run_log_path.open("a", encoding="utf-8") as log:
        log.write(json.dumps({
            "run_at": manifest["run_at"], "input_file": manifest["input_file"],
            "input_sha256": input_sha, "tickets": manifest["tickets"],
            "system_version": manifest["system_version"], "provider_mode": provider_mode,
            "simulated_outage": simulate_provider_outage,
            "valid_for_gate": validity["valid_for_gate"], "gate_passed": report["gate"]["passed"],
        }) + "\n")
    return report


def format_results_table(table):
    headers = ("Measure", "Baseline", "Target", "Achieved", "Status")
    rows = [(item["measure"], item["baseline"], item["target"], item["achieved"], item["status"]) for item in table]
    widths = [max(len(str(value)) for value in column) for column in zip(headers, *rows)]
    lines = ["  ".join(str(value).ljust(width) for value, width in zip(headers, widths))]
    lines.append("  ".join("-" * width for width in widths))
    lines.extend("  ".join(str(value).ljust(width) for value, width in zip(row, widths)) for row in rows)
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Evaluate the support pipeline against a labeled ticket JSON file."
    )
    parser.add_argument("input_path", help="Path to a JSON list of labeled tickets")
    parser.add_argument("output_path", help="Path where the JSON evaluation report is written")
    parser.add_argument("--ground-truth", help="Optional reference responses for must-mention checks")
    parser.add_argument("--human-review", help="review_summary.json produced by evaluation.review score")
    parser.add_argument("--simulate-provider-outage", action="store_true",
                        help="Fail every model call to test fail-closed behaviour and availability")
    parser.add_argument("--gate", action="store_true",
                        help="Exit non-zero if any measure fails or the run is not valid for the gate")
    parser.add_argument("--no-warm-up", action="store_true", help="Skip loading the embedding model first")
    parser.add_argument("--run-log", default=str(DEFAULT_RUN_LOG), help="Append-only run log path")
    arguments = parser.parse_args(argv)

    report = run_evaluation(
        arguments.input_path,
        arguments.output_path,
        ground_truth_path=arguments.ground_truth,
        human_review_path=arguments.human_review,
        simulate_provider_outage=arguments.simulate_provider_outage,
        warm_up=not arguments.no_warm_up,
        run_log_path=arguments.run_log,
    )
    manifest = report["manifest"]
    print(f"Evaluated {manifest['tickets']} tickets from {manifest['input_file']} "
          f"(system {manifest['system_version']['commit']}, run #{manifest['previous_runs_on_this_input'] + 1} on this input)")
    print(format_results_table(report["results_table"]))
    print()
    print(report["caution_statement"])
    if not report["validity"]["valid_for_gate"]:
        print("\nINVALID FOR GATE: " + "; ".join(report["validity"]["reasons"]))
    print(f"\nDetailed results: {arguments.output_path}")
    if arguments.gate and not report["gate"]["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
