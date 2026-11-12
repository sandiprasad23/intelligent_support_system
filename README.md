# Support System

A Python 3.11 support-ticket assistant project. Runtime modules live in `src/`, versioned prompts in `prompts/`, evaluation tools and dated results in `evaluation/`, architecture notes in `docs/`, source datasets in `data/`, and generated databases/vector indexes in `storage/`.

Governance controls, risk register, decision-log fields, fairness methodology, incident steps, and the auto-response kill switch are documented in [docs/governance.md](docs/governance.md). [docs/application-architecture.md](docs/application-architecture.md) is the complete reference covering the pipeline, modules, governance, monitoring, evaluation and limitations.

## Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with the values needed for your local environment. Never commit `.env` or API keys.

Chat tickets may have a blank `subject`; `ticket_id` and `body` are required. The API pre-loads the embedding model at start-up, so the first request is not slow.

The example leaves `OPENROUTER_API_KEY` empty. In that mode, keyword classification is for local development and always escalates; it will not auto-reply. Provider-enabled requests are limited by `MAX_LLM_CALLS_PER_DAY`; automatic routing requires confidence >= `CONFIDENCE_THRESHOLD`, a source score >= `MIN_RETRIEVAL_SCORE`, and documentation within `MAX_DOC_AGE_DAYS`.

## Current scripts

Initialize the SQLite decision log:

```bash
python init_db.py
```

Build the documentation vector index:

```bash
python data-chunking.py
```

Expose Prometheus metrics locally:

```bash
python monitoring.py
```

The metrics endpoint is `http://localhost:8001/metrics`. In a second terminal, start Prometheus from the project root using `prometheus.yml`. On machines whose hostname causes URL parsing errors, run:

```bash
prometheus --config.file=prometheus.yml --web.listen-address=127.0.0.1:9090 --web.external-url=http://localhost:9090/
```

Run the API instead of the standalone metrics process to expose metrics from API requests:

```bash
python -m src.api
```

The API is available at `http://localhost:8000`; its metrics endpoint is `http://localhost:8001/metrics`. Stop any separately running `monitoring.py` process first, or set `METRICS_PORT` to a free port and update `prometheus.yml` to scrape that port. `API_PORT` can change the API port if 8000 is occupied.

To immediately disable automatic customer responses without redeploying, create `storage/AUTO_RESPONSE_DISABLED`; remove that file only after review to re-enable. The operator procedure and limitations are in [docs/governance.md](docs/governance.md).

## Tests

```bash
python -m pytest tests/ -v
```

GitHub Actions runs this offline test suite on pushes and pull requests. Model API calls are not part of CI.

## Evaluation

Build the vector index first. The harness requires an input ticket file and an output report path, so the same command can accept the hidden set without code changes:

```bash
python -m evaluation.harness data/validation_tickets.json evaluation/results/validation.json
```

For tickets that have matching records in `ground_truth_responses.json`, include the optional reference file to score required mentions and prohibited claims:

```bash
python -m evaluation.harness data/development_tickets.json evaluation/results/development.json --ground-truth data/ground_truth_responses.json
```

The report includes intent, urgency, route, and answerability accuracy; retrieval Hit@K and Recall@K; citation precision and recall; protected-ticket violations; slices by tier, region, language fluency, and short/long ticket; per-class precision with a confusion matrix; confidence calibration bands; p95 latency; and a results table with baseline, target, achieved value and status for each framework measure.

How the run works:

- Every ticket goes through the real API pipeline (`process_ticket_endpoint`) with labels withheld, so routing, guardrails, the kill switch and the audit log behave as in production. The decision log written during the run (`<output>.decisions.db`) is reconciled against the tickets processed.
- A run using the offline fallback, a simulated outage or an active kill switch is marked `valid_for_gate: false`. A real gate run needs a configured `OPENROUTER_API_KEY`.
- Each run is appended to `evaluation/results/run_log.jsonl` (override with `--run-log`) so results can be compared with earlier runs of the same input.
- `--gate` returns exit code 1 when a measured target is missed or the run is invalid. `--simulate-provider-outage` checks that every ticket escalates when the provider fails. `--no-warm-up` skips the embedding model pre-load.

Hallucination rate, citation accuracy and customer satisfaction need human judgement. Export a seeded sample (default 50 responses) for two assessors, then score the completed sheets and pass the summary back to the harness:

```bash
python -m evaluation.harness data/validation_tickets.json evaluation/results/validation.json
python -m evaluation.review export evaluation/results/validation.json evaluation/results/review
# assessor A fills the a_* columns and assessor B the b_* columns, independently
python -m evaluation.review score evaluation/results/review
python -m evaluation.harness data/validation_tickets.json evaluation/results/validation.json --human-review evaluation/results/review/review_summary.json
```

Repeat contacts cannot be measured offline because the datasets do not link tickets from the same customer. For assessment, pass the hidden dataset path as the first argument and the desired JSON report path as the second. See [docs/governance.md](docs/governance.md) for metric definitions and review guidance.
