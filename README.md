# Support System

A Python 3.11 support-ticket assistant project. Runtime modules live in `src/`, versioned prompts in `prompts/`, evaluation tools and dated results in `evaluation/`, architecture notes in `docs/`, source datasets in `data/`, and generated databases/vector indexes in `storage/`.

Governance controls, risk register, decision-log fields, fairness methodology, incident steps, and the auto-response kill switch are documented in [docs/governance.md](docs/governance.md). [docs/application-architecture.md](docs/application-architecture.md) is the complete reference covering the pipeline, modules, governance, monitoring, evaluation and limitations.

## Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
test -e .env || cp .env.example .env
```

Create `.env` from `.env.example` only if `.env` does not already exist; do not overwrite an existing file that may contain credentials. Edit `.env` with the values needed for your local environment. Never commit `.env` or API keys.

Chat tickets may have a blank `subject`; `ticket_id` and `body` are required. The API pre-loads the embedding model at start-up, avoiding embedding initialization on the first request. Provider-backed classification and generation still add network/model latency to requests.

The example leaves `OPENROUTER_API_KEY` empty. In that mode, keyword classification is for local development and always escalates; it will not auto-reply. Provider-enabled requests are limited by `MAX_LLM_CALLS_PER_DAY`; passing the confidence, retrieval-score and document-age thresholds is necessary but not sufficient for an automatic response. The router also escalates high-risk intents, explicit feature requests, offline/provider failures, unsafe or stale evidence, and other governance conditions. Calls to the configured provider may incur cost.

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
python -m evaluation.harness data/validation_tickets.json evaluation/results/validation_run01.json --gate
```

Use a new output path for each run. The matching `<output>.decisions.db` is deleted and rebuilt when that path is evaluated again. The run log defaults to `evaluation/results/run_log.jsonl`; set `--run-log` to use a separate log. A labelled input must contain the ticket and label fields required by the harness. For a real provider-backed gate run, configure `OPENROUTER_API_KEY` in `.env`; evaluation makes model calls and may incur cost or hit provider rate limits.

For tickets that have matching records in `ground_truth_responses.json`, include the optional reference file to score literal required mentions and prohibited claims. These checks do not semantically compare a response with the full reference answer:

```bash
python -m evaluation.harness data/development_tickets.json evaluation/results/development_run01.json --ground-truth data/ground_truth_responses.json --gate
```

Without `--gate`, the command prints the gate results but exits successfully even if a target failed. With `--gate`, exit code 1 means at least one measured target failed or the run was invalid. Check both `gate.passed` and `validity.valid_for_gate` in the JSON report. A simulated provider outage is a resilience test, not a valid performance-gate run:

```bash
python -m evaluation.harness data/validation_tickets.json evaluation/results/outage_check.json --simulate-provider-outage --run-log evaluation/results/outage_run_log.jsonl
```

The report includes intent, urgency, route, and answerability accuracy; retrieval Hit@K and Recall@K; automated citation-ID precision and recall against expected document IDs; protected-ticket violations; slices by tier, region, language fluency, and ticket length; per-class precision with a confusion matrix; confidence calibration bands; end-to-end p95 latency; per-stage timing; and a results table with baseline, target, achieved value and status. Citation-ID metrics do not establish that a source semantically supports each response sentence. The “first contact resolution” measure is a proxy based on automatic responses, not a measured customer resolution.

How the run works:

- Every ticket goes through the real API processing pipeline (`process_ticket_endpoint`) with ground-truth labels withheld. The run uses the same routing and guardrail code, but calls the endpoint in-process rather than exercising HTTP/network behavior. The per-run decision database (`<output>.decisions.db`) is reconciled against processed tickets.
- A run using the offline fallback, a simulated outage or an active kill switch is marked `valid_for_gate: false`. A real gate run needs a configured `OPENROUTER_API_KEY`.
- Each run is appended to `evaluation/results/run_log.jsonl` (override with `--run-log`) so results can be compared with earlier runs of the same input.
- `--gate` returns exit code 1 when a measured target is missed or the run is invalid. `--simulate-provider-outage` checks that every ticket escalates when the provider fails. `--no-warm-up` skips the embedding model pre-load.
- `service.stage_latency_seconds` summarizes classification, retrieval, and generation durations separately for stages that ran. Overall ticket latency remains the end-to-end in-process measurement; stage percentiles cannot be added to obtain the overall percentile.
- The saved validation data labels 40% of tickets for escalation, while the configured escalation target is 30% or lower. The report calls out this target/population conflict; do not force the rate lower by overriding safety labels.

Hallucination rate, semantic citation accuracy and satisfaction need human judgement. Export a seeded sample (default 50 returned responses) for two assessors. Reviewers should work independently; preserve the exported files and enter both sets of ratings without re-exporting, because export recreates the CSVs. If fewer than 50 responses are available, the minimum-sample flag remains false.

```bash
python -m evaluation.review export evaluation/results/validation_run01.json evaluation/results/review_run01
# assessor A fills the a_* columns and assessor B the b_* columns, independently
python -m evaluation.review score evaluation/results/review_run01
```

To include the review summary in an evaluation report, the current harness must be run again with `--human-review`, for example with a distinct output path:

```bash
python -m evaluation.harness data/validation_tickets.json evaluation/results/validation_reviewed_run.json --human-review evaluation/results/review_run01/review_summary.json --gate
```

This reruns provider inference, may incur additional cost, and can produce different responses; the harness does not currently bind review rows to the same run's ticket outputs. Keep the original inference report and document this limitation when interpreting the combined report. Repeat contacts cannot be measured offline because the datasets do not link follow-up tickets to the assistant's responses. For assessment, pass a labelled dataset path first and a new output path second. See [docs/governance.md](docs/governance.md) for metric definitions and review guidance.
