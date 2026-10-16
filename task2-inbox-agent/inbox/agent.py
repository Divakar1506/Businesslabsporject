"""The Inbox Agent: email in -> classify -> ask the Knowledge Brain (API) -> draft -> human review -> learn.

Sending is simulated: approved replies go to the outbox table (one send per email, so retries
can never send twice). Nothing reaches a customer without a human, except categories a human
has explicitly switched to autonomous after 20 unchanged sends in a row.
"""
import json
from pathlib import Path

import settings
from shared import guardrails
from shared.llm_gateway import BudgetExceeded, Gateway

from . import autonomy
from .classifier import Classifier, risk, urgency
from .drafter import build_draft
from .kb_client import KBAnswer, KBClient, KBUnavailable
from .models import KNOWLEDGE_CATEGORIES, Category, Draft
from .store import InboxStore

LLM_PROMPT = (Path(__file__).parent / "prompts" / "draft_v1.md").read_text(encoding="utf-8")


def load_emails(path: Path = settings.EMAILS_FILE) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


class InboxAgent:
    def __init__(self, store: InboxStore, kb: KBClient, classifier: Classifier,
                 gateway: Gateway | None = None, escalate_unknown: bool = True):
        self.store, self.kb, self.classifier = store, kb, classifier
        self.gateway = gateway
        self.escalate_unknown = escalate_unknown

    # ------------------------------------------------------------------
    def process(self, email: dict) -> Draft:
        self.store.add_email(email)
        cat, conf = self.classifier.classify(email["subject"], email["body"])
        urg = urgency(email["subject"], email["body"])
        rsk = risk(cat, email["subject"], email["body"], urg)
        kb = feature = None
        kb_error = None
        question = f"{email['subject']}. {guardrails.redact_pii(email['body'])}"
        try:
            if cat in KNOWLEDGE_CATEGORIES:
                kb = self.kb.query(question, escalate=self.escalate_unknown)
            elif cat in (Category.QUOTE, Category.PROJECT_REQUEST):
                # Does the product already do this? Never escalate - it is only a hint for the reply.
                feature = self.kb.query(question, escalate=False)
        except KBUnavailable as e:
            kb_error = str(e)
        except ValueError as e:  # the API rejected the question (e.g. too long): treat as no evidence
            kb = KBAnswer(status="insufficient_evidence", answer="", flags=[str(e)])
        version, rules = self.store.active_rules()
        llm_reply, model = self._llm_reply(email, cat, kb) if kb_error is None else (None, "template")
        draft = build_draft(email, cat, conf, urg, rsk, kb, kb_error, feature,
                            self.store.thread_subject(email.get("thread_id"), email["id"]),
                            rules.get(cat.value, {}), version, llm_reply, model)
        draft_id = self.store.save_draft(draft.model_dump(mode="json"))
        self.store.set_status(email["id"], "kb_unavailable" if kb_error else "drafted")
        ok, _ = autonomy.can_auto_send(self.store, draft.model_dump(mode="json"))
        if ok and self.store.send(email["id"], draft.reply, "auto"):
            self.store.add_review(email_id=email["id"], draft_id=draft_id, category=cat.value, action="auto",
                                  draft_text=draft.reply, final_text=draft.reply, unchanged=1, reason="autonomous",
                                  reviewer="agent", prompt_version=draft.prompt_version)
            self.store.set_status(email["id"], "sent")
            self.store.log_event(cat.value, "auto_sent", email["id"])
        return draft

    def _llm_reply(self, email: dict, cat: Category, kb: KBAnswer | None) -> tuple[str | None, str]:
        """Model-written reply when LLM_MODE is ollama/claude; None in offline mode or on any failure."""
        if self.gateway is None or self.gateway.mode == "offline":
            return None, "template"
        facts = kb.answer if kb and kb.status == "answered" else "No help-center facts found."
        user = (f"<email>\nSubject: {email['subject']}\n{email['body']}\n</email>\n<category>{cat.value}</category>\n"
                f"<facts>\n{facts}\n</facts>")
        try:
            r = self.gateway.call(task="inbox", endpoint="draft", system=LLM_PROMPT, max_tokens=500,
                                  messages=[{"role": "user", "content": user}], prompt_version="inbox-llm-v1")
        except (BudgetExceeded, Exception):
            return None, "template"
        if r.model.startswith("offline/"):
            return None, "template"
        return r.text.strip(), r.model

    # ------------------------------------------------------------------
    def review(self, email_id: str, action: str, final_text: str | None = None, reason: str = "",
               reviewer: str = "reviewer") -> dict:
        """action: approve | edit | reject | escalate. Returns {sent, autonomy}."""
        if action not in ("approve", "edit", "reject", "escalate"):
            raise ValueError(f"unknown action {action}")
        latest = self.store.latest_draft(email_id)
        if latest is None:
            raise KeyError(email_id)
        draft_id, draft = latest
        if action == "approve":
            final_text = draft["reply"]
        if action == "edit" and not (final_text or "").strip():
            raise ValueError("an edit needs the new text")
        if action == "approve" and not final_text.strip():
            raise ValueError("there is no draft to approve; write a reply instead (edit)")
        same = action == "approve" or (action == "edit" and autonomy.unchanged(draft["reply"], final_text))
        if action == "edit" and same:
            action = "approve"  # saving the draft untouched counts as an unchanged send
        sent = False
        if action in ("approve", "edit") or (action == "reject" and (final_text or "").strip()):
            sent = self.store.send(email_id, final_text, f"human:{reviewer}")
            self.store.set_status(email_id, "sent")
        elif action == "reject":
            self.store.set_status(email_id, "rejected")
        else:
            email = self.store.email(email_id)
            try:
                self.kb.escalate(f"{email['subject']}. {guardrails.redact_pii(email['body'])}", reason or "escalated by reviewer")
            except KBUnavailable:
                pass  # still marked escalated locally; the reviewer sees the state
            self.store.set_status(email_id, "escalated")
        self.store.add_review(email_id=email_id, draft_id=draft_id, category=draft["category"], action=action,
                              draft_text=draft["reply"], final_text=final_text, unchanged=int(same),
                              reason=guardrails.redact_pii(reason), reviewer=reviewer,
                              prompt_version=draft["prompt_version"])
        state = autonomy.record_outcome(self.store, draft["category"], same, action)
        return {"sent": sent, "autonomy": state}
