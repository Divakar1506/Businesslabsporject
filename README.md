# AI Tech Consultant (Business Labs Projects)

Three connected AI systems built around one shared Knowledge Brain API: Task 1 Knowledge Brain, Task 2 Inbox Agent, Task 3 Meeting to Scope. The full work plan is in [TODO.md](TODO.md).

**Built so far:** the shared LLM gateway (`shared/llm_gateway`), the token & cost dashboard (`dashboard/app.py`) and the core of **Task 1** (crawl → parse → spaCy chunks → local embeddings → hybrid search → `/query` API). **Tasks 2 and 3 are not built yet.**

## Run each task
| Task | Status | How to run (VS Code: F5 and pick the entry) |
|---|---|---|
| Task 1 – Knowledge Brain | Core works | 1. **Task 1: Ingest 20 articles** (first time only) → 2. **Task 1: Knowledge Brain API** → open http://localhost:8100/docs and try `POST /query` |
| Task 2 – Inbox Agent | Not built | — |
| Task 3 – Meeting to Scope | Not built | — |

Terminal equivalent for Task 1 (from `task1-knowledge-brain/`):
```
python run_ingest.py --limit 20
python -m uvicorn app.main:app --port 8100
python -m unittest discover -s tests -t . -v
```
In offline mode (default, $0) answers are **extractive**: the best matching sentences from the help center, quoted with sources. Questions the help center does not cover return `status: insufficient_evidence` and `needs_human: true`.

## Cost: $0 by default
- `LLM_MODE=offline` is the default: answers come from `data/replay.jsonl` (recorded earlier) or a clearly marked placeholder. No network, no cost. All tests run this way.
- `LLM_MODE=ollama` uses a local model through Ollama, also $0.
- Paid Claude calls need `LLM_MODE=claude` **and** `HARD_MONTHLY_CAP_USD` above 0. It is **0 by default**, so every paid call is blocked before it reaches the API.
- Limitation: the brief asks for Claude at the center with measured model comparisons. At $0 those Claude numbers cannot be produced; EVALS.md must say so.

## Prerequisites
- Python 3.11+ (tested on 3.13) with `streamlit`, `altair`, `pandas`; `spacy` optional (used for free token estimates, no model download)
- Copy `.env.example` to `.env` (works as is, in offline mode)
- Optional: `pip install anthropic` + `ANTHROPIC_API_KEY` for claude mode; Ollama for ollama mode

## Run in VS Code
1. **File → Open Folder…** and choose this `ai-tech-consultant` folder (not its parent).
2. Install the **Python** extension (it brings the debugger) and pick your interpreter.
3. Press **F5**, then choose:
   - **Dashboard: demo data** — seeds synthetic usage, then opens the dashboard on http://localhost:8601
   - **Dashboard: token & cost (real usage)** — reads `data/usage.db`, http://localhost:8600
   - **Tests: all (offline, free)**
4. Or **Ctrl+Shift+B** runs the tests, and **Terminal → Run Task…** lists all tasks. Tests also appear in the Testing sidebar.

## Run from a terminal
```
python -m streamlit run dashboard/app.py
python -m unittest discover -s tests -t . -v
```

## Using the gateway
```python
from shared.llm_gateway import Gateway, BudgetExceeded
gw = Gateway()
r = gw.call(task="knowledge", endpoint="query", system=SYSTEM, messages=[{"role": "user", "content": q}])
for chunk in gw.stream(task="inbox", endpoint="draft", messages=msgs):  # streaming, faster first words
    print(chunk, end="")
```
Every call is logged (tokens, cost, latency, model, prompt version — never the prompt text). Budgets and per-task models are set on the dashboard's **Budgets & controls** tab. A call that would break a hard limit raises `BudgetExceeded`; near a limit, calls switch to the cheapest model.
