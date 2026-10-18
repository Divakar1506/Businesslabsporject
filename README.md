# AI Tech Consultant (Business Labs Projects)

One shared **Knowledge Brain** (Task 1) that answers product questions from the Brilliant Directories help center with sources, used **only through its API** by an **Inbox Agent** (Task 2) that drafts customer replies, and by **Meeting to Scope** (Task 3) that turns client meetings into a scope, estimate and proposal. Everything runs **offline at $0** by default. Plan: [TODO.md](TODO.md) · Results: [EVALS.md](EVALS.md) · Overview: [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md)

## Prerequisites
Python 3.11+ (tested on 3.13) with `fastapi uvicorn httpx pydantic streamlit altair pandas numpy tokenizers spacy`. Embeddings use the locally cached `BAAI/bge-small-en-v1.5` (no download, no PyTorch). Copy `.env.example` to `.env`; it works as is.

## Run it from the VS Code terminal
Open this folder in VS Code, then **Terminal → New Terminal**:

```
python run.py ingest --limit 20     # first time only: read 20 help-center articles (~1 min, polite)
python run.py all                   # API + all screens + cost dashboard. Ctrl+C stops everything
```

| What | URL | Try this |
|---|---|---|
| Task 1 Knowledge Brain API | http://localhost:8100/docs | `POST /query` |
| Task 1 chat + expert queue | http://localhost:8501 | ask "How do I clone a post type?", then a question it can't answer |
| Task 2 inbox review | http://localhost:8502 | sidebar → *Load the 56 sample emails* → review, edit, send |
| Task 3 scope builder | http://localhost:8503 | sidebar → *Load sample meetings A and B* → approve v1 → *New meeting*: follow-up of meeting_a with `meeting_c_followup.txt` |
| Token & cost dashboard | http://localhost:8600 | spend, tokens, budgets, $0 cap |

Other commands: `python run.py test` (all 76 tests, $0) · `python run.py eval` (all evaluations) · `python run.py api|chat|inbox|scope|dashboard` (one service) · `python run.py ingest` (all articles, resumes) · `python run.py help`.
The same commands are under **Terminal → Run Task…**, and **Ctrl+Shift+B** runs the tests. F5 entries are in `.vscode/launch.json`.

## Cost: $0 by default
`LLM_MODE=offline` (default): answers are quoted from the help center, and drafts come from versioned templates. `LLM_MODE=ollama` uses a free local model. Paid Claude calls need `LLM_MODE=claude` **and** `HARD_MONTHLY_CAP_USD` above 0; the cap is **0** by default, so every paid call is blocked before it is sent. **Limitation:** the brief wants Claude at the center with measured model comparisons. At $0 those Claude numbers are not produced, and EVALS.md says so.

## Layout
`task1-knowledge-brain/` crawl, parse, chunk, embed, search, API, escalations, chat · `task2-inbox-agent/` classify, KB client, drafts, review, 20-in-a-row autonomy, learning · `task3-meeting-to-scope/` 4 transcripts, 5 agents, change report, approval · `shared/` LLM gateway (budgets, streaming, usage log) and guardrails · `dashboard/` token and cost dashboard.
