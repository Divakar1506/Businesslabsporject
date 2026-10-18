"""Agent 1 - Transcript Analyst. Input: transcript text. Output: MeetingFacts (validated).

Staged, so long meetings work:
1. parse into turns (the original text is kept untouched)
2. split into overlapping sections (core.transcript.sections)
3. per section, turn every sentence into events: request / unclear / remove / modify / confirm
   for a topic, plus deadline, budget, responsibilities, dependencies, commitments
4. merge events from all sections (overlap duplicates dropped) into one record per topic;
   the latest statement wins, and the full history stays in the sources
5. validate: every quote must appear word for word in its turn
Rules are deterministic and free. What the client asked for (explicit) is kept apart from
what only the consultant suggested (inferred).
"""
import re
from dataclasses import dataclass

from shared import guardrails

from core.models import MeetingFacts, Requirement, SourceRef, Turn
from core.topics import BY_KEY, TOPICS, Topic
from core.transcript import parse, sections, sentences

REQ = re.compile(r"\b(we|i)\s*(really\s+)?(want|need|would like|'d like|have to)\b|\bwe'?d like\b|\bneeds?\b|\bshould\b"
                 r"|\bmust\b|\bcould you\b|\bcan you\b|\bwould be nice\b|\bwould help\b|\badd\b|\bhas to\b", re.I)
MUST = re.compile(r"\bmust\b|critical|has to|have to|essential|non-negotiable|top priority|the big one|main thing", re.I)
OPTIONAL = re.compile(r"nice to have|would be nice|\boptional\b", re.I)
UNCLEAR = re.compile(r"\bmaybe\b|not sure|\bmight\b|perhaps|we'll see|something like|possibly|\blater\b|open question|unclear", re.I)
REMOVE = re.compile(r"\bdrop\b|\bremove|no longer|don'?t need|do not need|\bscrap\b|forget about|^no\b|\bis out\b|not needed", re.I)
MODIFY = re.compile(r"\binstead\b|rather than|now we want|\bactually\b|\bchanges?\b", re.I)
CONFIRM = re.compile(r"to confirm|\bagreed\b|stay (the same|as agreed)|are in\b", re.I)
PRONOUN_START = re.compile(r"^(it|that|this|those|these|they)\b", re.I)
REPLY_START = re.compile(r"^(yes|yeah|no|ok|okay|honestly|those|that|it|this|sure)\b", re.I)
DATE = re.compile(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+"
                  r"\d{1,2}(?:st|nd|rd|th)?(?:,?\s*\d{4})?", re.I)
DEADLINE_CUE = re.compile(r"\b(before|by|live|deadline|launch|go live|campaign)\b", re.I)
MONEY = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?k?(?:\s*(?:-|to|and)\s*\$?\s?\d[\d,]*k?)?", re.I)
RESPONSIBILITY = re.compile(r"\b(i|we|our \w+)\s+(can|will|'ll|could)\s+(send|give|provide|get)|\bare ours\b|i'?ll give", re.I)
DEPENDENCY = re.compile(r"api (access|keys?)|external dependency|third[- ]party|access to", re.I)
COMMITMENT = re.compile(r"\bi'?ll send\b|\bi will send\b|\bwe'?ll deliver\b", re.I)


@dataclass
class Event:
    kind: str           # request | unclear | remove | modify | confirm | mention
    topic: str
    priority: str | None
    sentence: str
    turn: Turn


def topics_in(sentence: str, all_matches: bool = False) -> list[Topic]:
    """Topics in a sentence. Single mode: the topic mentioned earliest (ties: catalogue order)."""
    found = []
    for order, t in enumerate(TOPICS):
        m = t.pattern.search(sentence)
        if m:
            found.append((m.start(), order, t))
    if not found:
        return []
    found.sort()
    if all_matches:
        keys = {t.key for _, _, t in found}
        # a specific filter topic already covers the generic "filter" word
        return [t for _, _, t in found if not (t.key == "FILTERS" and keys & {"PRICE_FILTER", "VERIFIED_FILTER"}
                                                and not re.search(r"\bfilters\b", sentence, re.I))]
    return [found[0][2]]


def _priority(s: str) -> str:
    if MUST.search(s):
        return "must"
    if OPTIONAL.search(s) or UNCLEAR.search(s):
        return "optional"
    return "should"


def extract_events(section: list[Turn], current: dict, done: set) -> list[Event]:
    """`current` carries the topic under discussion across sections; `done` holds turns already read
    (section overlaps), so each turn is interpreted once, with the context it really had."""
    events = []
    for turn in section:
        if turn.line in done:
            continue
        done.add(turn.line)
        for s in sentences(turn.text):
            if guardrails.injection_signals(s):
                continue  # quoted instructions are meeting content, never requirements
            if (DATE.search(s) and DEADLINE_CUE.search(s)) or MONEY.search(s):
                continue  # deadlines and budgets are constraints, handled separately
            if PRONOUN_START.search(s) and current.get("topic"):
                tops = [BY_KEY[current["topic"]]]  # "It should be there" = the topic just discussed
            else:
                tops = topics_in(s)
            if turn.role == "consultant" and CONFIRM.search(s):
                for t in topics_in(s, all_matches=True):
                    kind = ("remove" if REMOVE.search(s) or re.search(r"\bout\b", s) else
                            "confirm" if re.search(r"\bconfirmed\b|\bare in\b|\bstay\b|\bagreed\b", s, re.I) else
                            "unclear" if UNCLEAR.search(s) else "confirm")
                    events.append(Event(kind, t.key, None, s, turn))
                continue
            if not tops:
                # a short reply like "Yes, those three are a must." refines the topic just discussed
                if (turn.role == "client" and current.get("topic") and REPLY_START.search(s)
                        and (MUST.search(s) or UNCLEAR.search(s))):
                    kind = "unclear" if UNCLEAR.search(s) and not MUST.search(s) else "request"
                    events.append(Event(kind, current["topic"], _priority(s), s, turn))
                continue
            t = tops[0]
            current["topic"] = t.key
            if REMOVE.search(s):
                kind = "remove"
            elif UNCLEAR.search(s) and not MUST.search(s):
                kind = "unclear"
            elif turn.role == "client" and MODIFY.search(s) and REQ.search(s):
                kind = "modify"
            elif REQ.search(s) or (turn.role == "consultant" and "?" in s):
                kind = "request"
            else:
                kind = "mention"
            events.append(Event(kind, t.key, _priority(s), s, turn))
    return events


def analyse(meeting_id: str, text: str, max_tokens: int = 500) -> MeetingFacts:
    turns = parse(text)
    flags = []
    if not turns:
        return MeetingFacts(meeting_id=meeting_id, requirements=[], flags=["no speaker turns found in the transcript"])
    if not any(t.role == "client" for t in turns):
        flags.append("no client speaker found: requirements need a '(Client)' label")
    for t in turns:
        for sig in guardrails.injection_signals(t.text):
            flags.append(f"[{t.ts}] {t.speaker} said something that looks like an instruction to the AI ({sig!r}); "
                         f"treated as meeting content, not obeyed")

    # stages 2-4: per-section extraction, merged with duplicates from overlaps removed
    current, done, seen, events = {}, set(), set(), []
    for sec in sections(turns, max_tokens):
        for ev in extract_events(sec, current, done):
            key = (ev.turn.line, ev.sentence, ev.topic, ev.kind)
            if key not in seen:
                seen.add(key)
                events.append(ev)

    def ref(turn: Turn, quote: str) -> SourceRef:
        return SourceRef(meeting_id=meeting_id, ts=turn.ts, speaker=turn.speaker, quote=quote)

    reqs: dict[str, dict] = {}
    for ev in events:
        if ev.kind == "mention" and ev.topic not in reqs:
            continue
        r = reqs.setdefault(ev.topic, {"status": None, "priority": "should", "explicit": False, "sources": [],
                                       "desc": [], "modified": False})
        r["sources"].append(ref(ev.turn, ev.sentence))
        if ev.turn.role == "client" and ev.kind in ("request", "modify", "unclear"):
            r["explicit"] = True
            if ev.sentence not in r["desc"] and ev.kind != "unclear" or not r["desc"]:
                r["desc"].append(ev.sentence)
        if ev.kind == "request":
            r["status"] = "confirmed" if ev.turn.role == "client" else (r["status"] or "unclear")
            if ev.turn.role == "client" and ev.priority:
                r["priority"] = ev.priority if not (r["priority"] == "must" and ev.priority == "should") else "must"
        elif ev.kind == "modify":
            r["status"], r["modified"] = "confirmed", True
            r["desc"] = [ev.sentence]
        elif ev.kind == "unclear":
            # a consultant remark never downgrades what the client said
            if ev.turn.role == "client" or r["status"] is None:
                r["status"], r["priority"] = "unclear", "optional"
        elif ev.kind == "remove":
            r["status"] = "removed"
        elif ev.kind == "confirm" and r["status"] in (None, "confirmed"):
            r["status"] = "confirmed"

    requirements, out_of_scope = [], []
    for key, r in reqs.items():
        if r["status"] is None:
            continue
        t = BY_KEY[key]
        req = Requirement(
            id=f"REQ-{key.replace('_', '-')}", topic=key, title=t.title,
            description=" ".join(r["desc"][:3]) or r["sources"][0].quote,
            status=r["status"], priority=r["priority"] if r["status"] != "unclear" else "optional",
            explicit=r["explicit"], sources=r["sources"],
            acceptance_criteria=[t.acceptance] + (["Exact behaviour to be confirmed with the client."]
                                                  if r["status"] == "unclear" else []))
        req.dependencies = [ev.sentence for ev in events if ev.topic == key and DEPENDENCY.search(ev.sentence)]
        (out_of_scope if r["status"] == "removed" else requirements).append(req)

    # dependencies stated in a turn right after a topic (e.g. "I'll need your ShipStation API access")
    for turn in turns:
        for s in sentences(turn.text):
            if DEPENDENCY.search(s):
                for t in topics_in(s, all_matches=True):
                    for req in requirements:
                        if req.topic == t.key and s not in req.dependencies:
                            req.dependencies.append(s)

    deadline = deadline_src = budget = None
    responsibilities, commitments = [], []
    prev = None
    for turn in turns:
        for s in sentences(turn.text):
            if turn.role == "client" and DATE.search(s) and DEADLINE_CUE.search(s):
                deadline, deadline_src = DATE.search(s).group(0), ref(turn, s)
            if turn.role == "client" and MONEY.search(s) and (re.search(r"budget", s, re.I) or (prev and re.search(r"budget", prev.text, re.I))
                                                             or re.search(r"\b(around|between|about)\b", s, re.I)):
                budget = s
            if turn.role == "client" and RESPONSIBILITY.search(s):
                responsibilities.append(s)
            if turn.role == "consultant" and COMMITMENT.search(s):
                commitments.append(f"[{turn.ts}] {s}")
        prev = turn

    open_q = [f"{r.title}: {r.description}" for r in requirements if r.status == "unclear"]
    facts = MeetingFacts(meeting_id=meeting_id, requirements=requirements, deadline=deadline,
                         deadline_source=deadline_src, budget=budget, out_of_scope=out_of_scope,
                         client_responsibilities=responsibilities, open_questions=open_q,
                         consultant_commitments=commitments, flags=flags)
    validate(facts, turns)
    return facts


def validate(facts: MeetingFacts, turns: list[Turn]) -> None:
    """Every quote must be traceable to the transcript - otherwise the record is rejected."""
    by_ts = {}
    for t in turns:
        by_ts.setdefault(t.ts, []).append(t.text)
    for r in facts.requirements + facts.out_of_scope:
        for s in r.sources:
            if not any(s.quote in text for text in by_ts.get(s.ts, [])):
                raise ValueError(f"{r.id}: quote not found at [{s.ts}]: {s.quote!r}")
