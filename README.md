# AI Tech Consultant (Business Labs Projects)

One shared **Knowledge Brain** (Task 1) answers product questions from the Brilliant Directories help center with sources. It is used **only through its API** by an **Inbox Agent** (Task 2) that drafts customer replies, and by **Meeting to Scope** (Task 3) that turns client meetings into a scope, an estimate and a proposal. Everything runs **offline at $0** by default.
More detail: [DESIGN.md](DESIGN.md) · [EVALS.md](EVALS.md) · [AI_LOG.md](AI_LOG.md) · [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md) · [TODO.md](TODO.md)

## Run it (VS Code terminal, one command)
Python 3.11+ with `fastapi uvicorn httpx pydantic streamlit altair pandas numpy tokenizers spacy`. Copy `.env.example` to `.env` (works as is).
Open **this** folder first (VS Code: File → Open Folder), or `cd` into it. Running `run.py` from another folder fails with "can't open file run.py".
```
cd "C:\Users\ZARA\OneDrive\Desktop\Business Labs Projects\ai-tech-consultant"
python run.py ingest --limit 20     # first time only: read 20 help-center articles (~1 min, polite)
python run.py all                   # API + all screens + cost dashboard. Ctrl+C stops everything
python run.py test                  # all 76 tests, $0
python run.py eval                  # all evaluations, $0
```
| Screen | URL | Try this |
|---|---|---|
| Task 1 chat + expert queue | http://localhost:8501 | "How do I clone a post type?", then something it can't answer |
| Task 1 API docs | http://localhost:8100/docs | `POST /query` |
| Task 2 inbox review | http://localhost:8502 | *Load the 56 sample emails* → review, edit, send |
| Task 3 scope builder | http://localhost:8503 | *Load sample meetings A and B* → approve v1 → *New meeting* (follow-up of meeting_a) with `meeting_c_followup.txt` |
| Token & cost dashboard | http://localhost:8600 | spend, tokens, budgets, $0 cap |

The same commands are under **Terminal → Run Task…**, and F5 entries are in `.vscode/launch.json`.

## What the brief asked for, what was built, and what was added
Status: ✅ done · 🟡 partly done · ❌ not done. Every number comes from [EVALS.md](EVALS.md).

### Task 1 — Knowledge Brain (Python)
| Brief requirement | Status | How / where | **Additional (beyond the brief)** |
|---|---|---|---|
| Read everything once, politely; survive stopping halfway | ✅ (full crawl running: 495 / 1,537) | `ingestion/crawler.py`: 2 at a time, ≥1.5 s apart, robots.txt, retries with backoff, progress saved per page, resumes | Content-hash skip: an unchanged re-run makes **0** requests. Limited test reads sample how-to articles first. |
| Understand the images | 🟡 | Each screenshot is stored with its article, section and the text before/after it, and shown with answers | — *(gap: no vision description, because no free vision model is available)* |
| Stay fresh every day; update only what changed; removed articles disappear | 🟡 | Sitemap diff (new / changed / removed), re-processes changed pages only, deactivates removed ones (tested). `python run.py ingest` does the daily run but is **not scheduled yet** | Article versions kept for rollback |
| Chatbot: correct answer, sources, honest "don't know" | ✅ 31/32 evals | `app/answer.py`, `ui/chat.py`: hybrid search, citations, abstains below 0.68 | Local embeddings in NumPy (no download, no PyTorch); hybrid vector + BM25 search; threshold calibrated dev/test; screenshots shown with answers |
| Clean API usable from its docs alone | ✅ | FastAPI `/query`, `/escalations`, `/ingestion/*`, `/health`, OpenAPI at `/docs` | Typed request/response schemas, optional bearer token, 60 s timeout, `source` tag per caller |
| Can't answer → expert task → answer flows back into memory | ✅ | Expert queue tab; `POST /escalations/{id}/resolve` | *General* vs *customer-only* answers; expiry dates; PII masked before storing; escalated questions saved for future evals |

### Task 2 — Inbox Agent (Python, as the brief allows instead of TypeScript)
| Brief requirement | Status | How / where | **Additional (beyond the brief)** |
|---|---|---|---|
| TypeScript | 🟡 Python | Node.js is not installed; the brief allows Python without mixing | — *(loses the 10% bonus)* |
| Understand each email, get facts from Task 1's API, draft in the company voice | ✅ | `inbox/classifier.py`, `kb_client.py`, `drafter.py`; held-out category 20/20, 23/23 answers grounded in the right article | Urgency and risk scoring; quote emails check whether an existing feature already covers the request |
| Human sends, edits, or writes their own | ✅ | `ui/review.py`: Send / Reject (own reply) / Expert / Redraft | Simulated outbox; one send per email (no duplicates) |
| Learn from every email; prove better, not worse | ✅ | `inbox/learning.py`: deleted sentences become versioned style rules, adopted only if a replay improves and changes no approved draft | One-click rollback; edit-rate and correction-reason stats |
| 20 unchanged drafts of the same kind → may go out alone; lose freedom when quality drops | ✅ | `inbox/autonomy.py`: same kind = category, unchanged = text compared (not the click), admin switch, kill switch on an edit or >10% edit rate, audit log | Refunds, complaints, quotes and projects **never** go out alone |
| ≥50 realistic emails | ✅ 56 | `data/emails.jsonl`, labelled, 20 held out as unseen emails | Prompt-injection and personal-data emails included |

### Task 3 — Meeting to Scope (Python)
| Brief requirement | Status | How / where | **Additional (beyond the brief)** |
|---|---|---|---|
| A team of agents with clear hand-offs | ✅ | `agents/`: Analyst → Feasibility → Estimation → Scope → Proposal & Review, each with a validated pydantic hand-off | Review agent blocks inconsistent proposals |
| Asks the questions a senior consultant would ask | 🟡 | Unclear items become open questions in the scope and proposal (not an interactive Q&A) | Explicit vs inferred requirements; every requirement keeps its verbatim quote |
| Scope of work, task list with hours, client-ready proposal | ✅ | 10-section scope, low/likely/high + PERT, proposal with configurable rate | Unclear items are never estimated or priced |
| Check existing settings through Task 1 | ✅ | `agents/feasibility.py` over the API | "Not found" is never treated as proof: it asks for an expert check |
| Second meeting: what changed, what's new, what's outside scope | ✅ | `core/diff.py`, compared with the **approved** baseline | Change requests get their own subtotal |
| Long transcripts | ✅ | Staged sections and merge; proven equal to one-pass | — |
| Nothing reaches the client without human approval | ✅ | `core/store.py`: named reviewer, hash check, refusals logged | Content edited after approval cannot be sent |
| ≥3 role-played meetings recorded with a friend, one a follow-up | 🟡 | 4 scripted transcripts (A, B, follow-up C, held-out D); **not recorded** | Held-out meeting D for honest testing (4/7) |

### Rules and submission
| Brief requirement | Status | Notes |
|---|---|---|
| Claude at the centre; compare models with 20–30 evals per choice | ❌ | The build cost is set to **$0**. The Claude path is built but blocked by the $0 cap, so no paid model comparison was run. |
| ≥20 test cases per task; quality, cost, speed | ✅ | 32 / 56 / 34 cases, cost $0, latency measured |
| Retries, human approval, safe bad input | ✅ | Retries with backoff, approval gates, input checks and guardrails |
| One folder per task; one command; commits over 4 days | ✅ / 🟡 | `run.py all`. Commits on 15, 16 and 18 Oct |
| README, DESIGN, EVALS, AI_LOG | ✅ (AI_LOG is a draft for the author to review) | |
| Demo video | ❌ | To be recorded by the author |
| One AI coding tool, full chat history | ✅ | Claude Code, one conversation |

### Additional work not asked for in the brief
| Added | Why | Where |
|---|---|---|
| **LLM gateway** with offline / Ollama / Claude modes, budgets per task, automatic downgrade, **hard monthly cap (default $0)** | Cost control; no surprise bills | `shared/llm_gateway/` |
| **Token & cost dashboard**: spend, tokens, cache savings, month-end projection, time to first token, alerts | See where money and tokens go | `dashboard/app.py` |
| **Fast metrics using data structures**: hourly roll-ups, latency histogram, top-K heap | 30-day summary in ~15 ms | `shared/llm_gateway/store.py` |
| **Streaming** replies, with time to first token logged | Faster first words | `gateway.stream()` |
| **Free spaCy tokenization** for chunking, search and token estimates | $0, no model download | `tokens.py`, `chunker.py` |
| **Shared guardrails**: injection, PII masking, citation, link and promise checks | Safety across all three tasks | `shared/guardrails.py` |
| **Replay cache**: record a paid answer once, replay it free | $0 re-runs | `providers.py` |
| **Held-out test sets** and leave-one-out tuning | Honest numbers | each `evaluation/` folder |
| **One-command runner + VS Code tasks/F5** | Easy to run and review | `run.py`, `.vscode/` |
| **76 automated tests**, all offline | Reproducible, free | `tests/` in each folder |
