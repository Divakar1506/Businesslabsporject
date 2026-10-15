# AI Tech Consultant: Project Overview

*Business Labs Projects · JD4 capstone · status as of 2026-10-15*

This project is separate from the Future Forecaster app. Everything lives in this folder:
`Desktop/Business Labs Projects/ai-tech-consultant`.

---

## 1. The problem

Software that cost a client $2,000 two years ago now sells for about $200. To pay the same salaries, a small software company must handle 10 to 20 times more clients.

Writing code is no longer the slow part. The slow parts are:

| Slow part | What it costs the business |
|---|---|
| Finding the right answer in hundreds of help articles | Slow, inconsistent replies |
| Answering every customer email by hand | High support cost, missed follow-ups |
| Turning a vague client meeting into a scope, estimate and proposal | Days per proposal, missed items, unpaid extra work |

## 2. The solution: one brain, two helpers

```
                ┌──────────────────────────────────────┐
                │  Task 1: Knowledge Brain  (Python)   │
                │  help center + screenshots → answers  │
                │  with sources, or "I don't know"     │
                └───────────────┬──────────────────────┘
                     REST API   │   (the only way in)
              ┌─────────────────┴─────────────────┐
              ▼                                   ▼
┌───────────────────────────┐       ┌───────────────────────────────┐
│ Task 2: Inbox Agent       │       │ Task 3: Meeting to Scope      │
│ email → draft reply →     │       │ transcript → scope → hours    │
│ human approves → learns   │       │ estimate → proposal → human   │
└───────────────────────────┘       └───────────────────────────────┘
              │                                   │
              └──────────────┬────────────────────┘
                             ▼
        Shared LLM gateway  →  usage log  →  Token & Cost dashboard
```

- **Task 1, Knowledge Brain:** reads the Brilliant Directories help center (sitemap, articles and screenshots), checks for changes every day, answers questions with source links, and passes questions it can't answer to a human expert.
- **Task 2, Inbox Agent:** sorts each customer email by type, gets the facts from Task 1, and drafts a reply. A human approves, edits or rejects it. Once 20 drafts of the same type in a row are sent unchanged, that type may be sent automatically.
- **Task 3, Meeting to Scope:** a small team of agents turns meeting transcripts into requirements, checks with Task 1 whether a product setting already solves each need, then writes the scope, hour estimates and proposal. A follow-up meeting is compared with the approved scope. Nothing goes to the client without human approval.

**Key rule:** Tasks 2 and 3 use Task 1 only through its API, never by copying its code. One knowledge update then reaches both helpers.

## 3. What is built today

| Part | Status | Where |
|---|---|---|
| Folder structure for all 3 tasks | Done (empty folders) | `task1-…`, `task2-…`, `task3-…` |
| Shared LLM gateway | **Done, tested** | `shared/llm_gateway/` |
| Token & cost dashboard | **Done, tested** | `dashboard/app.py` |
| $0 cost protection | **Done, tested** | `providers.py`, `budget.py` |
| 28 automatic tests | **Passing** | `tests/` |
| VS Code one-click run | Done | `.vscode/` |
| Task 1, Task 2, Task 3 logic | **Not started** | see TODO.md |
| DESIGN.md, EVALS.md, AI_LOG.md, demo video | Not started | |

### 3.1 The shared LLM gateway: every AI call goes through one door

Why: so cost, speed and safety are controlled in one place instead of three.

| File | What it does |
|---|---|
| `gateway.py` | `Gateway.call()` and `Gateway.stream()`. It picks the model, caches the system prompt, calls the model, and logs the result. Streaming shows the first words sooner. |
| `providers.py` | The three modes (below) and a saved-answers file so the same question never has to be paid for twice. |
| `budget.py` | Spending limits per task and for all tasks, in dollars and tokens. Near a limit, calls switch to the cheapest model. Over a limit, calls are blocked. Also enforces the hard monthly cap. |
| `pricing.py` | Price per model, including cache-read and cache-write prices. Local models cost $0. |
| `tokens.py` | Free token estimates using spaCy's blank English tokenizer (no model download). |
| `store.py` | SQLite usage log with fast summaries (see 3.3). It never stores the email or question text. |

### 3.2 Zero-cost design: the build costs $0

| Mode (`LLM_MODE` in `.env`) | Cost | What happens |
|---|---|---|
| `offline` **(default)** | $0 | Returns a saved answer from `data/replay.jsonl`, or a reply clearly marked "[offline placeholder]". No internet needed. |
| `ollama` | $0 | Uses a free local model (for example llama2) through Ollama. |
| `claude` | Paid | Real Claude API. **Blocked by default**, because `HARD_MONTHLY_CAP_USD=0`. |

The cap is checked **before** any request is sent, so a mistake cannot create a bill. The dashboard can lower limits but cannot raise the cap. Only `.env` can.

### 3.3 Token & cost dashboard (Streamlit)

Five tabs:

1. **Token spending:** tokens over time by kind (input, cache read, cache write, output) and by task, the biggest consumers, output speed, time to first word, and how close the free token estimate is to the real count.
2. **Cost:** spend today vs. yesterday, a month-end projection, money saved by caching, cost per 1,000 tokens, slow-call times (p50, p95, p99), error rate, and blocked or downgraded calls.
3. **Budgets & controls:** usage bars against each limit, alerts, settings per task, and an emergency "cheapest model only" switch.
4. **Recent calls:** the latest calls (details only, no message text).
5. **Streaming playground:** try a call and watch the reply stream in.

**Speed tricks (data structures and algorithms):**

| Problem | Method | Result |
|---|---|---|
| Summing thousands of calls on every refresh | Each call adds itself to an **hourly summary table** (each call updates one row) | 30-day summary in about 15 ms |
| Slow-call percentiles need sorting | **Histogram with log-scale buckets**: one pass, no sort | Within 5% of the exact value (tested) |
| "Top 12 biggest consumers" | **Heap** (`heapq.nlargest`) | Doesn't sort the whole list |
| Looking up saved answers | **Dictionary keyed by a hash of the request** | Instant lookup, no repeat cost |

## 4. How to run it (VS Code)

1. **File → Open Folder** → choose `ai-tech-consultant`.
2. Press **F5** and choose:
   - **Dashboard: demo data**: made-up usage so you can explore the screens (a yellow banner says it is demo data).
   - **Dashboard: token & cost (real usage)**: your real calls.
   - **Tests: all (offline, free)**.
3. Or press **Ctrl+Shift+B** to run the tests.

From a terminal, inside this folder:

```
python -m streamlit run dashboard/app.py
python -m unittest discover -s tests -t . -v
```

## 5. Checked against the brief

| Brief requirement | Where we are |
|---|---|
| Task 1 Python, Task 2 TypeScript (or Python), Task 3 Python | Not started. Node.js isn't installed, so Task 2 will probably be Python. The brief allows this, but students who use both languages get 10% extra. |
| Tasks 2 and 3 call Task 1 through its API | Planned. The gateway and folders are ready for it. |
| Claude at the center, at least 2 models compared with 20–30 evals each | **Conflict with the $0 goal.** Claude calls cost money, so at $0 we can compare offline and Ollama only. EVALS.md must say this honestly. |
| Evals for quality, cost and speed | Cost and speed tracking is built (dashboard). The quality eval cases are not written yet. |
| Retries, human approval, safe handling of bad input | Budget blocks and logged errors are done. Guardrails and approval screens are planned. |
| Simple screens | Dashboard done. Chat, inbox and scope screens planned. |
| One-command run, tests, commits over 4 days | Tests and VS Code run are done. No git repository yet. |

## 6. What is next (in order)

1. `git init` and a first commit.
2. Task 1: crawl 10–20 help articles politely, parse them, chunk them with spaCy, search with free local embeddings, and serve answers through a FastAPI API.
3. A guardrails module: prompt-injection checks, removing personal data from logs, and checking that every citation is real.
4. Task 2 (Inbox Agent) and Task 3 (Meeting to Scope), both calling the Task 1 API.
5. Write 20 or more eval cases per task, then DESIGN.md, EVALS.md and AI_LOG.md.

The full checklist is in [TODO.md](TODO.md).

## 7. Honest limits right now

- Only the shared plumbing exists. None of the three tasks is built yet.
- The dashboard has been viewed with **demo data** only. No real model call has run.
- Ollama mode is tested only against a simulated server, because Ollama isn't running on this machine.
- The Haiku 5.5 cache-read price is an assumption (a tenth of its input price). Check it before trusting Haiku cost numbers.
