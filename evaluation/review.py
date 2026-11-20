import argparse
import csv
import json
import random
import re
import sys
from pathlib import Path

from evaluation.metrics import CITATION_PATTERN

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESPONSES_FILE = "review_responses.csv"
CITATIONS_FILE = "review_citations.csv"
SUMMARY_FILE = "review_summary.json"
MINIMUM_RESPONSES = 50

RESPONSE_COLUMNS = [
    "ticket_id", "subject", "body", "response_text", "sources",
    "a_unsupported_claim", "b_unsupported_claim",
    "a_satisfaction_1to5", "b_satisfaction_1to5", "a_notes", "b_notes",
]
CITATION_COLUMNS = [
    "ticket_id", "sentence", "cited_doc_id", "source_text", "a_supports", "b_supports",
]


def citation_items(response_text):
    """Pair each cited document with the sentence it is attached to."""
    items = []
    previous_sentence = ""
    for segment in re.split(r"(?<=[.!?])\s+|\n+", response_text):
        cited = list(dict.fromkeys(CITATION_PATTERN.findall(segment)))
        sentence = CITATION_PATTERN.sub("", segment).strip()
        if sentence:
            previous_sentence = sentence
        items.extend(
            (sentence or previous_sentence, doc_id.strip())
            for doc_id in cited
            if sentence or previous_sentence
        )
    return items


def export_review(report_path, output_dir, sample_size=MINIMUM_RESPONSES, seed=2026,
                  documentation_path=None):
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    candidates = [row for row in report["ticket_results"] if row.get("response_returned")]
    if not candidates:
        raise ValueError("The report contains no automatically returned responses to review.")

    documentation_path = Path(documentation_path or PROJECT_ROOT / "data" / "documentation.json")
    articles = {
        item["doc_id"]: item["content"]
        for item in json.loads(documentation_path.read_text(encoding="utf-8"))
    }
    sample = random.Random(seed).sample(candidates, min(sample_size, len(candidates)))

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / RESPONSES_FILE).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESPONSE_COLUMNS)
        writer.writeheader()
        for row in sample:
            writer.writerow({
                "ticket_id": row["ticket_id"], "subject": row["subject"], "body": row["body"],
                "response_text": row["response_text"], "sources": ", ".join(row["retrieved_doc_ids"]),
            })
    citation_rows = [
        {"ticket_id": row["ticket_id"], "sentence": sentence, "cited_doc_id": doc_id,
         "source_text": articles.get(doc_id, "(document not found)")}
        for row in sample
        for sentence, doc_id in citation_items(row["response_text"])
    ]
    with (output_dir / CITATIONS_FILE).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CITATION_COLUMNS)
        writer.writeheader()
        writer.writerows(citation_rows)
    return {"responses": len(sample), "citations": len(citation_rows),
            "meets_minimum": len(sample) >= MINIMUM_RESPONSES, "directory": str(output_dir)}


def _yes_no(value):
    value = (value or "").strip().lower()
    if value in ("y", "yes", "true", "1"):
        return True
    if value in ("n", "no", "false", "0"):
        return False
    return None


def _score(value):
    try:
        score = int((value or "").strip())
    except ValueError:
        return None
    return score if 1 <= score <= 5 else None


def cohen_kappa(first, second):
    """Agreement between two yes/no raters corrected for chance; None if undefined."""
    total = len(first)
    if not total:
        return None
    observed = sum(a == b for a, b in zip(first, second)) / total
    yes_a, yes_b = sum(first) / total, sum(second) / total
    expected = yes_a * yes_b + (1 - yes_a) * (1 - yes_b)
    return None if expected == 1 else (observed - expected) / (1 - expected)


def _rate_pairs(pairs):
    return [a for a, _ in pairs], [b for _, b in pairs]


def score_review(directory):
    directory = Path(directory)
    with (directory / RESPONSES_FILE).open(newline="", encoding="utf-8") as handle:
        responses = list(csv.DictReader(handle))
    with (directory / CITATIONS_FILE).open(newline="", encoding="utf-8") as handle:
        citations = list(csv.DictReader(handle))

    claims = [(_yes_no(r["a_unsupported_claim"]), _yes_no(r["b_unsupported_claim"])) for r in responses]
    claims = [pair for pair in claims if None not in pair]
    scores = [(_score(r["a_satisfaction_1to5"]), _score(r["b_satisfaction_1to5"])) for r in responses]
    scores = [pair for pair in scores if None not in pair]
    supports = [(_yes_no(c["a_supports"]), _yes_no(c["b_supports"])) for c in citations]
    supports = [pair for pair in supports if None not in pair]

    def share(values):
        return 100 * sum(values) / len(values) if values else None

    claim_a, claim_b = _rate_pairs(claims)
    support_a, support_b = _rate_pairs(supports)
    summary = {
        "responses_reviewed": len(claims),
        "minimum_sample_met": len(claims) >= MINIMUM_RESPONSES,
        "hallucination_rate_pct": share([a or b for a, b in claims]),
        "hallucination_rate_by_assessor_pct": {"a": share(claim_a), "b": share(claim_b)},
        "hallucination_agreement_pct": share([a == b for a, b in claims]),
        "hallucination_kappa": cohen_kappa(claim_a, claim_b),
        "citations_reviewed": len(supports),
        "citation_accuracy_pct": share([a and b for a, b in supports]),
        "citation_accuracy_by_assessor_pct": {"a": share(support_a), "b": share(support_b)},
        "citation_agreement_pct": share([a == b for a, b in supports]),
        "citation_kappa": cohen_kappa(support_a, support_b),
        "satisfaction_responses": len(scores),
        "satisfaction_mean": (
            sum(a + b for a, b in scores) / (2 * len(scores)) if scores else None
        ),
        "satisfaction_by_assessor": {
            "a": sum(a for a, _ in scores) / len(scores) if scores else None,
            "b": sum(b for _, b in scores) / len(scores) if scores else None,
        },
        "satisfaction_within_one_point_pct": share([abs(a - b) <= 1 for a, b in scores]),
        "method": (
            "Hallucination = either assessor flagged an unsupported claim. Citation accuracy = both "
            "assessors judged the cited source supports the sentence. Satisfaction = mean of both "
            "assessors' 1-5 rubric scores."
        ),
    }
    (directory / SUMMARY_FILE).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="Human review sample for hallucination, citation and satisfaction scoring.")
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="Write review sheets for two assessors")
    export.add_argument("report_path")
    export.add_argument("output_dir")
    export.add_argument("--sample-size", type=int, default=MINIMUM_RESPONSES)
    export.add_argument("--seed", type=int, default=2026)
    score = commands.add_parser("score", help="Score the completed sheets")
    score.add_argument("output_dir")
    arguments = parser.parse_args(argv)

    if arguments.command == "export":
        result = export_review(arguments.report_path, arguments.output_dir,
                               arguments.sample_size, arguments.seed)
        print(f"Wrote {result['responses']} responses and {result['citations']} citations to {result['directory']}.")
        if not result["meets_minimum"]:
            print(f"Warning: fewer than {MINIMUM_RESPONSES} responses are available; the hallucination measure will be marked insufficient.")
        print("Assessor A fills the a_* columns and assessor B the b_* columns, independently.")
        print("Use yes/no for unsupported_claim and supports, and 1-5 for satisfaction. Then run: python -m evaluation.review score " + result["directory"])
        return 0
    summary = score_review(arguments.output_dir)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
