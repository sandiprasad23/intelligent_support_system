# Architecture

## Request flow

`src/api.py` accepts a ticket and coordinates the pipeline:

1. `src/ingest.py` normalizes ticket fields and whitespace.
2. `src/classify.py` predicts intent, urgency, confidence, and entities using the versioned prompt in `prompts/` (or its local rule-based fallback when no API key is configured).
3. `src/retrieve.py` searches the Chroma index built from `data/documentation.json`, returning source IDs, titles, and passages.
4. `src/route.py` decides whether to auto-respond or escalate based on confidence, documentation coverage, and ticket labels.
5. `src/generate.py` drafts a response using only retrieved passages and the versioned response prompt.
6. `src/guardrails.py` checks for exposed secrets and citation presence before returning a response.
7. `src/logging_store.py` writes the decision to `storage/decisions.db`.
8. `src/monitoring.py` exposes request counters, latency, and guardrail counters for Prometheus.

## Data and generated files

- `data/` contains the small source and validation datasets. Chroma runtime artifacts accidentally created there are not source datasets.
- `storage/` contains generated local databases and vector indexes; do not commit it.
- `evaluation/results/` contains dated evaluation output; do not commit generated result files.
- `.env` contains local secrets and is ignored by Git. `.env.example` contains placeholders only.

## Evaluation

Run the harness with explicit input and output paths, for example `python -m evaluation.harness data/validation_tickets.json evaluation/results/validation.json`. It evaluates the full ticket pipeline and reports intent, urgency, route, and answerability accuracy, retrieval, citation, protected-ticket, latency, and customer-segment metrics. An optional `--ground-truth` path enables must-mention and must-not-claim checks for matching ticket IDs. The input path is not hardcoded, so the same harness can accept the hidden set at assessment time.
