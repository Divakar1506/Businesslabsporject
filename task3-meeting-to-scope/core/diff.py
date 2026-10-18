"""Compare a follow-up meeting with the APPROVED baseline and merge the result.

Matching strategy: requirements are matched by their stable topic ID (REQ-<TOPIC>), so "the
filters" in meeting A and "category, rating and distance filters" in meeting C are the same record.
- removed   : the follow-up removes a baseline requirement
- modified  : the client restates a baseline requirement differently (new description or status)
- added     : a requirement not in the baseline -> always flagged as outside the agreed scope,
              to be estimated and quoted separately
- unchanged : only re-confirmed, or not mentioned
"""
from core.models import Change, ChangeReport, MeetingFacts, Requirement


def compare(baseline_reqs: list[Requirement], baseline_deadline: str | None, baseline_hours: float,
            baseline_version: int, follow: MeetingFacts) -> tuple[list[Requirement], list[Change], list[Requirement]]:
    base = {r.id: r for r in baseline_reqs}
    new = {r.id: r for r in follow.requirements}
    removed_now = {r.id: r for r in follow.out_of_scope}
    changes, merged, newly_excluded = [], [], []

    for rid, b in base.items():
        if rid in removed_now:
            src = removed_now[rid].sources[-1]
            changes.append(Change(kind="removed", req_id=rid, title=b.title, before=b.description, source=src))
            newly_excluded.append(removed_now[rid])
            continue
        n = new.get(rid)
        if n and n.explicit and (n.description != b.description or n.status != b.status):
            m = b.model_copy(deep=True)
            m.description, m.status = n.description, n.status
            m.sources = b.sources + n.sources
            if n.priority != "should":
                m.priority = n.priority
            m.feasibility, m.estimate_status = None, "needs_review"
            changes.append(Change(kind="modified", req_id=rid, title=b.title,
                                  before=f"[{b.status}] {b.description}", after=f"[{n.status}] {n.description}",
                                  source=n.sources[-1]))
            merged.append(m)
        else:
            changes.append(Change(kind="unchanged", req_id=rid, title=b.title))
            merged.append(b)

    for rid, n in new.items():
        if rid not in base:
            a = n.model_copy(deep=True)
            changes.append(Change(kind="added", req_id=rid, title=n.title, after=n.description, out_of_scope=True,
                                  source=n.sources[-1], note="Not in the approved baseline: estimate and quote separately."))
            merged.append(a)
    for rid, r in removed_now.items():
        if rid not in base:
            newly_excluded.append(r)
    return merged, changes, newly_excluded


def report(changes: list[Change], baseline_version: int, meeting_id: str, deadline_before: str | None,
           deadline_after: str | None, hours_before: float, hours_after: float,
           hours_change_requests: float = 0.0) -> ChangeReport:
    return ChangeReport(baseline_version=baseline_version, meeting_id=meeting_id, changes=changes,
                        deadline_before=deadline_before, deadline_after=deadline_after or deadline_before,
                        hours_before=hours_before, hours_after=hours_after,
                        hours_change_requests=hours_change_requests)
