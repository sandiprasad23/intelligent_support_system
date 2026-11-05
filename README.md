# Support System

A Python 3.11 support-ticket assistant project. Runtime application modules live in `src/`, source datasets live in `data/`, and generated databases and vector indexes live in `storage/`.

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

The metrics endpoint is `http://localhost:8081/metrics`. In a second terminal, start Prometheus from the project root using `prometheus.yml`. On machines whose hostname causes URL parsing errors, run:

```bash
prometheus --config.file=prometheus.yml --web.listen-address=127.0.0.1:9090 --web.external-url=http://localhost:9090/
```

## Tests

```bash
python -m pytest tests/ -v
```

GitHub Actions runs this offline test suite on pushes and pull requests. Model API calls are not part of CI.
