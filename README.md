# Support System

A Python 3.11 support-ticket assistant project. Runtime modules live in `src/`, versioned prompts in `prompts/`, evaluation tools and dated results in `evaluation/`, architecture notes in `docs/`, source datasets in `data/`, and generated databases/vector indexes in `storage/`.

## Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with the values needed for your local environment. Never commit `.env` or API keys.

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

The report includes intent, urgency, route, and answerability accuracy; retrieval Hit@K and Recall@K; citation precision and recall; protected-ticket violations; response latency; and slices by tier, region, and language fluency. Reference-text metrics are calculated only for IDs that match the optional reference file. For assessment, pass the hidden dataset path as the first argument and the desired JSON report path as the second. See [docs/architecture.md](docs/architecture.md) for module and data boundaries.
