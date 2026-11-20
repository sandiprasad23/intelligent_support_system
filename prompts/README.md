# Prompt Registry

Prompts used by the running system live directly in this directory and are versioned with the source.

- `classify_v1.txt`: ticket intent, urgency, confidence, alternatives, and entity extraction, with per-intent definitions and confidence guidance (`PR-01-v1.2`).
- `respond_v1.txt`: concise source-grounded response drafting with document citations (`PR-03-v1.2`).

When a prompt changes materially, add a new versioned file and update the corresponding version constant in `src/`; do not overwrite a prompt version used to produce prior decisions.
