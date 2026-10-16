"""Learning from human edits - measurable and undoable, no model retraining.

1. mine(): for each category, find sentences of our drafts that reviewers deleted in at least
   MIN_TIMES edits and in at least MIN_SHARE of that category's edits.
2. propose(): turn them into a new, inactive style-rules version (rules-vN).
3. evaluate(): replay every past review with the candidate rules:
     - edited reviews: does the draft get closer to what the human sent? (difflib similarity)
     - unchanged reviews: the draft must stay exactly the same (no regressions).
4. adopt(): activate only if similarity improved and nothing approved unchanged was altered.
   Rolling back = store.activate_rules(<older version>).
"""
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher

from .autonomy import normalise
from .drafter import apply_style_rules
from .store import InboxStore

MIN_TIMES = 3
MIN_SHARE = 0.6
_SENT = re.compile(r"(?<=[.!?])\s+|\n+")


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT.split(text or "") if len(s.strip()) > 15]


def mine(store: InboxStore) -> dict[str, list[str]]:
    deleted: dict[str, Counter] = defaultdict(Counter)
    edits: Counter = Counter()
    for r in store.reviews():
        if r["action"] != "edit" or r["unchanged"]:
            continue
        edits[r["category"]] += 1
        final = normalise(r["final_text"])
        for s in set(sentences(r["draft_text"])):
            if normalise(s) not in final:
                deleted[r["category"]][s] += 1
    return {cat: sorted(s for s, n in cnt.items() if n >= MIN_TIMES and n / edits[cat] >= MIN_SHARE)
            for cat, cnt in deleted.items() if any(n >= MIN_TIMES for n in cnt.values())}


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalise(a), normalise(b)).ratio()


def evaluate(store: InboxStore, rules: dict) -> dict:
    before, after, regressions, n_edits = [], [], 0, 0
    for r in store.reviews():
        new_draft = apply_style_rules(r["draft_text"] or "", rules.get(r["category"], {}))
        if r["unchanged"]:
            regressions += normalise(new_draft) != normalise(r["draft_text"])
        elif r["action"] == "edit":
            n_edits += 1
            before.append(_similarity(r["draft_text"], r["final_text"]))
            after.append(_similarity(new_draft, r["final_text"]))
    mean = lambda xs: round(sum(xs) / len(xs), 4) if xs else 0.0  # noqa: E731
    return {"edited_reviews": n_edits, "similarity_before": mean(before), "similarity_after": mean(after),
            "regressions_on_unchanged": regressions}


def propose(store: InboxStore) -> dict | None:
    found = mine(store)
    if not found:
        return None
    _, current = store.active_rules()
    rules = {cat: dict(v) for cat, v in current.items()}
    for cat, sents in found.items():
        merged = sorted(set(rules.get(cat, {}).get("remove_sentences", [])) | set(sents))
        rules[cat] = {**rules.get(cat, {}), "remove_sentences": merged}
    if rules == current:
        return None
    version = f"rules-v{len(store.all_rules())}"
    result = evaluate(store, rules)
    note = (f"removes {sum(len(v) for v in found.values())} sentence(s) reviewers keep deleting; "
            f"similarity {result['similarity_before']} -> {result['similarity_after']}, "
            f"regressions {result['regressions_on_unchanged']}")
    store.add_rules(version, rules, note)
    return {"version": version, "rules": rules, "evaluation": result}


def adopt(store: InboxStore, proposal: dict) -> bool:
    ev = proposal["evaluation"]
    if ev["similarity_after"] > ev["similarity_before"] and ev["regressions_on_unchanged"] == 0:
        store.activate_rules(proposal["version"])
        return True
    return False
