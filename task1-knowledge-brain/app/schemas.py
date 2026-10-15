"""Typed API contract for the Knowledge Brain. Tasks 2 and 3 code against these shapes only."""
from typing import Literal

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000, description="The customer's or agent's question")
    top_k: int = Field(5, ge=1, le=20, description="How many evidence chunks to retrieve")
    context: str | None = Field(None, max_length=4000, description="Optional extra context, e.g. the email body")


class Source(BaseModel):
    title: str
    url: str
    section: str = Field(description="Heading of the section the evidence came from ('' = intro)")
    score: float = Field(description="Cosine similarity of the best chunk from this article, 0-1")
    kind: Literal["article", "expert"] = Field("article", description="expert = an approved human expert answer")


class ImageRef(BaseModel):
    src: str
    article_url: str
    section: str


class QueryResponse(BaseModel):
    answer: str
    status: Literal["answered", "insufficient_evidence"]
    needs_human: bool = Field(description="True when a human expert should answer instead")
    sources: list[Source]
    images: list[ImageRef] = Field(description="Screenshots from the cited sections")
    retrieval_score: float = Field(description="Best cosine similarity found. Not a calibrated probability.")
    answer_mode: Literal["llm", "extractive", "none"] = Field(
        description="llm = written by the model; extractive = quoted from the evidence (offline, $0); none = no answer")
    model: str | None = None
    request_id: str
    latency_ms: float
    flags: list[str] = Field(default_factory=list, description="Guardrail findings (injection attempts, rejected output)")
    escalation_id: int | None = Field(None, description="Set when the question was sent to a human expert")


class EscalationCreate(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    reason: str = Field("raised manually", max_length=500)
    source: Literal["manual", "inbox", "scope", "query"] = "manual"


class EscalationResolve(BaseModel):
    answer: str = Field(min_length=3, max_length=8000)
    expert: str = Field(min_length=1, max_length=100)
    scope: Literal["general", "customer"] = Field(
        "general", description="general = add to memory for everyone; customer = keep on record only")
    expires_at: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="YYYY-MM-DD; omit = no expiry")


class Escalation(BaseModel):
    id: int
    created_at: float
    question: str
    reason: str
    evidence: list[dict]
    source: str
    status: str
    expert: str | None = None
    answer: str | None = None
    scope: str | None = None
    expires_at: str | None = None
    resolved_at: float | None = None
    article_id: str | None = None


class IngestionRunRequest(BaseModel):
    limit: int | None = Field(None, ge=1, description="Max articles to track; omit for the whole sitemap")


class ErrorResponse(BaseModel):
    detail: str
