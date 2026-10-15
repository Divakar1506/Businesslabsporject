"""Knowledge Brain API.

    uvicorn app.main:app --port 8100          (run from task1-knowledge-brain/)
    Open http://localhost:8100/docs for the interactive API documentation.
"""
import asyncio
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE.parent)]

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException  # noqa: E402

import settings  # noqa: E402
from app.answer import Brain  # noqa: E402
from app.schemas import ErrorResponse, IngestionRunRequest, QueryRequest, QueryResponse  # noqa: E402
from ingestion import pipeline  # noqa: E402
from ingestion.store import KBStore  # noqa: E402

app = FastAPI(title="Knowledge Brain API", version="0.1.0",
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


@app.get("/health")
def health():
    s = store.status()
    return {"status": "ok" if s["active_chunks"] else "empty", "active_articles": s["active_articles"],
            "active_chunks": s["active_chunks"]}


@app.post("/query", response_model=QueryResponse, responses={401: {"model": ErrorResponse}},
          dependencies=[Depends(auth)])
async def query(req: QueryRequest) -> QueryResponse:
    """Retrieve evidence and answer. `status=insufficient_evidence` means: do not guess, ask a human."""
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(brain().query, req.question, req.top_k, req.context), timeout=60)
    except asyncio.TimeoutError:
        raise HTTPException(504, "answering took longer than 60 s")
    return QueryResponse(**result)


@app.get("/ingestion/status", dependencies=[Depends(auth)])
def ingestion_status():
    return {**store.status(), "running": _ingest_lock.locked()}


@app.post("/ingestion/run", status_code=202, dependencies=[Depends(auth)])
async def ingestion_run(req: IngestionRunRequest, background: BackgroundTasks):
    """Start a polite crawl in the background. Poll /ingestion/status for progress."""
    if _ingest_lock.locked():
        raise HTTPException(409, "an ingestion run is already in progress")

    async def job():
        async with _ingest_lock:
            await pipeline.run(store, req.limit)
            brain().reload()

    background.add_task(job)
    return {"accepted": True, "limit": req.limit}
