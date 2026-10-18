"""Scope Builder screen:  streamlit run ui/app.py   (the Knowledge Brain API should be running)

Upload or pick a transcript -> requirements with sources -> feasibility -> estimate -> proposal ->
change report for follow-ups -> human approval -> (simulated) send. History and audit log included.
"""
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE.parent)]

import settings  # noqa: E402
from core.kb_client import KBClient  # noqa: E402
from core.orchestrator import load_sample_meetings, run_meeting  # noqa: E402
from core.store import ApprovalError, ScopeStore  # noqa: E402

st.set_page_config(page_title="Scope Builder", page_icon="📐", layout="wide")


@st.cache_resource
def deps():
    return ScopeStore(settings.DB_PATH), KBClient(settings.KB_API_URL, settings.KB_API_TOKEN)


store, kb = deps()
FEAS = {"existing_config": "🟢 existing setting", "existing_limited": "🟡 existing + adjustment",
        "custom": "🔴 custom", "needs_clarification": "⚪ needs clarification"}
STATUS = {"draft": "📝 draft", "approved": "✅ approved", "superseded": "🗄️ superseded", "sent": "📤 sent"}

with st.sidebar:
    st.header("Scope Builder")
    if kb.health():
        st.success("Knowledge Brain online")
    else:
        st.error(f"Knowledge Brain unavailable at {settings.KB_API_URL}: feasibility checks will say 'needs clarification'.")
    if st.button("Load sample meetings A and B", width="stretch"):
        with st.spinner("Running the agent team…"):
            for m in load_sample_meetings():
                if m["follows"] is None and not m.get("holdout"):
                    run_meeting(store, kb, m["project"], m["client"], m["id"], m["date"], m["text"])
        st.rerun()
    reviewer = st.text_input("Your name (for approvals)", "")

    with st.expander("➕ New meeting"):
        projects = [p["name"] for p in store.projects()]
        pname = st.selectbox("Project", ["(new project)"] + projects)
        new_name = st.text_input("New project name") if pname == "(new project)" else pname
        client = st.text_input("Client name")
        mdate = st.date_input("Meeting date", date.today())
        follows = None
        if pname != "(new project)":
            pid0 = store.project(pname)
            prev = [m["id"] for m in store.meetings(pid0)]
            choice = st.selectbox("Follow-up of", ["(first meeting)"] + prev)
            follows = None if choice == "(first meeting)" else choice
        samples = {m["file"]: m for m in load_sample_meetings()}
        pick = st.selectbox("Sample transcript (optional)", ["(none)"] + list(samples))
        up = st.file_uploader("…or upload a .txt transcript", type=["txt"])
        text = st.text_area("…or paste it", samples[pick]["text"] if pick != "(none)" else "", height=160)
        if up:
            text = up.read().decode("utf-8", errors="replace")
        if st.button("Run the agents", type="primary"):
            try:
                mid = f"m{datetime.now():%Y%m%d%H%M%S}"
                with st.spinner("Analysing, checking feasibility, estimating…"):
                    run_meeting(store, kb, new_name, client or new_name, mid, mdate.isoformat(), text, follows)
                st.rerun()
            except (ApprovalError, ValueError) as e:
                st.error(str(e))

projects = store.projects()
if not projects:
    st.info("No projects yet. Use **Load sample meetings A and B** or **New meeting** in the sidebar.")
    st.stop()

pname = st.selectbox("Project", [p["name"] for p in projects])
pid = store.project(pname)
versions = store.versions(pid)
if not versions:
    st.info("No versions yet for this project.")
    st.stop()
vnum = st.radio("Version", [v["version"] for v in versions], index=len(versions) - 1, horizontal=True,
                format_func=lambda n: f"v{n} · {STATUS[next(v['status'] for v in versions if v['version'] == n)]}")
v = store.version(pid, vnum)
d = v["data"]
base = store.baseline(pid)
st.caption(f"From meeting **{v['meeting_id']}** · status **{v['status']}**"
           + (f" · approved by {v['approved_by']}" if v["approved_by"] else "")
           + (f" · current approved baseline: v{base['version']}" if base else " · no approved baseline yet"))
for f in d["facts"]["flags"]:
    st.warning(f"🛡️ {f}")

tab_req, tab_est, tab_prop, tab_chg, tab_appr = st.tabs(
    ["Requirements", "Estimate", "Proposal", "Changes vs baseline", "Approval & history"])

with tab_req:
    st.markdown(f"**Deadline:** {d['scope']['deadline'] or 'not stated'} · **Budget:** {d['scope']['budget'] or 'not stated'}"
                f" · **Requirements:** {len(d['requirements'])}".replace("$", r"\$"))
    rows = [{"id": r["id"], "requirement": r["title"], "status": r["status"], "priority": r["priority"],
             "asked by client": "yes" if r["explicit"] else "inferred",
             "product check": FEAS.get(r["feasibility"]["cls"], "") if r.get("feasibility") else "",
             "estimate": r["estimate_status"]} for r in d["requirements"]]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    for r in d["requirements"]:
        with st.expander(f"{r['id']} · {r['title']}"):
            st.write(r["description"])
            st.markdown("**Acceptance criteria:** " + " ".join(r["acceptance_criteria"]))
            if r.get("feasibility"):
                st.markdown(f"**Product check:** {FEAS[r['feasibility']['cls']]} — {r['feasibility']['note']}")
                for e in r["feasibility"]["evidence"]:
                    st.markdown(f"- [{e['title']}]({e['url']}) · match {e['score']:.2f}")
            for dep in r["dependencies"]:
                st.caption(f"Dependency: {dep}")
            st.markdown("**Where this came from (verify):**")
            for s in r["sources"]:
                st.caption(f"[{s['meeting_id']} {s['ts']}] {s['speaker']}: “{s['quote']}”")
    if d["facts"]["out_of_scope"]:
        st.markdown("**Explicitly out of scope:** " + "; ".join(r["title"] for r in d["facts"]["out_of_scope"]))

with tab_est:
    e = d["estimate"]
    c = st.columns(4)
    c[0].metric("Low", f"{e['total_low']:g} h")
    c[1].metric("Likely", f"{e['total_likely']:g} h")
    c[2].metric("High", f"{e['total_high']:g} h")
    c[3].metric("Expected (PERT)", f"{e['expected']:g} h",
                f"{e['currency']} {e['expected'] * e['rate']:,.0f} at {e['rate']:g}/h", delta_color="off")
    st.dataframe(pd.DataFrame(e["tasks"]), hide_index=True, width="stretch")
    if e["not_estimated"]:
        st.info("Not estimated until clarified: " + ", ".join(e["not_estimated"]))

with tab_prop:
    st.download_button("Download proposal (.md)", d["proposal"], file_name=f"proposal_{pname}_v{vnum}.md")
    if v["status"] == "draft":
        st.warning("DRAFT — not approved. It cannot be sent to the client.")
    st.markdown(d["proposal"])

with tab_chg:
    cr = d.get("change_report")
    if not cr:
        st.info("This version is from a first meeting. Run a follow-up meeting (sidebar) after approving a version.")
    else:
        st.caption(f"Compared with approved baseline **v{cr['baseline_version']}** (never with an unapproved draft).")
        icon = {"added": "➕", "removed": "➖", "modified": "✏️", "unchanged": "▫️"}
        st.dataframe(pd.DataFrame([{"change": f"{icon[c['kind']]} {c['kind']}", "id": c["req_id"], "requirement": c["title"],
                                    "outside agreed scope": "⚠️ quote separately" if c["out_of_scope"] else "",
                                    "before": c["before"] or "", "after": c["after"] or ""} for c in cr["changes"]]),
                     hide_index=True, width="stretch")
        c = st.columns(3)
        c[0].metric("Deadline", cr["deadline_after"] or "-", None if cr["deadline_after"] == cr["deadline_before"]
                    else f"was {cr['deadline_before']}", delta_color="off")
        c[1].metric("Agreed scope (likely)", f"{cr['hours_after']:g} h", f"{cr['hours_after'] - cr['hours_before']:+g} h")
        c[2].metric("Change requests", f"{cr['hours_change_requests']:g} h", "quoted separately", delta_color="off")
        if base and base["version"] != vnum:
            with st.expander("Side by side: approved baseline vs this version"):
                l, r = st.columns(2)
                l.markdown(f"**v{base['version']} (approved)**")
                l.markdown("\n".join(f"- {x}" for x in base["data"]["scope"]["deliverables"]))
                r.markdown(f"**v{vnum}**")
                r.markdown("\n".join(f"- {x}" for x in d["scope"]["deliverables"]))

with tab_appr:
    for i in d["review_issues"]:
        (st.error if i["severity"] == "blocker" else st.warning)(f"{i['severity']}: {i['issue']}")
    st.dataframe(pd.DataFrame([{"version": x["version"], "status": STATUS[x["status"]], "meeting": x["meeting_id"],
                                "approved by": x["approved_by"] or "",
                                "likely hours": x["data"]["estimate"]["total_likely"]} for x in versions]),
                 hide_index=True, width="stretch")
    c1, c2 = st.columns(2)
    if c1.button(f"✅ Approve v{vnum}", disabled=v["status"] != "draft", type="primary"):
        try:
            store.approve(pid, vnum, reviewer)
            st.rerun()
        except ApprovalError as e:
            st.error(str(e))
    if c2.button(f"📤 Send v{vnum} to client (simulated)", disabled=v["status"] != "approved",
                 help="Only an approved, unchanged version can be sent"):
        try:
            store.send_to_client(pid, vnum, reviewer or "unknown")
            st.success("Marked as sent (simulated - nothing leaves this computer).")
            st.rerun()
        except ApprovalError as e:
            st.error(str(e))
    st.subheader("Audit log")
    audit = pd.DataFrame(store.audit(pid))
    if not audit.empty:
        audit["time"] = audit["ts"].map(lambda t: datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S"))
        st.dataframe(audit[["time", "version", "actor", "event", "detail"]], hide_index=True, width="stretch")
