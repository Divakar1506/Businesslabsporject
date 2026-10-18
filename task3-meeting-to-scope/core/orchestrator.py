"""Runs the agent team in order and stores the result as a DRAFT version.

initial meeting:   Analyst -> Feasibility -> Estimation -> Scope -> Proposal & Review
follow-up meeting: Analyst -> diff against the APPROVED baseline -> Feasibility (changed items only)
                   -> Estimation -> Scope -> Proposal & Review (with change report)
Each hand-off is a validated pydantic model, so a bad output stops the run at the stage that made it.
"""
import json
from pathlib import Path

import settings
from agents import analyst, estimator, feasibility, proposal, scoper
from core import diff
from core.kb_client import KBClient
from core.models import Requirement
from core.store import ApprovalError, ScopeStore


def load_sample_meetings() -> list[dict]:
    meta = json.loads((settings.TRANSCRIPTS / "meetings.json").read_text(encoding="utf-8"))
    for m in meta:
        m["text"] = (settings.TRANSCRIPTS / m["file"]).read_text(encoding="utf-8")
    return meta


def _package(project, client, meeting_id, facts, reqs, text, kb, version, extra_excl=None, change_report=None):
    est, reqs = estimator.run(reqs, settings.HOURLY_RATE, settings.CURRENCY)
    scope = scoper.build(project, client, facts, reqs, text, extra_excl)
    md, issues = proposal.build(project, client, scope, est, version, change_report)
    return {"meeting_id": meeting_id, "facts": facts.model_dump(mode="json"),
            "requirements": [r.model_dump(mode="json") for r in reqs], "scope": scope.model_dump(mode="json"),
            "estimate": est.model_dump(mode="json"), "proposal": md, "review_issues": issues,
            "change_report": change_report.model_dump(mode="json") if change_report else None}, est


def run_meeting(store: ScopeStore, kb: KBClient, project: str, client: str, meeting_id: str, date: str,
                text: str, follows: str | None = None) -> int:
    """Process one meeting and save a draft version. Returns the version number."""
    pid = store.project(project, client)
    store.add_meeting(meeting_id, pid, date, text, follows)
    facts = analyst.analyse(meeting_id, text, settings.SECTION_TOKENS)
    next_version = len(store.versions(pid)) + 1

    if not follows:
        reqs = feasibility.run(facts.requirements, kb)
        data, _ = _package(project, client, meeting_id, facts, reqs, text, kb, next_version)
        return store.save_version(pid, meeting_id, data)

    base = store.baseline(pid)
    if base is None:
        store.log(pid, None, "agents", "followup_refused", f"{meeting_id}: no approved baseline")
        raise ApprovalError("A follow-up is compared with the approved scope. Approve a version first.")
    bdata = base["data"]
    base_reqs = [Requirement(**r) for r in bdata["requirements"]]
    merged, changes, excluded = diff.compare(base_reqs, bdata["scope"]["deadline"], bdata["estimate"]["total_likely"],
                                             base["version"], facts)
    to_check = [r for r in merged if r.feasibility is None]
    checked = {r.id: r for r in feasibility.run(to_check, kb)}
    merged = [checked.get(r.id, r) for r in merged]
    deadline = facts.deadline or bdata["scope"]["deadline"]
    facts = facts.model_copy(update={"deadline": deadline, "budget": facts.budget or bdata["scope"]["budget"],
                                     "client_responsibilities": bdata["scope"]["client_responsibilities"][:-2]
                                     + facts.client_responsibilities,
                                     "out_of_scope": [Requirement(**r) for r in bdata["facts"]["out_of_scope"]]})
    est, _ = estimator.run(merged, settings.HOURLY_RATE, settings.CURRENCY)
    added = {c.req_id for c in changes if c.kind == "added"}
    cr_hours = round(sum(t.likely for t in est.tasks if t.req_id in added) * (1 + estimator.PM_SHARE), 1)
    rep = diff.report(changes, base["version"], meeting_id, bdata["scope"]["deadline"], deadline,
                      bdata["estimate"]["total_likely"], round(est.total_likely - cr_hours, 1), cr_hours)
    # the problem statement comes from the whole project history, not only the follow-up
    history = "\n".join(m["transcript"] for m in store.meetings(pid))
    data, _ = _package(project, client, meeting_id, facts, merged, history, kb, next_version, excluded, rep)
    return store.save_version(pid, meeting_id, data)
