# Code Structure

## Request flow

`src/api.py` accepts a ticket and coordinates the pipeline:

1. `src/ingest.py` normalizes ticket fields and whitespace.
2. `src/classify.py` predicts intent, urgency, confidence, and entities using the versioned prompt in `prompts/` (or its local rule-based fallback when no API key is configured).
3. `src/retrieve.py` searches the Chroma index built from `data/documentation.json`, returning source IDs, titles, passages, relevance scores and review ages. The embedding store is cached and pre-loaded at API start-up.
4. `src/route.py` decides whether to auto-respond or escalate based on confidence, documentation coverage, and ticket labels.
5. `src/generate.py` drafts a response using only retrieved passages and the versioned response prompt.
6. `src/guardrails.py` blocks detected private data, missing/unknown citations, and a small set of unsupported commitments; suspected instruction overrides are escalated before model calls.
7. `src/logging_store.py` writes a privacy-minimized audit record to `storage/decisions.db` and enforces the daily provider-call budget.
8. `src/monitoring.py` exposes request counters, latency, confidence distribution, and guardrail counters for Prometheus.

## Data and generated files

- `data/` contains the small source and validation datasets. Chroma runtime artifacts accidentally created there are not source datasets.
- `storage/` contains generated local databases and vector indexes; do not commit it.
- `evaluation/results/` contains dated evaluation output; do not commit generated result files.
- `.env` contains local secrets and is ignored by Git. `.env.example` contains placeholders only.

## Evaluation

Run the harness with explicit input and output paths, for example `python -m evaluation.harness data/validation_tickets.json evaluation/results/validation.json`. It sends each ticket through the real API pipeline with labels withheld and reports accuracy, retrieval, citation, protected-ticket, calibration, latency (including p95) and customer-segment metrics, plus a gate table and a run log (`evaluation/results/run_log.jsonl`). Options: `--ground-truth`, `--human-review`, `--simulate-provider-outage`, `--gate`, `--no-warm-up`, `--run-log`. Hallucination, citation-accuracy and satisfaction scores come from `python -m evaluation.review export|score`. The input path is not hardcoded, so the same harness can accept the hidden set at assessment time. Full detail is in [application-architecture.md](application-architecture.md#evaluation).
