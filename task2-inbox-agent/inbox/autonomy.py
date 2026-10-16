"""The 20-in-a-row rule, made precise and auditable.

Definitions (see DESIGN notes in README):
- "Same kind"      = the same category (how_to, troubleshooting, follow_up, ...).
- "Without change" = the sent text equals the draft after normalising whitespace and line endings
                     (normalise() below). Clicking Approve is not enough; the texts are compared.

Rules:
1. Each unchanged human send adds 1 to the category's streak. An edit, a rejection or an escalation
   resets it to 0.
2. At AUTONOMY_STREAK (20) the category becomes *eligible*. An admin must still switch it *on*.
3. Even when on, a draft is sent automatically only if it is low risk, not urgent, grounded in the
   Knowledge Brain, has no guardrail flags, and its category is not in NEVER_AUTONOMOUS.
4. Kill switch: autonomy turns off by itself when a human changes an auto-eligible draft, or when the
   edit rate over the last AUTONOMY_WINDOW reviews rises above AUTONOMY_MAX_EDIT_RATE.
Every change is written to autonomy_events.
"""
import re

import settings

from .models import NEVER_AUTONOMOUS, Category
from .store import InboxStore


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\r\n", "\n")).strip()


def unchanged(draft_text: str, final_text: str) -> bool:
    return normalise(draft_text) == normalise(final_text)


def record_outcome(store: InboxStore, category: str, was_unchanged: bool, action: str) -> dict:
    """Update the streak after a human review. Returns the new autonomy state."""
    st = store.autonomy(category)
    if was_unchanged and action == "approve":
        streak = st["streak"] + 1
        store.set_autonomy(category, streak=streak)
        if streak >= settings.AUTONOMY_STREAK and not st["eligible"] and Category(category) not in NEVER_AUTONOMOUS:
            store.set_autonomy(category, eligible=1)
            store.log_event(category, "eligible", f"{streak} unchanged sends in a row")
    else:
        if st["streak"]:
            store.log_event(category, "streak_reset", f"{action} after {st['streak']} unchanged")
        store.set_autonomy(category, streak=0)
        if st["enabled"]:
            store.set_autonomy(category, enabled=0, eligible=0)
            store.log_event(category, "disabled", f"kill switch: human {action} on an autonomous category")
    _check_edit_rate(store, category)
    return store.autonomy(category)


def _check_edit_rate(store: InboxStore, category: str) -> None:
    recent = store.reviews(category, settings.AUTONOMY_WINDOW)
    if len(recent) < settings.AUTONOMY_WINDOW:
        return
    rate = sum(1 for r in recent if not r["unchanged"]) / len(recent)
    st = store.autonomy(category)
    if st["enabled"] and rate > settings.AUTONOMY_MAX_EDIT_RATE:
        store.set_autonomy(category, enabled=0, eligible=0, streak=0)
        store.log_event(category, "disabled", f"kill switch: edit rate {rate:.0%} over last {len(recent)}")


def enable(store: InboxStore, category: str, admin: str) -> None:
    st = store.autonomy(category)
    if Category(category) in NEVER_AUTONOMOUS:
        raise ValueError(f"{category} always needs a human")
    if not st["eligible"]:
        raise ValueError(f"{category} is not eligible: {st['streak']}/{settings.AUTONOMY_STREAK} unchanged sends")
    store.set_autonomy(category, enabled=1)
    store.log_event(category, "enabled", f"by {admin}")


def disable(store: InboxStore, category: str, admin: str) -> None:
    store.set_autonomy(category, enabled=0)
    store.log_event(category, "disabled", f"manually by {admin}")


def can_auto_send(store: InboxStore, draft: dict) -> tuple[bool, str]:
    cat = draft["category"]
    st = store.autonomy(cat)
    if not st["enabled"]:
        return False, "autonomy off for this category"
    blockers = [r for r in draft["approval_reasons"] if not r.startswith("low classification")]
    if Category(cat) in NEVER_AUTONOMOUS:
        return False, f"{cat} always needs a human"
    if draft["risk"] != "low" or draft["urgency"] == "high":
        return False, "not low risk"
    if draft["kb_status"] not in ("answered", "not_needed") or draft["flags"]:
        return False, "not grounded or flagged"
    if draft["category_confidence"] < 0.8:
        return False, "classification not confident enough"
    if blockers:
        return False, "; ".join(blockers)
    return True, "eligible and enabled"
