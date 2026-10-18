"""Agent 5 - Proposal and Review. Input: scope + estimate (+ change report). Output: client-ready
markdown and a list of review issues. A version with blocker issues cannot be approved.

Review checks (consistency between the agents' outputs):
- every deliverable has an estimate, and nothing unclear or removed is promised
- custom work is never presented as an existing feature
- no unsupported promises (guarantees, free work, refunds) - prices only from the estimate table
"""
from shared import guardrails

from core.models import ChangeReport, Estimate, ScopeDoc


def _money(hours: float, est: Estimate) -> str:
    return f"{est.currency} {hours * est.rate:,.0f}"


def build(project: str, client: str, scope: ScopeDoc, est: Estimate, version: int,
          changes: ChangeReport | None = None) -> tuple[str, list[dict]]:
    by_req: dict[str, list] = {}
    for t in est.tasks:
        by_req.setdefault(t.req_id, []).append(t)
    L = [f"# Proposal: {project}", f"*Prepared for {client} · version {version} · DRAFT until approved*", "",
         "## Executive summary", scope.objective, "",
         f"**Estimated effort:** {est.total_low:.0f}–{est.total_high:.0f} hours (most likely {est.total_likely:.0f}, "
         f"expected {est.expected:.0f}). **Indicative price:** {_money(est.total_low, est)}–{_money(est.total_high, est)} "
         f"at {est.currency} {est.rate:.0f}/hour, excluding taxes.", "",
         "## The problem", scope.problem, "", "## Proposed solution"]
    L += [f"- {s}" for s in scope.solution] + ["", "## Scope and deliverables"]
    L += [f"- {d}" for d in scope.deliverables]
    if changes:
        L += ["", f"## Changes since approved version {changes.baseline_version}"]
        for c in changes.changes:
            if c.kind != "unchanged":
                tag = " — **outside the agreed scope: quoted separately**" if c.out_of_scope else ""
                L.append(f"- **{c.kind.title()}** {c.req_id} {c.title}{tag}" + (f": {c.after}" if c.after else ""))
        if changes.deadline_before != changes.deadline_after:
            L.append(f"- **Deadline** moved from {changes.deadline_before} to {changes.deadline_after}")
        L.append(f"- Agreed scope, likely effort: {changes.hours_before:.0f} h → {changes.hours_after:.0f} h")
        if changes.hours_change_requests:
            L.append(f"- **Change requests (new items), quoted separately:** about {changes.hours_change_requests:.0f} h "
                     f"(≈ {_money(changes.hours_change_requests, est)}). They are included in the totals below; "
                     f"approve or remove each one before work starts")
    L += ["", "## Estimate", "| Deliverable | Task | Low | Likely | High |", "|---|---|---|---|---|"]
    for rid, ts in by_req.items():
        for t in ts:
            L.append(f"| {rid} | {t.task} | {t.low:g} | {t.likely:g} | {t.high:g} |")
    L.append(f"| **Total** | incl. 10% project management | **{est.total_low:g}** | **{est.total_likely:g}** | **{est.total_high:g}** |")
    L += ["", "## Timeline and milestones",
          f"- Target go-live: **{scope.deadline or 'to be agreed'}** (to be confirmed after scope approval)",
          "- Milestone 1: configuration items · Milestone 2: custom items · Milestone 3: acceptance testing", "",
          "## Acceptance criteria"] + [f"- {a}" for a in scope.acceptance]
    L += ["", "## Your responsibilities"] + [f"- {r}" for r in scope.client_responsibilities]
    L += ["", "## Not included"] + [f"- {e}" for e in scope.exclusions]
    L += ["", "## Assumptions"] + [f"- {a}" for a in scope.assumptions + est.notes]
    L += ["", "## Open questions and risks"] + ([f"- {q}" for q in scope.open_questions] or ["- None"])
    L += ["", "## Approval and next steps",
          "This proposal is an estimate, not a fixed quote. Work starts after you approve this version in writing."]
    md = "\n".join(L)
    return md, review(scope, est, md)


def review(scope: ScopeDoc, est: Estimate, md: str) -> list[dict]:
    issues = []
    estimated = {t.req_id for t in est.tasks}
    for r in scope.requirements:
        in_deliverables = any(d.startswith(r.id + " ") for d in scope.deliverables)
        if in_deliverables and r.id not in estimated:
            issues.append({"severity": "blocker", "issue": f"{r.id} is a deliverable without an estimate"})
        if in_deliverables and r.status != "confirmed":
            issues.append({"severity": "blocker", "issue": f"{r.id} is {r.status} but listed as a deliverable"})
        if in_deliverables and r.feasibility and r.feasibility.cls == "custom" and \
                not any(s.startswith(r.id) and "custom development" in s for s in scope.solution) and \
                "Service work" not in r.feasibility.note:
            issues.append({"severity": "blocker", "issue": f"{r.id} is custom work but not labelled as such"})
    bad = [i for i in guardrails.check_commitments(md).issues if i not in ("price", "delivery date")]
    for i in bad:
        issues.append({"severity": "blocker", "issue": f"proposal contains an unsupported promise: {i}"})
    if not scope.deliverables:
        issues.append({"severity": "warning", "issue": "no confirmed deliverables"})
    if scope.open_questions:
        issues.append({"severity": "warning", "issue": f"{len(scope.open_questions)} open question(s) to resolve with the client"})
    return issues
