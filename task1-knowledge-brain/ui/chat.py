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
    if status:
        st.metric("New knowledge waiting for review", status.get("pending_review", 0))
        if status.get("edited_but_source_changed"):
            st.warning(f"{status['edited_but_source_changed']} edited article(s): the help center changed since "
                       f"your edit - check them in Manage knowledge")
    me = st.text_input("Your name (for reviews and edits)", key="me")

tab_ask, tab_expert, tab_add, tab_review, tab_manage = st.tabs(
    ["Ask", "Expert queue", "Add knowledge", "Review new knowledge", "Manage knowledge"])

with tab_add:
    st.caption("Add your own knowledge: fill in the form and attach files. Text is pulled out of the files. "
               "It goes to **Review new knowledge** first and is used in answers only after a person approves it.")
    with st.form("add_knowledge", clear_on_submit=True):
        title = st.text_input("Title *", placeholder="e.g. Refund policy for annual plans")
        c1, c2 = st.columns(2)
        section = c1.text_input("Section / category", placeholder="e.g. Billing")
        tags = c2.text_input("Tags (comma separated)", placeholder="refund, annual, billing")
        body = st.text_area("Knowledge text", height=200,
                            placeholder="Write the answer the way you would explain it to a customer…")
        files = st.file_uploader("Attach files (JPG, PNG, PDF, Word .docx, .txt, .md — up to 5, 10 MB each)",
                                 type=["jpg", "jpeg", "png", "pdf", "docx", "txt", "md"], accept_multiple_files=True)
        img_desc = st.text_area("Describe the image(s) (makes screenshots searchable)", height=80,
                                placeholder="e.g. Settings › Billing page with the 'Refund' button highlighted")
        author = st.text_input("Your name")
        if st.form_submit_button("Send for review", type="primary"):
            payload = [("files", (f.name, f.getvalue(), f.type or "application/octet-stream")) for f in files or []]
            res, e = api("POST", "/articles", data={"title": title, "body": body, "section": section, "tags": tags,
                                                    "image_description": img_desc, "author": author},
                         files=payload or None)
            if e:
                st.error(e)
            else:
                st.success(f"Sent for review as **{res['article_id']}**. It is **not** used in answers until someone "
                           f"approves it in **Review new knowledge**.")
                for f in res["files"]:
                    icon = "✅" if f["kind"] != "unsupported" and (f["chars"] or f["kind"] == "image") else "⚠️"
                    st.markdown(f"{icon} **{f['name']}** — {f['method'] or f['kind']}, {f['chars']:,} characters of text")
                    for w in f["warnings"]:
                        st.caption(f"   ⚠️ {w}")
                for fl in res["flags"]:
                    st.warning(f"🛡️ {fl}")

with tab_review:
    st.caption("New knowledge waits here. Read what was extracted, correct it if needed, then approve it into the "
               "knowledge base or reject it. Lines starting with `## ` begin a new section.")
    pending, e = api("GET", "/articles", params={"status": "pending", "limit": 100})
    if e:
        st.error(e)
    elif not pending:
        st.success("Nothing waiting for review.")
    for p in pending or []:
        full, e2 = api("GET", f"/articles/{p['article_id']}")
        if e2:
            st.error(e2)
            continue
        with st.container(border=True):
            st.markdown(f"**{p['article_id']}** · added by {p['edited_by'] or 'unknown'} · "
                        + (" › ".join(p["breadcrumb"]) or "no section"))
            if p["files"]:
                st.caption("Files: " + " · ".join(f"[{n}]({API}/files/{p['article_id']}/{n})" for n in p["files"]))
            for img in full["images"]:
                st.image(API + img["src"] if img["src"].startswith("/") else img["src"], width=240)
            t = st.text_input("Title", full["title"], key=f"rt_{p['article_id']}")
            txt = st.text_area("Text that will be learned", full["editable_text"], height=220, key=f"rx_{p['article_id']}")
            b1, b2 = st.columns(2)
            if b1.button("✅ Approve into the knowledge base", key=f"ap_{p['article_id']}", type="primary"):
                _, e3 = api("POST", f"/articles/{p['article_id']}/approve",
                            json={"reviewer": me, "title": t, "text": txt})
                st.error(e3) if e3 else st.rerun()
            if b2.button("❌ Reject", key=f"rj_{p['article_id']}"):
                _, e3 = api("POST", f"/articles/{p['article_id']}/reject", json={"reviewer": me or "unknown"})
                st.error(e3) if e3 else st.rerun()

with tab_manage:
    st.caption("Correct or delete anything the brain knows. Every edit keeps the old version, and crawls never "
               "overwrite a human edit.")
    c1, c2 = st.columns([1, 2])
    src = c1.selectbox("Show", ["manual", "expert", "crawled", "all"],
                       format_func={"manual": "Uploaded by hand", "expert": "Expert answers",
                                    "crawled": "Help-center articles", "all": "Everything"}.get)
    q = c2.text_input("Search titles")
    items, e = api("GET", "/articles", params={"source": src, "q": q, "limit": 100})
    if e:
        st.error(e)
    elif not items:
        st.info("Nothing here yet." + (" Use **Add knowledge** to upload some." if src == "manual" else ""))
    labels = {a["article_id"]: f"{a['title'][:70]} ({a['article_id']})" for a in items or []}
    edit_id = st.selectbox("✏️ Edit an article", ["—"] + list(labels), format_func=lambda k: labels.get(k, "— choose —"))
    if edit_id != "—":
        full, e2 = api("GET", f"/articles/{edit_id}")
        if e2:
            st.error(e2)
        else:
            with st.container(border=True):
                t = st.text_input("Title", full["title"], key=f"et_{edit_id}")
                txt = st.text_area("Text (lines starting with `## ` begin a new section)", full["editable_text"],
                                   height=260, key=f"ex_{edit_id}")
                if st.button("Save changes", key=f"es_{edit_id}", type="primary"):
                    _, e3 = api("PUT", f"/articles/{edit_id}", json={"title": t, "text": txt, "editor": me})
                    st.error(e3) if e3 else (st.success("Saved. The brain uses the corrected text now."), st.rerun())
    st.caption(f"Showing {len(items or [])} article(s) (max 100). Use the search box to find others.")
    for a in items or []:
        with st.container(border=True):
            review = {"pending": "🕒 waiting for review", "rejected": "❌ rejected"}.get(a["review_status"])
            state = review or ("🟢 in use" if a["active"] else "🗑️ deleted (not used in answers)")
            link = f"[{a['title']}]({a['url']})" if a["url"].startswith("http") else f"**{a['title']}**"
            st.markdown(f"{link} · `{a['article_id']}` · {a['source']} · v{a['version']} · {state}"
                        + (f" · ✏️ edited by {a['edited_by']}" if a["human_edited"] else ""))
            if a["source_changed"]:
                st.warning("The help center changed this page after your edit. Compare and update if needed.")
            if a["breadcrumb"]:
                st.caption(" › ".join(a["breadcrumb"]))
            if a["files"]:
                st.caption("Files: " + " · ".join(f"[{n}]({API}/files/{a['article_id']}/{n})" for n in a["files"]))
            if a["active"] and a["review_status"] == "approved":
                if st.button("🗑️ Delete from the knowledge base", key=f"rm_{a['article_id']}"):
                    _, e2 = api("DELETE", f"/articles/{a['article_id']}")
                    st.error(e2) if e2 else st.rerun()
            elif not a["active"] and a["review_status"] == "approved" and st.button("Restore", key=f"rs_{a['article_id']}"):
                _, e2 = api("POST", f"/articles/{a['article_id']}/restore")
                st.error(e2) if e2 else st.rerun()

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
                    tag = {"expert": "🧑‍💼 expert answer", "manual": "📎 uploaded knowledge"}.get(s.get("kind"), "📄")
                    label = s["title"] + (f" — {s['section']}" if s["section"] else "")
                    if s["url"].startswith("http"):
                        st.markdown(f"{tag} [{label}]({s['url']}) · score {s['score']:.2f}")
                    else:
                        st.markdown(f"{tag} {label} · score {s['score']:.2f}")
                if r["images"]:
                    with st.expander(f"Screenshots from these sections ({len(r['images'])})"):
                        cols = st.columns(3)
                        for i, img in enumerate(r["images"]):
                            src = API + img["src"] if img["src"].startswith("/") else img["src"]  # uploaded files
                            cols[i % 3].image(src, caption=img["section"] or "intro", width="stretch")
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
