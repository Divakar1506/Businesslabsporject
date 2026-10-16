"""Typed data for the Inbox Agent. Every draft is validated against Draft before it is stored."""
from enum import Enum

from pydantic import BaseModel, Field


class Category(str, Enum):
    HOW_TO = "how_to"
    TROUBLESHOOTING = "troubleshooting"
    PROJECT_REQUEST = "project_request"
    REFUND_CANCEL = "refund_cancel"
    COMPLAINT = "complaint"
    QUOTE = "quote"
    FOLLOW_UP = "follow_up"
    OTHER = "other"


# These never go out without a human, whatever the approval streak says (money, reputation, commitments).
NEVER_AUTONOMOUS = {Category.REFUND_CANCEL, Category.COMPLAINT, Category.QUOTE, Category.PROJECT_REQUEST,
                    Category.OTHER}
# Categories where product knowledge answers the email, so the Knowledge Brain is asked.
KNOWLEDGE_CATEGORIES = {Category.HOW_TO, Category.TROUBLESHOOTING}


class Email(BaseModel):
    id: str
    from_addr: str = Field(alias="from")
    subject: str = Field(max_length=500)
    body: str = Field(max_length=20000)
    thread_id: str | None = None
    received_at: str | None = None

    model_config = {"populate_by_name": True}


class Source(BaseModel):
    title: str
    url: str
    score: float


class Draft(BaseModel):
    email_id: str
    category: Category
    category_confidence: float = Field(ge=0, le=1)
    urgency: str = Field(pattern="^(low|normal|high)$")
    risk: str = Field(pattern="^(low|medium|high)$")
    summary: str
    reply: str
    sources: list[Source] = []
    kb_status: str = Field(description="answered | insufficient_evidence | unavailable | not_needed")
    needs_approval: bool = True
    approval_reasons: list[str] = []
    next_action: str
    escalation_id: int | None = None
    flags: list[str] = []
    prompt_version: str
    model: str
