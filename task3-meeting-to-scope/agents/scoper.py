"""Agent 3 - Scope. Input: meeting facts + requirements with feasibility. Output: ScopeDoc (10 sections).

Only confirmed requirements become deliverables. Unclear ones go to open questions, removed ones to
exclusions - uncertain statements are never silently turned into commitments.
"""
import re

from core.models import MeetingFacts, Requirement, ScopeDoc
from core.transcript import parse, sentences

PROBLEM = re.compile(r"complain|can'?t find|clunky|slow|dropping|\bmiss|pain|random|isn'?t great|wrong|still have to", re.I)
CLASS_LABEL = {"existing_config": "existing feature - configuration only",
               "existing_limited": "existing feature plus an adjustment",
               "custom": "custom development (subject to technical investigation)",
               "needs_clarification": "to be clarified"}


def problem_statements(text: str, limit: int = 4) -> list[str]:
    out = []
    for t in parse(text):
        if t.role == "client":
            out += [s for s in sentences(t.text) if PROBLEM.search(s)]
    return out[:limit]


def build(project: str, client: str, facts: MeetingFacts, reqs: list[Requirement], transcript_text: str,
          extra_exclusions: list[Requirement] | None = None) -> ScopeDoc:
    active = [r for r in reqs if r.status != "removed"]
    confirmed = [r for r in active if r.status == "confirmed" and r.estimate_status == "estimated"]
    unclear = [r for r in active if r not in confirmed]
    removed = [r for r in reqs if r.status == "removed"] + facts.out_of_scope + (extra_exclusions or [])
    problems = problem_statements(transcript_text)
    open_q = [f"{r.id} {r.title}: {r.feasibility.note if r.feasibility else 'needs clarification'}" for r in unclear]
    open_q += [f"{r.id} {r.title}: confirm with a product expert that no existing feature covers this"
               for r in confirmed if r.feasibility and r.feasibility.cls == "custom" and "Service work" not in r.feasibility.note]
    deps = [f"{r.id}: {d}" for r in confirmed for d in r.dependencies]
    seen_ex, exclusions = set(), []
    for r in removed:
        if r.id not in seen_ex:
            seen_ex.add(r.id)
            exclusions.append(f"{r.title} (client: \"{r.sources[-1].quote}\")")
    exclusions += [f"{r.title} - not included until the client confirms it" for r in unclear]
    return ScopeDoc(
        objective=f"Deliver the agreed improvements for {client} ({project})"
                  + (f" before {facts.deadline}." if facts.deadline else "."),
        problem=" ".join(problems) or "See the meeting transcript.",
        requirements=reqs,
        solution=[f"{r.id} {r.title}: " + ("service work, quoted separately" if r.feasibility and
                                            r.feasibility.note.startswith("Service work") else
                                            CLASS_LABEL.get(r.feasibility.cls if r.feasibility else "custom"))
                  for r in confirmed],
        deliverables=[f"{r.id} {r.title} — {r.description}" for r in confirmed],
        exclusions=exclusions,
        assumptions=["The client provides content, access and feedback within 2 business days.",
                     "Work is done on the current Brilliant Directories platform and theme.",
                     "Estimates assume the requirements as written; changes go through a change request."]
                    + [f"Dependency - {d}" for d in deps],
        acceptance=[f"{r.id}: {c}" for r in confirmed for c in r.acceptance_criteria],
        client_responsibilities=facts.client_responsibilities + ["Approve the scope before work starts.",
                                                                  "Test each deliverable against its acceptance criteria."],
        open_questions=open_q,
        deadline=facts.deadline, budget=facts.budget)
