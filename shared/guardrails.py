"""Guardrails shared by all three tasks. Pure Python, no model calls, $0.

- check_input       : length, empty, control characters
- injection_signals : phrases that try to change the system's instructions (in questions, emails,
                      transcripts or retrieved documents). Detection only - the caller decides what to do.
- redact_pii        : masks emails, phone numbers, card numbers before anything is logged or stored
- check_citations   : every [n] in an answer must point to evidence that was actually given
- check_links       : links in an answer must come from allowed domains
- check_commitments : flags promises (refunds, prices, dates, guarantees) that need human approval
"""
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

MAX_INPUT_CHARS = 8000

_INJECTION = [re.compile(p, re.I) for p in (
    r"\bignore (all |any |the )?(previous|prior|above|earlier) (instructions|prompts?|rules)",
    r"\bdisregard (all |any |the )?(previous|prior|above|system) (instructions|prompts?|rules)",
    r"\bforget (everything|all|your) (instructions|rules|you were told)",
    r"\byou are now (a|an|in) ",
    r"\b(reveal|show|print|repeat) (me )?(your|the) (system prompt|instructions|hidden prompt)",
    r"\b(new|updated) (system )?instructions?\s*:",
    r"\bact as (an? )?(admin|administrator|developer mode|dan)\b",
    r"\bdeveloper mode\b",
    r"<\s*/?\s*(system|assistant|instructions?)\s*>",
    r"\bdo not (cite|mention) (the )?sources\b",
)]

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
# Any run of digits with spaces, dots, dashes or brackets; counted as a phone when it has 9-15 digits
# (covers +1 555 123 4567, +91 98765 43210, (020) 7946 0958; dates and years have fewer digits).
_PHONE = re.compile(r"(?<![\w+])\+?\(?\d[\d ().-]{7,}\d(?!\w)")
_CITE = re.compile(r"\[(\d+)\]")
_URL = re.compile(r"https?://[^\s)\]>\"']+")
_COMMIT = [(re.compile(p, re.I), label) for p, label in (
    (r"\b(we will|we'll|i will|i'll) (refund|reimburse|credit)\b", "refund promise"),
    (r"\b(full|partial) refund (has been|will be) (issued|processed)\b", "refund promise"),
    (r"\bguarantee[sd]?\b", "guarantee"),
    (r"\b(free of charge|no charge|at no cost)\b", "free work promise"),
    (r"[$€£₹]\s?\d", "price"),
    (r"\b(by|before|on) (monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|\d{1,2}(st|nd|rd|th)?)\b",
     "delivery date"),
)]


@dataclass
class Check:
    ok: bool
    issues: list[str] = field(default_factory=list)


def check_input(text: str, max_chars: int = MAX_INPUT_CHARS) -> Check:
    issues = []
    if not text or not text.strip():
        issues.append("empty input")
    if len(text) > max_chars:
        issues.append(f"input longer than {max_chars} characters")
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", text or ""):
        issues.append("control characters")
    return Check(not issues, issues)


def injection_signals(text: str) -> list[str]:
    """Matched phrases, empty if none. Retrieved documents and user input are data, never instructions."""
    return [m.group(0) for p in _INJECTION for m in [p.search(text or "")] if m]


def redact_pii(text: str) -> str:
    text = _EMAIL.sub("[email]", text or "")
    text = _CARD.sub(lambda m: "[card]" if len(re.sub(r"\D", "", m.group(0))) >= 13 else m.group(0), text)
    return _PHONE.sub(lambda m: "[phone]" if 9 <= len(re.sub(r"\D", "", m.group(0))) <= 15 else m.group(0), text)


def check_citations(answer: str, n_evidence: int, require: bool = True) -> Check:
    cited = [int(x) for x in _CITE.findall(answer or "")]
    issues = [f"citation [{c}] has no matching evidence" for c in sorted(set(cited)) if not 1 <= c <= n_evidence]
    if require and not cited:
        issues.append("answer has no citations")
    return Check(not issues, issues)


def check_links(answer: str, allowed_domains: set[str]) -> Check:
    bad = [u for u in _URL.findall(answer or "")
           if not any(urlparse(u).netloc == d or urlparse(u).netloc.endswith("." + d) for d in allowed_domains)]
    return Check(not bad, [f"link not allowed: {u}" for u in bad])


def check_commitments(text: str) -> Check:
    """Promises that only a human may make (used by the Inbox Agent and proposals)."""
    found = sorted({label for p, label in _COMMIT if p.search(text or "")})
    return Check(not found, found)
