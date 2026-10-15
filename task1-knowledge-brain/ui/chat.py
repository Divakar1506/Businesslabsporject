"""Knowledge Brain screens:  streamlit run ui/chat.py   (the API must be running on KB_API_URL)

Talks to the API over HTTP only, like Tasks 2 and 3 will.
- Ask: chat with answers, sources, screenshots, and a clear "sent to an expert" state.
- Expert queue: open questions the brain could not answer; resolving one adds it to memory.
"""
import os
from datetime import date, datetime

import httpx
import streamlit as st

API = os.environ.get("KB_API_URL", "http://localhost:8100")
TOKEN = os.environ.get("KB_API_TOKEN", "")
HEADERS = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}

st.set_page_config(page_title="Knowledge Brain", page_icon="📚", layout="wide")


def api(method: str, path: str, **kw):
    """Returns (json, error message). Never raises, so the screen can show a clear failure state."""
    try:
        r = httpx.request(method, f"{API}{path}", headers=HEADERS, timeout=90, **kw)
    except httpx.HTTPError:
        return None, f"Knowledge service unavailable at {API}. Start it with: python -m uvicorn app.main:app --port 8100"
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = r.text
        return None, f"Error {r.status_code}: {detail}"
    return r.json(), None


health, err = api("GET", "/health")
with st.sidebar:
    st.header("Knowledge Brain")
    if err:
        st.error(err)
    else:
        st.success(f"Online · {health['active_articles']} articles · {health['active_chunks']} chunks")
    status, _ = api("GET", "/ingestion/status")
    if status:
        pages = status["pages"]
        st.caption(f"Crawl: {pages.get('done', 0)} done · {pages.get('pending', 0)} pending · "
                   f"{pages.get('failed', 0)} failed · {pages.get('removed', 0)} removed")
    open_esc, _ = api("GET", "/escalations", params={"status": "open"})
    st.metric("Questions waiting for an expert", len(open_esc or []))

tab_ask, tab_expert = st.tabs(["Ask", "Expert queue"])

with tab_ask:
    if "history" not in st.session_state:
        st.session_state.history = []
    for turn in st.session_state.history:
        with st.chat_message("user"):
            st.write(turn["q"])
        with st.chat_message("assistant"):
            r = turn["r"]
            if r is None:
                st.error(turn["err"])
                continue
            if r["status"] == "insufficient_evidence":
                st.warning(r["answer"], icon="🙋")
                st.caption(f"Sent to the expert queue as #{r['escalation_id']} · best match score "
                           f"{r['retrieval_score']:.2f}" if r.get("escalation_id") else "")
            else:
                st.markdown(r["answer"])
                mode = {"extractive": "quoted from the help center (offline, $0)", "llm": f"written by {r['model']}"}
                st.caption(f"{mode.get(r['answer_mode'], r['answer_mode'])} · match score "
                           f"{r['retrieval_score']:.2f} · {r['latency_ms']:.0f} ms")
                for i, s in enumerate(r["sources"], 1):
                    tag = "🧑‍💼 expert answer" if s.get("kind") == "expert" else "📄"
                    label = s["title"] + (f" — {s['section']}" if s["section"] else "")
                    if s["url"].startswith("http"):
                        st.markdown(f"{tag} [{label}]({s['url']}) · score {s['score']:.2f}")
                    else:
                        st.markdown(f"{tag} {label} · score {s['score']:.2f}")
                if r["images"]:
                    with st.expander(f"Screenshots from these sections ({len(r['images'])})"):
                        cols = st.columns(3)
                        for i, img in enumerate(r["images"]):
                            cols[i % 3].image(img["src"], caption=img["section"] or "intro", width="stretch")
            for f in r.get("flags", []):
                st.caption(f"🛡️ {f}")

    q = st.chat_input("Ask about settings, billing, members, design…")
    if q:
        with st.spinner("Searching the help center…"):
            r, e = api("POST", "/query", json={"question": q})
        st.session_state.history.append({"q": q, "r": r, "err": e})
        st.rerun()

with tab_expert:
    st.caption("Questions the brain could not answer. A **general** answer is added to memory for everyone; "
               "a **customer** answer is kept on record only. Set an expiry for time-limited facts.")
    items, e = api("GET", "/escalations", params={"status": "open"})
    if e:
        st.error(e)
    elif not items:
        st.success("No open questions.")
    for esc in items or []:
        with st.container(border=True):
            when = datetime.fromtimestamp(esc["created_at"]).strftime("%Y-%m-%d %H:%M")
            st.markdown(f"**#{esc['id']} · {esc['question']}**")
            st.caption(f"{when} · from {esc['source']} · {esc['reason']}")
            if esc["evidence"]:
                st.caption("Closest articles: " + "; ".join(f"{x['title']} ({x['score']:.2f})" for x in esc["evidence"]))
            with st.form(f"resolve_{esc['id']}"):
                ans = st.text_area("Expert answer", key=f"a{esc['id']}")
                c1, c2, c3 = st.columns(3)
                who = c1.text_input("Your name", key=f"w{esc['id']}")
                scope = c2.radio("Applies to", ["general", "customer"], horizontal=True, key=f"s{esc['id']}")
                exp = c3.date_input("Expires (optional)", value=None, min_value=date.today(), key=f"e{esc['id']}")
                if st.form_submit_button("Save answer", type="primary"):
                    body = {"answer": ans, "expert": who, "scope": scope,
                            "expires_at": exp.isoformat() if exp else None}
                    res, e2 = api("POST", f"/escalations/{esc['id']}/resolve", json=body)
                    if e2:
                        st.error(e2)
                    else:
                        st.success("Saved." + (" It is now part of the brain's memory." if scope == "general" else ""))
                        st.rerun()
