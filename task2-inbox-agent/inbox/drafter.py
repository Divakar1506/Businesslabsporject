"""Write a reply draft for one classified email.

Two writers, same output schema (models.Draft):
- template (offline, $0): category templates in the company voice + Knowledge Brain evidence.
- llm (LLM_MODE=ollama|claude): the model writes the reply from the same facts; if its text fails a
  guardrail it is discarded and the template reply is used.
Learned style rules (see learning.py) are applied last, so improvements are versioned and undoable.
"""
import re

import settings
from shared import guardrails

from .kb_client import KBAnswer
from .models import NEVER_AUTONOMOUS, Category, Draft, Source

TEMPLATE_VERSION = "inbox-template-v1"
ALLOWED_LINK_DOMAINS = {"brilliantdirectories.com", "support.brilliantdirectories.com"}
_CITE = re.compile(r"\s*\[\d+\]")


def first_name(body: str) -> str | None:
    """The sign-off name, e.g. 'Thanks,\\nMaria' -> 'Maria'."""
    lines = [l.strip(" ,.-") for l in body.strip().splitlines() if l.strip()]
    if len(lines) >= 2 and re.fullmatch(r"(thanks|thank you|regards|best|cheers|kind regards|best regards|sincerely)",
                                        lines[-2].lower().strip(" ,!")):
        cand = lines[-1].split()[0] if lines[-1].split() else ""
        if cand.isalpha() and cand[0].isupper() and len(cand) <= 20:
            return cand
    return None


def summarize(subject: str, body: str) -> str:
    text = re.sub(r"\s+", " ", body).strip()
    first = re.split(r"(?<=[.?!])\s", text, maxsplit=1)[0]
    return f"{subject.strip()} — {first[:160]}"


def evidence_lines(kb: KBAnswer, limit: int = 4) -> list[str]:
    lines = [l.strip()[2:] if l.strip().startswith("- ") else l.strip()
             for l in kb.answer.splitlines()[1:] if l.strip()] if kb.answer_mode == "extractive" else [kb.answer]
    return [_CITE.sub("", l).strip() for l in lines if l][:limit]


def _links(sources: list[dict], n: int = 2) -> str:
    out = [f"{s['title']}: {s['url']}" for s in sources[:n] if s["url"].startswith("http")]
    return ("\n\nFull guide" + ("s" if len(out) > 1 else "") + ":\n" + "\n".join(out)) if out else ""


def template_reply(cat: Category, email: dict, kb: KBAnswer | None, feature_hint: KBAnswer | None,
                   thread_subject: str | None) -> str:
    name = first_name(email["body"])
    hi = f"Hi {name}," if name else "Hi there,"
    sig = settings.SIGNATURE
    if cat in (Category.HOW_TO, Category.TROUBLESHOOTING):
        if kb and kb.status == "answered":
            lead = ("Here's how to do that, from our help center:" if cat == Category.HOW_TO
                    else "Here's what our help center says about this:")
            steps = "\n".join(f"- {l}" for l in evidence_lines(kb))
            return (f"{hi}\n\nThanks for reaching out. {lead}\n\n{steps}{_links(kb.sources)}\n\n"
                    f"If anything doesn't match what you see on your screen, reply with a screenshot and we'll take a look.\n\n{sig}")
        return (f"{hi}\n\nThanks for your question. I want to make sure you get an accurate answer, so I've passed it "
                f"to our product specialist, and we'll get back to you as soon as they confirm.\n\n{sig}")
    if cat == Category.REFUND_CANCEL:
        return (f"{hi}\n\nThanks for letting us know. I've passed your request to our billing team, who will review "
                f"your account and reply to you directly.\n\nIf there's anything we could have done better, we'd "
                f"really like to hear it.\n\n{sig}")
    if cat == Category.COMPLAINT:
        return (f"{hi}\n\nThank you for telling us about this, and I'm sorry for the trouble it has caused. I've shared "
                f"your message with our support lead so it gets proper attention, and we'll follow up with you directly."
                f"\n\nTo help us look into it quickly, could you reply with the page links, a screenshot, and when you "
                f"first noticed the problem?\n\n{sig}")
    if cat in (Category.QUOTE, Category.PROJECT_REQUEST):
        hint = ""
        if feature_hint and feature_hint.status == "answered" and feature_hint.sources:
            s = feature_hint.sources[0]
            hint = (f"\n\nGood news: part of this may already be possible with an existing feature, which would cost "
                    f"far less than custom work. See {s['title']}: {s['url']}")
        what = "an accurate quote" if cat == Category.QUOTE else "a clear scope and estimate"
        return (f"{hi}\n\nThanks for thinking of us for this. To prepare {what}, could you share:\n"
                f"- what you'd like it to do, step by step\n- any examples or websites you like\n"
                f"- your ideal timeline\n\nOnce we have these, our team will put the details together for you."
                f"{hint}\n\n{sig}")
    if cat == Category.FOLLOW_UP:
        ref = f" on \"{thread_subject}\"" if thread_subject else ""
        return (f"{hi}\n\nThanks for following up{ref}, and sorry for the wait. I'm checking the status with the team "
                f"now and will update you as soon as I have news.\n\n{sig}")
    return f"{hi}\n\nThanks for your message. A member of our team will look at it and get back to you.\n\n{sig}"


def apply_style_rules(reply: str, rules: dict) -> str:
    """Learned edits: drop sentences reviewers keep deleting."""
    for phrase in rules.get("remove_sentences", []):
        reply = reply.replace(" " + phrase, "").replace(phrase + " ", "").replace(phrase, "")
    return re.sub(r"\n{3,}", "\n\n", reply)


def build_draft(email: dict, cat: Category, conf: float, urgency_level: str, risk_level: str,
                kb: KBAnswer | None, kb_error: str | None, feature_hint: KBAnswer | None,
                thread_subject: str | None, rules: dict, rules_version: str,
                llm_reply: str | None = None, model: str = "template") -> Draft:
    flags = [f"email looks like a prompt injection: {s!r}" for s in guardrails.injection_signals(email["body"])]
    reasons = []
    if kb_error:
        reply, kb_status, next_action = "", "unavailable", f"Knowledge service unavailable ({kb_error}). Reply manually or retry."
        reasons.append("knowledge service unavailable - no draft written")
    else:
        kb_status = kb.status if kb else "not_needed"
        reply = template_reply(cat, email, kb, feature_hint, thread_subject)
        if llm_reply:
            problems = (guardrails.check_commitments(llm_reply).issues
                        + guardrails.check_links(llm_reply, ALLOWED_LINK_DOMAINS).issues)
            if problems:
                flags.extend(f"model draft rejected: {p}" for p in problems)
            else:
                reply = llm_reply
        reply = apply_style_rules(reply, rules)
        next_action = {
            Category.REFUND_CANCEL: "Billing team: review account and confirm refund/cancellation terms",
            Category.COMPLAINT: "Support lead: review and contact the customer",
            Category.QUOTE: "Sales: send scoping questions, then prepare a quote (Task 3)",
            Category.PROJECT_REQUEST: "Sales: book a scoping call, then Meeting-to-Scope (Task 3)",
            Category.FOLLOW_UP: "Check the status of the earlier request and reply with news",
        }.get(cat, "Send the reply" if kb_status == "answered" else "Wait for the product specialist's answer")

    commitments = guardrails.check_commitments(reply)
    links = guardrails.check_links(reply, ALLOWED_LINK_DOMAINS)
    if not commitments.ok:
        reasons.append("draft contains commitments: " + ", ".join(commitments.issues))
    if not links.ok:
        reasons.extend(links.issues)
    if cat in NEVER_AUTONOMOUS:
        reasons.append(f"{cat.value} always needs a human")
    if risk_level == "high":
        reasons.append("high risk")
    if urgency_level == "high":
        reasons.append("urgent")
    if kb_status == "insufficient_evidence":
        reasons.append("no evidence in the help center - sent to an expert")
    if flags:
        reasons.append("guardrail flags")
    if conf < 0.6:
        reasons.append(f"low classification confidence ({conf:.2f})")

    return Draft(
        email_id=email["id"], category=cat, category_confidence=conf, urgency=urgency_level, risk=risk_level,
        summary=guardrails.redact_pii(summarize(email["subject"], email["body"])), reply=reply,
        sources=[Source(title=s["title"], url=s["url"], score=s["score"]) for s in (kb.sources if kb else [])],
        kb_status=kb_status, needs_approval=True, approval_reasons=reasons, next_action=next_action,
        escalation_id=kb.escalation_id if kb else None, flags=flags,
        prompt_version=f"{TEMPLATE_VERSION}+{rules_version}", model=model)
