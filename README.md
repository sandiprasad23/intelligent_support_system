# Support System

A Python 3.11 support-ticket assistant project. Runtime modules live in `src/`, versioned prompts in `prompts/`, evaluation tools and dated results in `evaluation/`, architecture notes in `docs/`, source datasets in `data/`, and generated databases/vector indexes in `storage/`.

Governance controls, risk register, decision-log fields, fairness methodology, incident steps, and the auto-response kill switch are documented in [docs/governance.md](docs/governance.md). [docs/application-architecture.md](docs/application-architecture.md) is the complete reference covering the pipeline, modules, governance, monitoring, evaluation and limitations.

## Setup and run

### Prerequisites

- Python **3.11** with `pip` and `venv`.
- Git.
- Internet access to install dependencies and download the embedding model.
- Port `8000` for the API and port `8001` for metrics must be available.

### 1. Install Python 3.11 and Git

- **macOS with Homebrew:**

```bash
brew install python@3.11 git
```

- **Ubuntu/Debian:**

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3-pip git
```

- **Windows with WinGet (PowerShell):**

```powershell
winget install --id Python.Python.3.11 -e
winget install --id Git.Git -e
```

- Alternatively, install Python 3.11 from [python.org](https://www.python.org/downloads/) and enable **Add Python to PATH** on Windows.
- Restart the terminal after installation.

### 2. Verify the tools

macOS/Linux:

```bash
python3.11 --version
git --version
```

Windows:

```powershell
py -3.11 --version
git --version
```

- Python must report `3.11.x`.

### 3. Get the project

```bash
git clone <repository-url>
cd intelligent_support_system
```

- Run every remaining command from the directory containing `requirements.txt`.
- Skip cloning and run only `cd <path-to-project>` if the repository is already present.

### 4. Create and activate the virtual environment

macOS/Linux:

```bash
python3.11 -m venv .venv #To create virtual env
source .venv/bin/activate  # To activate it
which python # To check the current python environment file. It should show the current applications .venv/bin/python path
python -m pip install --upgrade pip
```

Windows PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

- If PowerShell blocks activation:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\.venv\Scripts\Activate.ps1
```

- Verify the environment:

```bash
python --version
python -c "import sys; print(sys.executable)"
```

- The path printed must point inside `.venv`.
- Activate `.venv` again whenever a new terminal is opened.

### 5. Install dependencies

```bash
python -m pip install -r requirements.txt
```

### 6. Configure `.env`

macOS/Linux:

```bash
test -e .env || cp .env.example .env
```

Windows PowerShell:

```powershell
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

Edit `.env` with the required local values:

```dotenv
OPENROUTER_API_KEY=
MODEL_NAME=meta-llama/llama-3.1-8b-instruct
EMBEDDING_MODEL=all-MiniLM-L6-v2
CHROMA_PATH=./storage/chroma
DATABASE_URL=sqlite:///./storage/decisions.db
LOG_LEVEL=INFO
CONFIDENCE_THRESHOLD=0.80
```

- Do not commit `.env` or a real API key.
- `OPENROUTER_API_KEY` can remain blank for offline tests and offline fallback behavior.
- A real key and valid `MODEL_NAME` are required for provider-backed responses and valid evaluation runs.
- These values come from `.env`; the application does not provide source-code defaults for them.

### 7. Initialize the application

```bash
python init_db.py
python data-chunking.py
```

- `init_db.py` creates the SQLite tables.
- `data-chunking.py` builds the documentation vector index and may download the embedding model on its first run.

### 8. Run the tests

```bash
python -m pytest tests/ -v
```

- Tests run offline and do not require a provider API call.

### 9. Start the API

```bash
python -m src.api
```

- API: `http://localhost:8000`
- Swagger UI: `http://localhost:8000/docs`
- Metrics: `http://localhost:8001/metrics`
- Stop the application with `Ctrl+C`.

## Optional operations

- Disable automatic responses immediately by creating `storage/AUTO_RESPONSE_DISABLED`.
- Remove that file only after review to re-enable automatic responses.
- Run metrics without the API:

```bash
python monitoring.py
```

- Start Prometheus from a second terminal:

```bash
prometheus --config.file=prometheus.yml
```

- If Prometheus has hostname URL errors:

```bash
prometheus --config.file=prometheus.yml --web.listen-address=127.0.0.1:9090 --web.external-url=http://localhost:9090/
```

- If port `8000` is occupied, set `API_PORT` in `.env`.
- If port `8001` is occupied, set `METRICS_PORT` in `.env` and update `prometheus.yml` accordingly.

## Evaluation

- Complete the database and vector-index initialization in setup step 7 before evaluation.
- The hidden/validation input can be passed directly without code changes.
- Configure a real `OPENROUTER_API_KEY` for a valid provider-backed gate run; model calls may incur cost or rate limits.
- Run a validation gate with a new output path:

```bash
python -m evaluation.harness data/validation_tickets.json evaluation/results/validation_run01.json --gate
```

- Use a different output path for every run; the matching `.decisions.db` is deleted and rebuilt when that path is reused.
- A labelled input must contain the ticket and label fields required by the harness.
- Run the development set with literal reference checks when matching ground-truth records are available:

```bash
python -m evaluation.harness data/development_tickets.json evaluation/results/development_run01.json --ground-truth data/ground_truth_responses.json --gate
```

- Without `--gate`, failures are reported but the command exits successfully.
- With `--gate`, exit code `1` means a target failed or the run was invalid.
- Check both `gate.passed` and `validity.valid_for_gate` in the JSON report.
- Test outage behavior with:

```bash
python -m evaluation.harness data/validation_tickets.json evaluation/results/outage_check.json --simulate-provider-outage --run-log evaluation/results/outage_run_log.jsonl
```

### Evaluation notes

- The pipeline processes tickets through `process_ticket_endpoint` in-process; HTTP/network behavior is not tested.
- Ground-truth labels are withheld from routing.
- The per-run database is reconciled against processed tickets.
- Offline fallback, simulated outage, or an active kill switch makes `valid_for_gate: false`.
- The default run log is `evaluation/results/run_log.jsonl`; override it with `--run-log`.
- `--no-warm-up` skips embedding-model preloading.
- The report includes accuracy, retrieval, citation, safety, fairness slices, calibration, latency, and gate results.
- Citation-ID checks do not prove semantic support for each response sentence.
- First-contact resolution is an automatic-response proxy, not a measured customer outcome.
- Stage latency values are separate from end-to-end latency and cannot be summed to obtain its percentile.
- The validation population labels 40% of tickets for escalation, while the configured target is at most 30%; do not override safety labels to force the target.
- The report requires human review for hallucination rate, semantic citation accuracy, and satisfaction.

### Human review

- Export a seeded sample for two independent assessors:

```bash
python -m evaluation.review export evaluation/results/validation_run01.json evaluation/results/review_run01
```

- Assessor A fills the `a_*` columns; assessor B independently fills the `b_*` columns.
- Preserve the exported files and do not re-export before scoring, because export recreates the review CSVs.
- The default requested sample is 50 responses; the minimum-sample flag remains false when fewer responses are available.
- Score the completed reviews:

```bash
python -m evaluation.review score evaluation/results/review_run01
```

- Add the review summary with a new evaluation report:

```bash
python -m evaluation.harness data/validation_tickets.json evaluation/results/validation_reviewed_run.json --human-review evaluation/results/review_run01/review_summary.json --gate
```

- This command reruns provider inference, may incur additional cost, and may produce different responses.
- Review rows are not currently bound to the same run's outputs; retain the original report and document this limitation.
- Repeat contacts cannot be measured offline because datasets do not link follow-up tickets to earlier responses.
- See `docs/governance.md` for metric definitions and review guidance.
