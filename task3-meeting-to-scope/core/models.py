"""Typed records passed between the agents. Each agent's output is validated by these models."""
from typing import Literal

from pydantic import BaseModel, Field

Status = Literal["confirmed", "unclear", "disputed", "removed"]
Priority = Literal["must", "should", "optional"]
FeasClass = Literal["existing_config", "existing_limited", "custom", "needs_clarification"]


class Turn(BaseModel):
    ts: str
    speaker: str
    role: Literal["client", "consultant", "other"]
    text: str
    line: int


class SourceRef(BaseModel):
    meeting_id: str
    ts: str
    speaker: str
    quote: str = Field(description="Exact words from the transcript, for a reviewer to verify")


class Evidence(BaseModel):
    title: str
    url: str
    score: float


class Feasibility(BaseModel):
    cls: FeasClass
    evidence: list[Evidence] = []
    note: str


class Requirement(BaseModel):
    id: str = Field(description="Stable ID from the topic, e.g. REQ-FILTERS. Used to match across meetings.")
    topic: str
    title: str
    description: str
    status: Status
    priority: Priority
    explicit: bool = Field(description="True if the client asked for it; False if inferred or suggested by us")
    sources: list[SourceRef]
    acceptance_criteria: list[str] = []
    dependencies: list[str] = []
    feasibility: Feasibility | None = None
    estimate_status: Literal["not_estimated", "estimated", "needs_review"] = "not_estimated"


class MeetingFacts(BaseModel):
    meeting_id: str
    requirements: list[Requirement]
    deadline: str | None = None
    deadline_source: SourceRef | None = None
    budget: str | None = None
    out_of_scope: list[Requirement] = []
    client_responsibilities: list[str] = []
    open_questions: list[str] = []
    consultant_commitments: list[str] = []
    flags: list[str] = []


class TaskEstimate(BaseModel):
    req_id: str
    task: str
    low: float
    likely: float
    high: float
    assumption: str = ""


class Estimate(BaseModel):
    tasks: list[TaskEstimate]
    total_low: float
    total_likely: float
    total_high: float
    expected: float = Field(description="PERT (low + 4 x likely + high) / 6")
    rate: float
    currency: str
    not_estimated: list[str] = Field(description="Requirement IDs left out because they are unclear")
    notes: list[str] = []


class ScopeDoc(BaseModel):
    objective: str
    problem: str
    requirements: list[Requirement]
    solution: list[str]
    deliverables: list[str]
    exclusions: list[str]
    assumptions: list[str]
    acceptance: list[str]
    client_responsibilities: list[str]
    open_questions: list[str]
    deadline: str | None
    budget: str | None


class Change(BaseModel):
    kind: Literal["added", "removed", "modified", "unchanged"]
    req_id: str
    title: str
    before: str | None = None
    after: str | None = None
    out_of_scope: bool = False
    source: SourceRef | None = None
    note: str = ""


class ChangeReport(BaseModel):
    baseline_version: int
    meeting_id: str
    changes: list[Change]
    deadline_before: str | None
    deadline_after: str | None
    hours_before: float
    hours_after: float
    hours_change_requests: float = Field(0, description="Likely hours of added items, quoted separately")
