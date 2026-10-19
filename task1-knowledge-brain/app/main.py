"""Knowledge Brain API.

    uvicorn app.main:app --port 8100          (run from task1-knowledge-brain/)
    Open http://localhost:8100/docs for the interactive API documentation.
"""
import asyncio
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE.parent)]

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, UploadFile  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402

import settings  # noqa: E402
from app.answer import Brain  # noqa: E402
from app.manual import ManualKnowledge  # noqa: E402
from app.schemas import (ArticleEdit, Escalation, EscalationCreate, EscalationResolve, ErrorResponse,  # noqa: E402
                         IngestionRunRequest, QueryRequest, QueryResponse, ReviewDecision)
from shared import guardrails  # noqa: E402
from ingestion import pipeline  # noqa: E402
from ingestion.store import KBStore  # noqa: E402

@asynccontextmanager
async def lifespan(_app):
    task = asyncio.create_task(daily_refresh_loop()) if settings.DAILY_REFRESH else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="Knowledge Brain API", version="0.1.0", lifespan=lifespan,
              description="Answers product questions from the Brilliant Directories help center, with sources. "
                          "Says so when it does not know.")
store = KBStore(settings.DB_PATH)
_brain: Brain | None = None
_ingest_lock = asyncio.Lock()


def brain() -> Brain:
    global _brain
    if _brain is None:
        _brain = Brain(store)
    return _brain


def auth(authorization: str | None = Header(None)):
    if settings.API_TOKEN and authorization != f"Bearer {settings.API_TOKEN}":
        raise HTTPException(401, "missing or wrong bearer token")


def manual() -> ManualKnowledge:
    return ManualKnowledge(store)


@app.post("/articles", status_code=201, dependencies=[Depends(auth)])
async def add_article(title: str = Form(..., min_length=3, max_length=300), body: str = Form(""),
                      section: str = Form(""), tags: str = Form(""), image_description: str = Form(""),
                      author: str = Form(""), files: list[UploadFile] = File(default=[])):
    """Add knowledge by hand: a form plus up to 5 files (.jpg .png .pdf .docx .txt .md, 10 MB each).
    Text is extracted and indexed, but the article stays PENDING (not used in answers) until a human
    approves it with POST /articles/{id}/approve."""
    check = guardrails.check_input(title + body + image_description, max_chars=200_000)
    if not check.ok:
        raise HTTPException(422, "; ".join(check.issues))
    data = [(f.filename or "file", await f.read()) for f in files if f.filename]
    try:
        result = await asyncio.to_thread(manual().create, title, body, section, tags, image_description, author, data)
    except ValueError as e:
        raise HTTPException(422, str(e))
    brain().reload()
    return result


@app.get("/articles", dependencies=[Depends(auth)])
def list_articles(source: str = "all", q: str = "", include_removed: bool = True, limit: int = 200,
                  status: str | None = None):
    """source: all | manual | crawled | expert. status: pending | approved | rejected. `q` filters by title."""
    if source not in ("all", "manual", "crawled", "expert"):
        raise HTTPException(422, "source must be all, manual, crawled or expert")
    if status not in (None, "pending", "approved", "rejected"):
        raise HTTPException(422, "status must be pending, approved or rejected")
    return manual().list(source, q, include_removed, min(limit, 2000), status)


@app.get("/articles/{article_id}", dependencies=[Depends(auth)])
def get_article(article_id: str):
    """Full article, including `editable_text` (the format PUT and approve accept)."""
    try:
        return manual().get(article_id)
    except KeyError:
        raise HTTPException(404, f"article {article_id} not found")


@app.put("/articles/{article_id}", dependencies=[Depends(auth)])
async def edit_article(article_id: str, req: ArticleEdit):
    """Correct any article. The old version is kept; crawls never overwrite a human edit."""
    try:
        art = await asyncio.to_thread(manual().edit, article_id, req.title, req.text, req.editor)
    except KeyError:
        raise HTTPException(404, f"article {article_id} not found")
    except ValueError as e:
        raise HTTPException(422, str(e))
    brain().reload()
    return art


@app.post("/articles/{article_id}/approve", dependencies=[Depends(auth)])
async def approve_article(article_id: str, req: ReviewDecision):
    """Move a pending article into the knowledge base (optionally with corrections)."""
    try:
        art = await asyncio.to_thread(manual().approve, article_id, req.reviewer, req.title, req.text)
    except KeyError:
        raise HTTPException(404, f"article {article_id} not found")
    except ValueError as e:
        raise HTTPException(409, str(e))
    brain().reload()
    return art


@app.post("/articles/{article_id}/reject", dependencies=[Depends(auth)])
def reject_article(article_id: str, req: ReviewDecision):
    try:
        manual().reject(article_id, req.reviewer)
    except KeyError:
        raise HTTPException(404, f"article {article_id} not found")
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"article_id": article_id, "review_status": "rejected"}


@app.delete("/articles/{article_id}", dependencies=[Depends(auth)])
def remove_article(article_id: str):
    """Hide an article from answers. It is kept and can be restored."""
    try:
        manual().remove(article_id)
    except KeyError:
        raise HTTPException(404, f"article {article_id} not found")
    brain().reload()
    return {"article_id": article_id, "active": False}


@app.post("/articles/{article_id}/restore", dependencies=[Depends(auth)])
def restore_article(article_id: str):
    try:
        manual().restore(article_id)
    except KeyError:
        raise HTTPException(404, f"article {article_id} not found")
    brain().reload()
    return {"article_id": article_id, "active": True}


@app.get("/files/{article_id}/{name}")
def get_file(article_id: str, name: str):
    """Original uploaded files, so answers can show the image or document."""
    path = manual().file_path(article_id, name)
    if path is None:
        raise HTTPException(404, "file not found")
    return FileResponse(path)


@app.get("/health")
def health():
    s = store.status()
    return {"status": "ok" if s["active_chunks"] else "empty", "active_articles": s["active_articles"],
            "active_chunks": s["active_chunks"]}


@app.post("/query", response_model=QueryResponse, responses={401: {"model": ErrorResponse}},
          dependencies=[Depends(auth)])
async def query(req: QueryRequest) -> QueryResponse:
    """Retrieve evidence and answer. `status=insufficient_evidence` means: do not guess, ask a human.
    Such questions are queued automatically as escalations (see `escalation_id`)."""
    check = guardrails.check_input(req.question + (req.context or ""))
    if not check.ok:
        raise HTTPException(422, "; ".join(check.issues))
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(brain().query, req.question, req.top_k, req.context, req.escalate,
                              None, req.source), timeout=60)
    except asyncio.TimeoutError:
        raise HTTPException(504, "answering took longer than 60 s")
    return QueryResponse(**result)


@app.post("/escalations", response_model=Escalation, status_code=201, dependencies=[Depends(auth)])
def create_escalation(req: EscalationCreate):
    """Ask a human expert directly (e.g. the Inbox Agent or Meeting-to-Scope found a gap)."""
    return brain().escalations.create(req.question, req.reason, [], req.source)


@app.get("/escalations", response_model=list[Escalation], dependencies=[Depends(auth)])
def list_escalations(status: str | None = None, limit: int = 100):
    return brain().escalations.list(status, min(limit, 500))


@app.post("/escalations/{eid}/resolve", response_model=Escalation, dependencies=[Depends(auth)],
          responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}})
def resolve_escalation(eid: int, req: EscalationResolve):
    """Record the expert's answer. scope=general adds it to memory (searchable at once); customer keeps it on record."""
    try:
        esc = brain().escalations.resolve(eid, req.answer, req.expert, req.scope, req.expires_at)
    except KeyError:
        raise HTTPException(404, f"escalation {eid} not found")
    except ValueError as e:
        raise HTTPException(409, str(e))
    brain().reload()
    return esc


_schedule = {"next_run": None, "last_result": None}


def other_run_in_progress() -> bool:
    """A crawl started elsewhere (e.g. `python run.py ingest` in another window) and not finished yet."""
    last = store.status()["last_run"]
    return bool(last and not last["finished_at"]
                and time.time() - last["started_at"] < settings.RUN_STALE_HOURS * 3600)


async def run_ingestion(limit: int | None = None) -> dict:
    async with _ingest_lock:
        report = await pipeline.run(store, limit)
        brain().escalations.expire()
        brain().reload()
        return report


def next_run_time(now: datetime, at: str) -> datetime:
    hh, mm = (int(x) for x in at.split(":"))
    nxt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return nxt if nxt > now else nxt + timedelta(days=1)


async def daily_refresh_loop():
    """Check the sitemap once a day at REFRESH_AT: add new pages, re-read changed ones, hide removed ones."""
    while True:
        nxt = next_run_time(datetime.now(), settings.REFRESH_AT)
        _schedule["next_run"] = nxt.isoformat(timespec="minutes")
        await asyncio.sleep(max(1.0, (nxt - datetime.now()).total_seconds()))
        if _ingest_lock.locked() or other_run_in_progress():
            _schedule["last_result"] = {"at": datetime.now().isoformat(timespec="minutes"),
                                        "skipped": "another ingestion run is in progress"}
            continue
        try:
            report = await run_ingestion(None)
            _schedule["last_result"] = {"at": datetime.now().isoformat(timespec="minutes"), "report": report}
        except Exception as e:  # never let a failed refresh stop the API; it retries tomorrow
            _schedule["last_result"] = {"at": datetime.now().isoformat(timespec="minutes"),
                                        "error": f"{type(e).__name__}: {e}"[:300]}


@app.get("/ingestion/status", dependencies=[Depends(auth)])
def ingestion_status():
    return {**store.status(), "running": _ingest_lock.locked() or other_run_in_progress(),
            "daily_refresh": {"enabled": settings.DAILY_REFRESH, "at": settings.REFRESH_AT, **_schedule}}


@app.post("/ingestion/run", status_code=202, dependencies=[Depends(auth)])
async def ingestion_run(req: IngestionRunRequest, background: BackgroundTasks):
    """Start a polite crawl in the background. Poll /ingestion/status for progress."""
    if _ingest_lock.locked() or other_run_in_progress():
        raise HTTPException(409, "an ingestion run is already in progress")
    background.add_task(run_ingestion, req.limit)
    return {"accepted": True, "limit": req.limit}
