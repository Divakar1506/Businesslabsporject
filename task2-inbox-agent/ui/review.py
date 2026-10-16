"""Inbox review screen:  streamlit run ui/review.py   (the Knowledge Brain API should be running)

Tabs: Inbox (review drafts) · Autonomy (20-in-a-row rule) · Learning (versioned style rules) · Outbox.
Sending is simulated: approved replies go to the outbox table.
"""
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE.parent)]

import settings  # noqa: E402
from inbox import autonomy, learning  # noqa: E402
from inbox.agent import InboxAgent, load_emails  # noqa: E402
from inbox.classifier import Classifier  # noqa: E402
from inbox.kb_client import KBClient  # noqa: E402
from inbox.models import NEVER_AUTONOMOUS, Category  # noqa: E402
from inbox.store import InboxStore  # noqa: E402
from shared.llm_gateway import Gateway  # noqa: E402

st.set_page_config(page_title="Inbox Agent", page_icon="📬", layout="wide")


@st.cache_resource
def get_agent() -> InboxAgent:
    dev = [e for e in load_emails() if e["split"] == "dev"]
    return InboxAgent(InboxStore(settings.DB_PATH), KBClient(settings.KB_API_URL, settings.KB_API_TOKEN,
                                                              settings.KB_TIMEOUT_SECONDS, settings.KB_RETRIES),
                      Classifier.train(dev), Gateway(), escalate_unknown=True)


agent = get_agent()
store = agent.store
STATUS_ICON = {"new": "🆕", "drafted": "📝", "sent": "✅", "rejected": "🚫", "escalated": "🙋", "kb_unavailable": "⚠️"}
RISK_ICON = {"low": "🟢 low", "medium": "🟠 medium", "high": "🔴 high"}

with st.sidebar:
    st.header("Inbox Agent")
    if agent.kb.health():
        st.success("Knowledge Brain online")
    else:
        st.error(f"Knowledge Brain unavailable at {settings.KB_API_URL}. Drafts that need product facts "
                 "will not be written until it is back.")
    counts = pd.Series([e["status"] for e in store.emails()]).value_counts().to_dict()
    st.caption(" · ".join(f"{STATUS_ICON.get(k, '')} {k}: {v}" for k, v in counts.items()) or "Inbox empty")
    if st.button("Load the 56 sample emails", width="stretch"):
        todo = [e for e in load_emails() if store.email(e["id"]) is None]
        bar = st.progress(0.0, text="Drafting…")
        for i, e in enumerate(todo, 1):
            agent.process(e)
            bar.progress(i / len(todo), text=f"Drafted {i}/{len(todo)}")
        st.rerun()
    if st.button("Retry emails waiting on the Knowledge Brain", width="stretch"):
        for e in store.emails("kb_unavailable"):
            agent.process({**e, "from": e["from_addr"]})
        st.rerun()
    reviewer = st.text_input("Your name", "reviewer")

tab_inbox, tab_auto, tab_learn, tab_out = st.tabs(["Inbox", "Autonomy", "Learning", "Outbox (simulated)"])

with tab_inbox:
    status = st.radio("Show", ["drafted", "kb_unavailable", "escalated", "sent", "rejected", "all"], horizontal=True)
    emails = store.emails(None if status == "all" else status)
    if not emails:
        st.info("Nothing here. Use **Load the 56 sample emails** in the sidebar.")
    else:
        drafts = {e["id"]: (store.latest_draft(e["id"]) or (None, {}))[1] for e in emails}
        label = {e["id"]: f"{STATUS_ICON.get(e['status'], '')} {e['id']} · {drafts[e['id']].get('category', '?')} · "
                          f"{e['subject']}" for e in emails}
        left, right = st.columns([1, 2])
        eid = left.radio("Emails", list(label), format_func=label.get, label_visibility="collapsed")
        e, d = store.email(eid), drafts[eid]
        with right:
            st.subheader(e["subject"])
            st.caption(f"From {e['from_addr']} · {e['received_at']} · status **{e['status']}**")
            st.text(e["body"])
            if d:
                c = st.columns(4)
                c[0].metric("Category", d["category"], f"{d['category_confidence']:.0%} sure", delta_color="off")
                c[1].metric("Urgency", d["urgency"])
                c[2].metric("Risk", RISK_ICON[d["risk"]])
                c[3].metric("Help center", d["kb_status"].replace("_", " "))
                for f in d["flags"]:
                    st.warning(f"🛡️ {f}")
                if d["approval_reasons"]:
                    st.caption("Needs a human because: " + "; ".join(d["approval_reasons"]))
                if d["sources"]:
                    st.caption("Evidence: " + " · ".join(f"[{s['title']}]({s['url']}) ({s['score']:.2f})" for s in d["sources"][:3]))
                if d.get("escalation_id"):
                    st.info(f"Sent to the Knowledge Brain expert queue as #{d['escalation_id']}.")
                st.caption(f"Next action: {d['next_action']} · writer {d['model']} · {d['prompt_version']}")
                if e["status"] in ("drafted", "kb_unavailable"):
                    text = st.text_area("Reply (edit freely)", d["reply"], height=280, key=f"txt_{eid}")
                    reason = st.text_input("Reason for your change (helps the agent learn)", key=f"why_{eid}")
                    b = st.columns(4)
                    try:
                        if b[0].button("✅ Send", type="primary", key=f"send_{eid}",
                                       help="Sends exactly what is in the box. Unchanged text counts toward autonomy."):
                            act = "approve" if autonomy.unchanged(d["reply"], text) else "edit"
                            agent.review(eid, act, text, reason, reviewer)
                            st.rerun()
                        if b[1].button("🚫 Reject", key=f"rej_{eid}", help="Discard the draft; if the box has your own reply it is sent"):
                            agent.review(eid, "reject", text if text.strip() and text != d["reply"] else None, reason, reviewer)
                            st.rerun()
                        if b[2].button("🙋 Expert", key=f"esc_{eid}", help="Send the question to the Knowledge Brain expert queue"):
                            agent.review(eid, "escalate", None, reason or "needs an expert", reviewer)
                            st.rerun()
                        if b[3].button("🔄 Redraft", key=f"re_{eid}"):
                            agent.process({**e, "from": e["from_addr"]})
                            st.rerun()
                    except ValueError as err:
                        st.error(str(err))
                elif e["status"] == "sent":
                    sent = next((o for o in store.outbox() if o["email_id"] == eid), None)
                    if sent:
                        st.success(f"Sent by {sent['sent_by']}")
                        st.text(sent["text"])

with tab_auto:
    st.caption(f"A category becomes eligible after **{settings.AUTONOMY_STREAK} unchanged sends in a row** "
               f"(text compared, not just the click). An admin must switch it on. It switches off by itself when a "
               f"human changes a draft or the edit rate over the last {settings.AUTONOMY_WINDOW} goes above "
               f"{settings.AUTONOMY_MAX_EDIT_RATE:.0%}. Refunds, complaints, quotes and project requests never go out alone.")
    rows = []
    for cat in Category:
        stt = store.autonomy(cat.value)
        rows.append({"category": cat.value, "streak": f"{stt['streak']}/{settings.AUTONOMY_STREAK}",
                     "eligible": "yes" if stt["eligible"] else "no", "autonomous": "ON" if stt["enabled"] else "off",
                     "allowed": "never" if cat in NEVER_AUTONOMOUS else "yes"})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    c1, c2, c3 = st.columns([2, 1, 1])
    cat = c1.selectbox("Category", [c.value for c in Category if c not in NEVER_AUTONOMOUS])
    if c2.button("Switch ON"):
        try:
            autonomy.enable(store, cat, reviewer)
            st.rerun()
        except ValueError as err:
            st.error(str(err))
    if c3.button("Switch OFF"):
        autonomy.disable(store, cat, reviewer)
        st.rerun()
    st.subheader("Audit log")
    ev = pd.DataFrame(store.events())
    if not ev.empty:
        ev["time"] = ev["ts"].map(lambda t: datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S"))
        st.dataframe(ev[["time", "category", "event", "detail"]], hide_index=True, width="stretch")

with tab_learn:
    revs = pd.DataFrame(store.reviews())
    if revs.empty:
        st.info("No reviews yet. Learning starts after reviewers send or edit drafts.")
    else:
        human = revs[revs["action"] != "auto"]
        stats = human.groupby("category").agg(reviews=("id", "count"), changed=("unchanged", lambda s: int((s == 0).sum())))
        stats["edit rate"] = (stats["changed"] / stats["reviews"]).map("{:.0%}".format)
        st.subheader("How often humans change drafts")
        st.dataframe(stats, width="stretch")
        reasons = human[human["reason"].fillna("") != ""]["reason"].value_counts().head(10)
        if not reasons.empty:
            st.caption("Most common correction reasons: " + "; ".join(f"{r} ({n})" for r, n in reasons.items()))
    st.subheader("Style rules (versioned)")
    active, _ = store.active_rules()
    st.dataframe(pd.DataFrame(store.all_rules())[["version", "active", "note"]], hide_index=True, width="stretch")
    c1, c2 = st.columns(2)
    if c1.button("Learn from edits now"):
        p = learning.propose(store)
        if p is None:
            st.info(f"Nothing new to learn yet (a sentence must be deleted in {learning.MIN_TIMES}+ edits).")
        elif learning.adopt(store, p):
            st.success(f"{p['version']} adopted: {p['evaluation']}")
        else:
            st.warning(f"{p['version']} saved but NOT adopted (no improvement or it would change approved drafts): "
                       f"{p['evaluation']}")
    ver = c2.selectbox("Activate / roll back to", [r["version"] for r in store.all_rules()],
                       index=[r["version"] for r in store.all_rules()].index(active))
    if ver != active and c2.button(f"Activate {ver}"):
        store.activate_rules(ver)
        st.rerun()

with tab_out:
    out = pd.DataFrame(store.outbox())
    if out.empty:
        st.info("Nothing sent yet.")
    else:
        out["sent"] = out["sent_at"].map(lambda t: datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M"))
        st.dataframe(out[["sent", "email_id", "sent_by", "text"]], hide_index=True, width="stretch")
        st.caption("Simulated delivery: nothing leaves this computer.")
