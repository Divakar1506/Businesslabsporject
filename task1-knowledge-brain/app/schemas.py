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


class IngestionRunRequest(BaseModel):
    limit: int | None = Field(None, ge=1, description="Max articles to track; omit for the whole sitemap")


class ErrorResponse(BaseModel):
    detail: str
