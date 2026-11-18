# Governance and Safety Controls

**Status:** Implemented prototype controls with limitations listed below  
**Accountable owner:** Nabieesan (repository owner and operator; replace this name if another person is formally accountable)  
**Last reviewed:** 2026-11-18

This document records the controls built into this capstone. It is not a claim of production certification. The system can produce customer-facing text, so any control that is missing, untested, or pattern-based is called out explicitly.

When a usable key is configured, ticket text that passes the local pattern screen is sent to OpenRouter for classification and possibly response generation. Use real customer data only if that provider and data transfer are approved for the organization; the empty-key default avoids provider calls.

## Runtime Requirement IDs

| ID | Control |
|---|---|
| `GOV-LOG-01` | Persist one privacy-minimized decision/failure record for every valid request processed |
| `GOV-ROUTE-01` | Fail closed on unsafe confidence, evidence, provider, freshness, and governance conditions |
| `GOV-PRIV-01` | Screen recognized private-data patterns before provider calls and before returning responses |
| `GOV-KILL-01` | Recheck the operator kill switch before returning any automatic response |

## 1. Risk Assessment

### Scoring

Likelihood and impact use a three-point scale: 1 = low, 2 = medium, 3 = high. The score is likelihood multiplied by impact. Any impact-3 risk is treated as requiring a mitigation before automatic responses are enabled, even if its numerical score is lower. The likelihood estimates are initial project assessments, not measured incident rates.

| Likelihood \\ Impact | Low (1) | Medium (2) | High (3) |
|---|---|---|---|
| High (3) | None currently rated | None currently rated | None currently rated |
| Medium (2) | None currently rated | R-04, R-05, R-06, R-07, R-08 | R-01, R-02, R-03 |
| Low (1) | None currently rated | None currently rated | None currently rated |

The impact-high cells are the pre-build mitigation band. Each listed high-impact risk has a code control and a regression test, with residual limitations recorded below.

| ID | Risk | Likelihood | Impact | Score | Mitigation in this design | Accountable owner |
|---|---|---:|---:|---:|---|---|
| R-01 | A confidently wrong answer is sent to a customer | 2 | 3 | 6 | Keep the confidence floor in the 0.80-1.00 range; independently escalate explicit feature-request wording; validate citation IDs; require vector score >= `MIN_RETRIEVAL_SCORE` (default 0.30); escalate if scores/review ages are missing, sources are stale, or retrieval is unranked. The operator kill switch stops automatic replies. | Nabieesan, project owner/operator |
| R-02 | Private data reaches the provider or appears in an outbound reply | 2 | 3 | 6 | Before provider calls, scan subject/body for API secrets, email addresses, phone-like numbers, customer IDs, and payment-card-like numbers; matching tickets are escalated locally. Scan every candidate reply with the same patterns; any match blocks it. The sanitized draft is not sent. Arbitrary names and unrecognized identifiers are not reliably detected. | Nabieesan, project owner/operator |
| R-03 | Customer text attempts to override system instructions or elicit prompt disclosure | 2 | 3 | 6 | Detect common instruction-override phrases before classifier/retrieval/provider calls and escalate without generating a reply. Prompts delimit the ticket as untrusted data; output checks block obvious system/developer prompt disclosure. These phrase checks are defense in depth, not a complete injection detector. | Nabieesan, project owner/operator |
| R-04 | Some customer groups receive worse answers | 2 | 2 | 4 | Evaluation reports quality by tier, region, language fluency, and ticket length; the configured maximum quality-gap target is under 5 percentage points. The latest 80-ticket snapshot reports a 14.2-point gap, but groups are under-sampled. Review gaps; do not infer fairness from overall averages. | Nabieesan, project owner/operator |
| R-05 | Documentation relied upon by the system is stale | 2 | 2 | 4 | Retrieval carries each source's review age. Unknown ages or sources older than `MAX_DOC_AGE_DAYS` (default 180) force escalation. The corpus owner must review and refresh documentation; this metadata must be maintained accurately. | Nabieesan, project owner/operator |
| R-06 | The model provider is unavailable or returns invalid output | 2 | 2 | 4 | Provider calls have timeouts and a daily call budget. Invalid classification becomes zero-confidence and escalates. Generation errors produce no draft and escalate. | Nabieesan, project owner/operator |
| R-07 | Latency degrades under load | 2 | 2 | 4 | The target is end-to-end p95 under 3 seconds. The latest 80-ticket snapshot measured 16.50 seconds p95; provider calls dominated stage timing. Provider timeouts are 15 seconds for classification and 20 seconds for generation. Run a load test and agree an SLO before deployment; no production load test or autoscaling is included. | Nabieesan, project owner/operator |
| R-08 | Provider costs rise unexpectedly with volume | 2 | 2 | 4 | An atomic SQLite UTC-day cap limits combined model calls to `MAX_LLM_CALLS_PER_DAY` (default 100). Classification and generation requests also cap output at 500 and 800 tokens. This is a call/token guard, not a dollar-denominated provider budget; configure a provider spend limit separately. | Nabieesan, project owner/operator |

## 2. Decision Logging

Each successfully processed, schema-valid ticket produces one `pipeline_complete` row in `storage/decisions.db`. Unexpected pipeline exceptions produce one `pipeline_failure` row and an escalation response. If the audit database itself is unavailable, the API returns HTTP 503 and does not send an answer. Requests rejected by Pydantic with HTTP 422 have not entered the automated decision pipeline and are not decision-log rows.

The `decisions` table contains the original decision fields plus migration-safe governance columns:

| Field | What is recorded |
|---|---|
| `decision_id`, `created_at`, `ticket_id`, `stage` | Unique event identity, UTC timestamp, ticket correlation ID, and completed/failure stage |
| `input_summary` | Privacy-minimized metadata: ticket ID, channel, tier, region, fluency, subject/body character counts. Raw ticket text, names, email, and API keys are not stored here. |
| `model_name` | Configured provider model identifier, or `offline-rules` when keyword fallback was used |
| `prediction_json` | Predicted intent, urgency, and confidence |
| `alternatives_json` | Alternative predictions supplied by the classifier, or an empty list when none are available |
| `sources_used` | JSON array of document IDs and retrieval relevance scores; score is null when unavailable |
| `threshold` | Confidence threshold applied to routing |
| `action_taken`, `reason` | Final `auto_respond` or `escalate` decision and human-readable rationale |
| `guardrails`, `guardrail_results` | Checks, findings, provider status, and pass/block outcomes |
| `stage_results` | Normalization, instruction-integrity, classification, retrieval, generation, routing, validation, kill-switch and failure outcomes; when those stages run, `duration_seconds` records classification, retrieval and generation timings |
| `prompt_version` | Classifier, retrieval, and response prompt/version identifiers used |
| `requirement_ids` | Governance control IDs associated with the decision |

A source excerpt or full model prompt is deliberately not logged: those may contain customer personal data. This supports reconstruction of the decision path without retaining message content. Access and retention for the database should follow the project's data policy; the prototype does not implement database access control or automated retention deletion.

### Coverage check

After a test run, compare processed-ticket metrics to decision rows for the same interval. At rest, inspect decision rows and failures with:

```sql
SELECT stage, COUNT(*) AS decisions, COUNT(DISTINCT ticket_id) AS tickets
FROM decisions
GROUP BY stage;
```

A normal accepted-ticket run should have one row per ticket ID, with each row in `pipeline_complete` or `pipeline_failure`. Duplicate ticket IDs across separate retries are possible and should be investigated rather than silently deduplicated. A database outage is visible as an HTTP 503; because persistence is unavailable, that failure cannot be recorded in the same database.

## 3. Fairness Audit

The evaluation command is:

```bash
python -m evaluation.harness data/development_tickets.json evaluation/results/development_run01.json --ground-truth data/ground_truth_responses.json --gate
python -m evaluation.harness data/validation_tickets.json evaluation/results/validation_run01.json --gate
```

The report groups outcomes by `customer_tier`, `customer_region`, `language_fluency`, and combined subject/body length (`short` <= 500 characters, `long` > 500 characters). For each segment it records ticket count, resolution rate, quality score, variation from the best-scoring segment, intent/route/answerability accuracy, citation-ID precision/recall, and protected-ticket violations. The configured maximum quality-gap target is under 5 percentage points; groups smaller than 20 tickets are marked unreliable.

The harness calls `api.process_ticket_endpoint` in-process with labels withheld. It exercises the API processing, privacy and instruction screens, routing, kill-switch, guardrail and audit code, but does not measure HTTP/network behavior or a deployed service. Screened tickets count as escalations and do not call the provider. A per-run decision database is reconciled against tickets processed, and the report adds per-class precision, a confusion matrix, confidence calibration, p95 latency, per-stage latency and a gate table (`--gate`). Use a distinct output path for each run: rerunning with the same path deletes and rebuilds its `<output>.decisions.db`. Provider-backed evaluations make model calls and may incur cost. Offline classification, simulated outage, or an active kill switch makes a run invalid for the gate. Hallucination rate, semantic citation accuracy and satisfaction require a two-assessor review (`python -m evaluation.review export|score`, with at least 50 responses for the minimum-sample flag). See [application-architecture.md](application-architecture.md#evaluation).

- **Resolution rate** is the fraction of labeled answerable tickets whose expected route is `auto_respond` for which the system auto-responded, returned a response, cited every expected document, and had no guardrail violation.
- **Quality score** is the unweighted average of six per-ticket checks: intent correctness, urgency correctness, route correctness, answerability correctness, governance safety correctness, and citation coverage for expected auto-response cases. For expected-escalation cases citation coverage is not required; safety/route checks still apply.
- **Variation from best** is the difference between that segment's quality score and the highest score among groups for the same field.

This is a transparent evaluation proxy, not a validated customer-resolution outcome. Ground-truth `history` is baseline human-handled performance and is not a model target. Small groups (fewer than 20 tickets) are explicitly marked as low-confidence comparisons, and the cross-group gap is reported in percentage points. Review large gaps, investigate examples, and report them honestly; do not tune only to the overall average. The validation set has only eight enterprise tickets, so use the larger development set for exploratory analysis and keep validation as a separate check.

The saved provider-backed run `evaluation/results/validation_postfix.json` (17 November 2026; 80 tickets) reports a 14.2-point maximum gap and fails the under-5-point target. Its smallest group has seven tickets, so this is a warning signal for further testing, not a reliable population-level fairness conclusion. The same run's calibration gap is 10.5 points against a 5-point tolerance, and end-to-end p95 latency is 16.50 seconds against a target under 3 seconds. Its manifest records uncommitted source changes, and the validation set has been reused; do not treat it as an independent clean release evaluation.

The dataset labels 32/80 tickets (40%) for escalation, while the system target is at most 30%. This target conflicts with the expected-route distribution in this validation population. Reconcile the target with the assignment rubric and intended operating population; do not meet it by suppressing required escalations.

### Human Review Workflow

Hallucination rate, semantic citation accuracy, and satisfaction require human judgement. Export at least 50 automatically returned responses when available; have two assessors fill their columns independently, then score the completed sheets:

```bash
python -m evaluation.review export evaluation/results/validation_run01.json evaluation/results/review_run01
# assessor A fills a_* columns; assessor B fills b_* columns independently
python -m evaluation.review score evaluation/results/review_run01
```

Export recreates the CSV files, so do not rerun it after reviewers have entered ratings. The summary can be passed to the harness with `--human-review`, but the current harness reruns model inference and does not bind review rows to those newly generated responses. Use a new report output path, keep the original inference report, and treat the joined review metrics as unlinked unless run-to-row linkage is added. Re-evaluation makes additional provider calls and may incur costs. Fewer than 50 completed response reviews is flagged as an insufficient sample; repeat contacts remain unmeasurable without longitudinal data linking follow-ups to the assistant's replies.

## 4. Runtime Guardrails

| Guardrail | Runtime check | When it fires |
|---|---|---|
| Private data | Pattern scan for API secrets, email addresses, phone-like numbers, `CUST-...` identifiers, and payment-card-like digit sequences | Candidate response is blocked, sanitized for logs/debug only, not returned, and the ticket is escalated |
| Grounding | Require at least one `[Doc ID: ...]` citation when sources exist; every citation must match a retrieved document ID | Missing or unknown citation blocks the draft and escalates |
| Instruction integrity | Common prompt-override phrases in subject/body are detected before model calls; prompts state ticket text is untrusted; output scans for obvious prompt disclosure | Suspected override skips classification/retrieval; suspicious output is blocked and escalated |
| Tone and scope | Block a small list of unsupported refund/fix-date commitments | Draft is blocked and escalated |
| Feature-request safety | Detect clear feature-request phrases independently of predicted intent | Ticket escalates to human review |
| Confidence floor | `CONFIDENCE_THRESHOLD` defaults to 0.80 and values below 0.80 or above 1.00 are rejected; offline keyword classification is never eligible; provider failure is zero-confidence | Route escalates; missing confidence is never treated as high confidence |
| Evidence freshness and ranking | JSON-order fallback is not eligible; missing review age, age above `MAX_DOC_AGE_DAYS`, missing/invalid relevance score, or best score below `MIN_RETRIEVAL_SCORE` is rejected | Route escalates |
| Provider privacy | Sensitive patterns in inbound subject/body are checked before classifier/retrieval/provider calls | Ticket is locally escalated; model provider is not called |

The privacy checks are pattern-based: they do not recognize arbitrary person names or every possible account identifier. Citation checks establish source-ID alignment, not that every sentence is entailed by the cited passage. These are known limitations; the project must not claim comprehensive DLP or semantic fact verification.

## 5. Incident Response

The accountable incident lead is **Nabieesan, project owner/operator**. The steps below are internal response targets for this capstone, not external or legal notification SLAs.

| Step | Action | Owner | Target |
|---|---|---|---|
| 1. Detect | Review a customer report, guardrail event, audit row, provider error, or Prometheus alert. Preserve the ticket ID, timestamp, decision ID, and relevant logs without copying secrets into chat. | Project owner/operator | Acknowledge within 15 minutes while actively operating the service |
| 2. Contain | Activate the kill switch immediately. If exposure is suspected, revoke/rotate the affected credential and restrict access to stored artifacts. | Project owner/operator | Start within 5 minutes of confirmation |
| 3. Assess | Identify affected tickets/time range, decision IDs, model/prompt versions, sources, guardrail results, and whether a reply was returned. Do not replay customer data to an external model during investigation. | Project owner/operator | Initial scope within 30 minutes |
| 4. Notify | Notify the course/project stakeholder and any affected internal owner using the applicable organization policy. Do not invent a legal notification deadline. | Project owner/operator | Initial internal notice within 1 hour of confirmed impact |
| 5. Remediate | Keep automatic responses disabled; patch or correct data/configuration; add a regression test; rotate secrets if applicable. | Project owner/operator | Before re-enabling, with a recorded verification result |
| 6. Review | Record root cause, affected decisions, residual risk, corrective action, and whether the risk register changed. | Project owner/operator | Within 2 working days |

### Kill switch

The operator creates this file from the repository root:

```bash
touch storage/AUTO_RESPONSE_DISABLED
```

The API checks the file before classification/retrieval and again after generation before returning a draft. New requests are escalated without model/provider calls while the file exists. If it is activated during an in-flight provider call, the completed draft is suppressed at the final gate; that call may take up to its configured timeout to finish. To re-enable, remove the file only after review:

```bash
rm storage/AUTO_RESPONSE_DISABLED
```

The control is available to whoever has write access to the application host. It is intentionally not an unauthenticated public API endpoint. Re-enablement is owned by the project owner/operator and should follow the incident review.

## 6. Declaration

- **This system must never** send a draft when request processing, classification, evidence retrieval, provider generation, or a required guardrail fails; it must escalate instead.
- **The mechanism that enforces that is** request validation, instruction-override detection, confidence and freshness routing, exact source-citation checks, private-data/commitment checks, provider call budgets, and the file-based kill switch checked before response return.
- **The most likely way it could still cause harm is** a plausible but unsupported claim or sensitive information not recognized by the current pattern-based checks being returned with a valid-looking citation.
- **We would not deploy this without first** fixing and independently testing the remaining DLP/grounding limitations, running load and fairness reviews on representative data, securing and retention-limiting the audit database, validating a named incident contact and notification policy, and demonstrating the kill switch in a production-like environment.
