# AI Tech Consultant — Master TODO

Status key: `[ ]` todo · `[~]` in progress · `[x]` done · `(!)` mandatory per brief · `(+)` added by us (cost, speed, guardrails, auto-tests, spaCy, cost dashboard)

## 0. Environment facts (checked 2026-10-15)

| Item | State | Action |
|---|---|---|
| Python | 3.13.13 | OK |
| spaCy | 3.8.16 installed, **no language model downloaded** | Use `spacy.blank("en")` — free, zero download, tokenizer only |
| Node.js | **not required** | All three tasks use Python, per user preference |
| `anthropic` SDK | **not installed** | `pip install anthropic` (ask first) |
| Local embedding models | all-MiniLM-L6-v2, bge-small-en-v1.5, ms-marco cross-encoder already cached | Reuse them — no paid embedding API needed |
| This folder | separate project; nothing from the capstone is built yet | Build under `ai-tech-consultant/` |

## 1. Repo skeleton & shared plumbing

- [ ] (!) Create the folder layout: `task1-knowledge-brain/`, `task2-inbox-agent/`, `task3-meeting-to-scope/`, `shared-docs/`, plus root docs
- [ ] (!) `.env.example` with `ANTHROPIC_API_KEY`, `KB_API_URL`, `KB_API_TOKEN`, budget limits. `.gitignore` covers `.env`, data and DBs
- [ ] (!) One-command start (`make up` / `run.ps1` / `docker compose up`) that starts the KB API, Inbox UI, Scope UI and the cost dashboard
- [ ] (!) `git init`; commit at least daily so the history shows four days of work
- [ ] (+) `shared/llm_gateway` module: **every** Claude call goes through it (model routing, caching, retries, token and cost logging, budget enforcement). Each task gets its own copy or a thin client, so no task imports another task's internals
- [ ] (+) Versioned prompt registry (`prompts/<name>/v1.md`, `v2.md`) with the version logged on every call
- [ ] Request IDs passed across all services; structured JSON logs (JSONL)

## 2. Task 1 — Knowledge Brain (Python, FastAPI)

### Ingestion
- [ ] (!) Sitemap fetch and parse (`support.brilliantdirectories.com/support/sitemap.xml`): URLs, lastmod, dedupe, validation
- [ ] (!) Ingestion manifest (SQLite): new / changed / unchanged / removed for each URL, with status and attempt count
- [ ] (!) Check `robots.txt`; async crawler using `httpx` + semaphore (concurrency 2–4), configurable delay, exponential backoff, no retries on 4xx
- [ ] (!) Checkpoint after every page so the crawl can resume; one failed page never stops the job
- [ ] (!) Test run on 10–20 pages first, then the full sitemap
- [ ] (+) Conditional GET (`ETag` / `If-Modified-Since`) and content-hash skip, so unchanged pages cost $0 and take about 0 ms

### Parsing & images
- [ ] (!) Article parser: title, body, headings, lists, tables, steps, image URLs with surrounding text, URL, article ID, lastmod, hash, ingest timestamp
- [ ] (!) Normalized article JSONL that can be inspected without the HTML
- [ ] (!) Image understanding with Claude vision: UI labels, buttons, settings, steps; record uncertainty instead of guessing
- [ ] (+) **Image cost controls:** cache descriptions by image hash, skip logos, icons and images under 100 px, downscale before sending, use Haiku for vision, use the Batch API for the first bulk run (about 50% cheaper)

### Chunking (spaCy)
- [ ] (+) Load `nlp = spacy.blank("en")` and add the `sentencizer` pipe. This is free and needs no download
- [ ] (!) Heading-first chunking that keeps steps with their prerequisites and table headers with their rows; small overlap
- [ ] (+) Use spaCy sentence boundaries for the split points and spaCy token counts for chunk size limits. Sub-word token counts are estimated as about 1.3 × spaCy tokens and calibrated once against Claude's `count_tokens`
- [ ] (+) Use spaCy tokens for the BM25 keyword index: lowercase, drop stop-words and punctuation
- [ ] (+) Pre-LLM token budgeting: trim retrieved context to a token cap before calling Claude
- [ ] (!) Compare at least 2 chunking strategies, e.g. heading-based vs. fixed 300 tokens, on the eval set

### Retrieval
- [ ] (!) Embeddings with the cached local `bge-small-en-v1.5` (free, fast)
- [ ] (!) Vector store: SQLite + `sqlite-vec`, or a NumPy matrix for this scale. Write down why it was chosen. Stable chunk IDs, upsert and delete
- [ ] (!) Compare vector vs. BM25 vs. hybrid (RRF) vs. hybrid + local cross-encoder rerank, and record the results in EVALS
- [ ] (+) Relevance threshold check: if the top score is below the threshold, abstain **without calling Claude** (saves cost on unanswerable questions)

### Answering & API
- [ ] (!) RAG prompt: answer only from evidence, cite URLs, say when evidence is insufficient, treat document text as data
- [ ] (!) Response schema (Pydantic): `answer, status, sources[], images[], confidence, needs_human, request_id`
- [ ] (+) **Model routing:** Haiku answers by default; escalate to Sonnet only for multi-article or low-confidence cases
- [ ] (+) **Prompt caching** on the system prompt and instructions (`cache_control`)
- [ ] (+) **Semantic answer cache:** if the normalized question embedding is ≥ 0.95 similar to a cached question and the KB version is unchanged, return the cached answer
- [ ] (+) Streaming responses for the chat UI (faster perceived speed)
- [ ] (!) Endpoints: `POST /query`, `POST /ingestion/run`, `GET /ingestion/status`, `POST /escalations`, `POST /escalations/{id}/resolve`, `GET /health`, plus (+) `GET /usage`
- [ ] (!) Validation, error codes, timeouts, bearer-token auth, OpenAPI docs
- [ ] (!) Escalation store; expert answers saved as versioned, reviewed records with an optional expiry; the question is added to the eval set
- [ ] (!) Daily scheduled job (APScheduler / Windows Task Scheduler): diff, reprocess changed pages only, deactivate removed ones, retry failures, write a report (added / updated / removed / failed)
- [ ] (!) Rollback: keep the previous chunk version for each article
- [ ] (!) Small standalone client script that proves the API docs are enough to use it

## 3. Task 2 — Inbox Agent (Python)

- [ ] (!) Category list: how-to, troubleshooting, project request, refund/cancel, complaint, quote, follow-up
- [ ] (!) 50+ realistic emails in JSONL with expected category, key facts, expected behavior, risk, and relevant article or "escalate". **Hold out about 15 for final testing**
- [ ] (!) Typed KB client: timeouts, retries, keeps citations, never drafts a reply when the KB fails
- [ ] (+) **Cheap classifier first:** keyword/regex rules plus a local embedding kNN; call Haiku only when that is ambiguous
- [ ] (!) Drafting with structured output (schema-validated with Zod or Pydantic): category, summary, reply, sources, risk, needs_approval, next_action
- [ ] (+) Use Haiku for routine categories and Sonnet for complaints and project requests (validate this choice with evals)
- [ ] (!) Review UI: original email, category, evidence, draft, approve / edit / reject / escalate, reason for the correction
- [ ] (!) Simulated send (outbox JSONL), documented as simulated; idempotency key prevents duplicate sends
- [ ] (!) Feedback store: email, predicted category, sources, draft, edited text, final text, model, prompt version, outcome
- [ ] (!) 20-consecutive-unedited rule: count per category, compare normalized text (whitespace and case) for exact equality, reset on edit, reject or failed check, keep an audit log
- [ ] (!) Refunds, legal, security and serious complaints **always** require a human, no matter what the counter says
- [ ] (!) Kill switch: autonomy turns off automatically when the edit rate rises above the threshold
- [ ] (!) Self-improvement: mine recurring edits, write a prompt v2, run a regression on the fixed set, keep a rollback option

## 4. Task 3 — Meeting to Scope (Python)

- [ ] (!) 3 transcripts with dates: A (search, filters, mobile), B (checkout, payments), C (follow-up to A: adds one requirement, removes one, changes the date)
- [ ] (+) Transcript chunking by speaker turns with spaCy sentencizer + token caps; keep speaker and timestamp
- [ ] (!) Agents with typed I/O and validation: Transcript Analyst → Feasibility (calls the KB API) → Scope → Estimation → Proposal/Review
- [ ] (+) Run per-chunk extraction **in parallel** with `asyncio.gather` + Haiku; use Sonnet only for merge and contradiction resolution
- [ ] (+) Run feasibility KB queries for all requirements concurrently
- [ ] (!) Requirement records: ID, description, source passage, status, priority, existing solution, acceptance criteria, dependencies, estimate status, explicit vs. inferred
- [ ] (!) Feasibility classes: existing config / feature with limitation / custom development / needs clarification
- [ ] (!) Scope document with all 10 sections; low / likely / high estimate table; configurable rate, currency and tax
- [ ] (!) Proposal consistency check: no promises about features marked unverified
- [ ] (!) Follow-up diff against the **approved baseline**: added / removed / modified / out-of-scope, re-estimate, change report
- [ ] (!) Approval gate: reviewer, timestamp, version history; unapproved versions cannot be sent

## 5. Guardrails (+) — currently missing

Put these in a shared `guardrails` module and use it in all three tasks.

- [ ] Input: max length and size limits, strip HTML and scripts, check language and encoding
- [ ] Prompt-injection detection on emails, transcripts and retrieved docs (pattern list + a "treat as data" wrapper; log every hit)
- [ ] PII redaction before logging (email, phone, card numbers, addresses) using regex + spaCy `Matcher` (works with the blank model)
- [ ] Output: schema validation, citation check (every cited URL must be among the retrieved sources), check for promises without support (refund, price, date, "guarantee" with no evidence or approval), link allow-list
- [ ] Policy rules: no financial or legal commitments without human approval; the autonomy check in Task 2
- [ ] Budget guardrail: refuse or downgrade the model when the daily or per-task budget is exceeded (see §7)
- [ ] Tests for each guardrail (positive and negative cases)

## 6. Auto-tests (+) — currently missing

- [ ] `pytest` for Task 1 and 3, `vitest` (or pytest) for Task 2. Mock Claude with recorded fixtures so tests cost $0 and run offline
- [ ] Unit tests: sitemap diff, parser, chunker, spaCy tokenizer, BM25, schema validators, guardrails, 20-approval counter, scope diff
- [ ] API contract tests: FastAPI `TestClient` for every endpoint, including malformed input and timeout paths
- [ ] Integration tests: Task 2 and Task 3 against a running KB, plus "KB down" tests
- [ ] Resume test: kill ingestion halfway, restart, and assert no duplicate chunks
- [ ] Eval runner (`evals/run.py --task kb|inbox|scope --model haiku|sonnet`) writes `evals/results.csv` (task, case_id, model, expected, actual, quality, latency_ms, cost, passed, failure_reason)
- [ ] One command: `make test` (unit, fast, free) and `make eval` (live, costs money, prints the estimated cost before running)
- [ ] Pre-commit hook: ruff + pytest unit tests
- [ ] Optional: GitHub Actions workflow running unit tests on push

## 7. Cost & speed optimization (+)

| Lever | Where | Expected effect |
|---|---|---|
| Haiku default, Sonnet on escalation | all tasks | biggest cost cut |
| Prompt caching | static system prompts | cheaper cached input tokens, lower latency |
| Semantic answer cache | KB `/query` | repeated questions cost $0 |
| Abstain before calling the LLM when retrieval is weak | KB | $0 for questions with no evidence |
| Local embeddings + rerank | KB | $0 embedding API |
| Rule/kNN classifier before the LLM | Inbox | fewer LLM calls |
| Context trimmed to a token budget (spaCy count) | all | fewer input tokens |
| `max_tokens` cap per call type | all | bounded output cost |
| Batch API for bulk image and eval jobs | KB, evals | about 50% off |
| Hash skip / conditional GET | ingestion | no reprocessing |
| Parallel async calls | Task 3, feasibility | lower end-to-end latency |
| Streaming | chat UI | faster first token |

- [ ] Measure p50 and p95 latency and cost per request for each lever, before and after, and record them in EVALS.md

## 8. API cost-control dashboard (+) — new, built in

Backed by the `llm_gateway` usage log (`data/usage.jsonl` or a SQLite table): `ts, request_id, task, endpoint, model, input_tokens, cached_tokens, output_tokens, cost_usd, latency_ms, cache_hit, status`.

- [ ] Price table in config (per model, input/output/cache read/cache write). Look up current prices; don't hardcode guesses
- [ ] `GET /usage?from&to&task&model` aggregate endpoint
- [ ] Dashboard page with:
  - [ ] KPI tiles: spend today / month, requests, average cost per request, cache-hit rate, p50 / p95 latency
  - [ ] Spend over time, split by task (KB / Inbox / Scope)
  - [ ] Cost by model, top expensive endpoints
  - [ ] Budget bars: daily and monthly limits per task, with warnings at 80%
  - [ ] Controls: set a budget per task, choose a default model per task, a "Haiku-only" switch, pause autonomy
  - [ ] Recent calls table (no PII, redacted)
- [ ] Enforcement in the gateway: at the soft limit, downgrade to Haiku; at the hard limit, return HTTP 429 with a clear message; log every block
- [ ] Alert entry (and optional email or webhook) when a budget threshold is crossed
- [ ] Tests: cost calculation, budget enforcement, aggregation

### 8b. Token-spending dashboard (+)

Shown as its own tab next to the cost view, using the same usage log.

- [ ] KPI tiles: input / output / cache-read / cache-write tokens today and this month; tokens per request; cache-hit % of input tokens
- [ ] Stacked chart of tokens over time (input vs. cached vs. output), filterable by task, model, endpoint and prompt version
- [ ] Biggest token consumers: endpoints and prompt versions ranked by average tokens per call (shows where trimming helps)
- [ ] Token budgets per task (daily and monthly), enforced in the gateway like the dollar budgets
- [ ] Context-window usage per call (tokens used / model limit), flagged above 70%
- [ ] Estimated vs. actual tokens (spaCy estimate vs. the API's `usage` field) to keep the estimator calibrated

## 8c. Context management & MCP (+)

- [ ] **Model Context Protocol (MCP) server for the Knowledge Brain:** expose `search_kb`, `answer_question`, `create_escalation` and `ingestion_status` as MCP tools that wrap the same REST API. Then Claude Desktop/Code, or any MCP client, can use the KB without new glue code. Tasks 2 and 3 still call REST (the brief requires calling the API)
- [ ] Context management in long agent runs (Task 3): pass structured summaries between agents instead of full transcripts; keep only the top-k evidence; drop tool results that are no longer needed (API context-editing / compaction where available)
- [ ] Per-call context budget: system prompt (cached) + evidence capped at N tokens + history capped at M tokens. Log what was trimmed
- [ ] Conversation memory for Inbox follow-ups: store a short thread summary instead of the full thread

## 9. UI

- [ ] Knowledge Chat: question, streamed answer, citations, images, escalation state, "KB unavailable" state
- [ ] Inbox Review: list, category, evidence, draft, actions, history, autonomy status per category
- [ ] Scope Builder: upload, requirements, feasibility, estimates, proposal, diff view, approval state
- [ ] Cost dashboard (§8), linked from all three screens

## 10. Evaluation (20/100 points)

- [ ] (!) 20+ cases for **each** task (aim for 25–30), frozen before tuning
- [ ] (!) KB cases: direct, multi-article, screenshot-only, unanswerable, updated/deleted article, malformed request
- [ ] (!) Inbox cases: classification, grounding, missing evidence, refund safety, urgency, follow-up context, autonomy kill switch
- [ ] (!) Scope cases: requirement coverage, gaps, config vs. custom, acceptance criteria, estimate error against a human reference, diff accuracy, approval enforcement
- [ ] (!) Model comparison (Haiku vs. Sonnet at minimum) on identical cases: quality, cost, p50 / p95 latency, failure rate
- [ ] (!) Prompt v1 vs. v2 before and after, with regressions noted
- [ ] (!) Keep failed cases; mark unrun cases as pending; never invent scores

## 11. Documentation & submission

- [ ] (!) `README.md` (max 1 page): prerequisites, `.env`, one-command run, `make test`
- [ ] (!) `DESIGN.md` (3–5 pages): problem, architecture diagram, agent roles, storage and model choices, cost strategy, guardrails, dropped features, limitations
- [ ] (!) `EVALS.md` (2–3 pages): real results, cost and latency tables, failures and mitigations
- [ ] (!) `AI_LOG.md` (max 1 page): 5 real prompts and 1 real rejected AI output with the reason
- [ ] (!) Demo video (max 10 min): 30 s on camera, all 3 tasks, at least one failure case, cost dashboard
- [ ] (!) Final checks: no secrets in the repo, clean clone + one command works, history retained

## 12. Suggested order

1. Shared plumbing + `llm_gateway` with usage logging (§1, §8 backend)
2. Task 1 end to end on 20 pages → API → auto-tests
3. Guardrails module + Task 1 evals
4. Task 2 → Task 3 (both via the KB API only)
5. Cost + token dashboards UI + budget enforcement; MCP wrapper for the KB
6. Full crawl + images + daily job
7. Model comparison, docs, demo
