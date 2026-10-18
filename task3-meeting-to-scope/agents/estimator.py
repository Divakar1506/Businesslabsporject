"""Agent 4 - Estimation. Input: requirements with feasibility. Output: Estimate (validated).

Each deliverable is broken into tasks from a task library chosen by its feasibility class and
profile. Every task has low / likely / high hours; totals add a 10% project-management line and a
PERT expected value. Unclear requirements are not estimated - they are listed for clarification.
Rate and currency come from settings, so hours never turn into an approved price by themselves.
"""
from core.models import Estimate, Requirement, TaskEstimate
from core.topics import BY_KEY

LIBRARY = {
    "existing_config": [("Configure the existing feature", 0.5, 1, 2), ("Test against the acceptance criteria", 0.5, 1, 1.5),
                        ("Walk the client through it", 0.25, 0.5, 1)],
    "existing_limited": [("Configure the existing feature", 0.5, 1, 2), ("Custom adjustment for the gap", 2, 4, 8),
                         ("Test against the acceptance criteria", 1, 1.5, 3)],
    "custom": [("Clarify requirement and technical design", 2, 3, 5), ("Backend changes", 4, 8, 14),
               ("Frontend changes", 3, 6, 10), ("Automated tests", 2, 3, 5), ("Acceptance testing and fixes", 1, 2, 4),
               ("Deploy and document", 1, 1.5, 3)],
    "integration": [("Third-party API investigation", 2, 4, 8)],
    "content": [("Content plan and first article (per month)", 3, 4, 6)],
    "design": [("Design concepts and revisions", 8, 14, 24), ("Build the approved design", 6, 10, 16)],
}
PM_SHARE = 0.10


def tasks_for(req: Requirement) -> list[TaskEstimate]:
    topic = BY_KEY.get(req.topic)
    profile = topic.profile if topic else "feature"
    cls = req.feasibility.cls if req.feasibility else "custom"
    if profile in ("content", "design"):
        rows = LIBRARY[profile]
        why = "service work"
    else:
        rows = list(LIBRARY[cls if cls in LIBRARY else "custom"])
        why = {"existing_config": "existing setting", "existing_limited": "existing feature with a gap",
               "custom": "custom development"}.get(cls, cls)
        if profile == "integration" and cls != "existing_config":
            rows = LIBRARY["integration"] + rows
            why += " + third-party dependency"
    assumption = f"{why}; " + (req.dependencies[0] if req.dependencies else "no external dependency stated")
    return [TaskEstimate(req_id=req.id, task=t, low=lo, likely=li, high=hi, assumption=assumption) for t, lo, li, hi in rows]


def run(reqs: list[Requirement], rate: float, currency: str) -> tuple[Estimate, list[Requirement]]:
    tasks, skipped, out = [], [], []
    for r in reqs:
        r = r.model_copy(deep=True)
        if r.status in ("unclear", "disputed") or (r.feasibility and r.feasibility.cls == "needs_clarification"):
            r.estimate_status = "not_estimated"
            skipped.append(r.id)
        elif r.status != "removed":
            tasks.extend(tasks_for(r))
            r.estimate_status = "estimated"
        out.append(r)
    lo, li, hi = (sum(getattr(t, k) for t in tasks) for k in ("low", "likely", "high"))
    if tasks:
        tasks.append(TaskEstimate(req_id="PROJECT", task="Project management and communication (10%)",
                                  low=round(lo * PM_SHARE, 1), likely=round(li * PM_SHARE, 1), high=round(hi * PM_SHARE, 1)))
        lo, li, hi = lo * (1 + PM_SHARE), li * (1 + PM_SHARE), hi * (1 + PM_SHARE)
    est = Estimate(tasks=tasks, total_low=round(lo, 1), total_likely=round(li, 1), total_high=round(hi, 1),
                   expected=round((lo + 4 * li + hi) / 6, 1), rate=rate, currency=currency, not_estimated=skipped,
                   notes=["Hours are estimates from a task library, not a commitment.",
                          "Items marked custom are subject to technical investigation."])
    return est, out
