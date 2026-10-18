"""Agent 2 - Product Feasibility. Input: requirements. Output: each requirement with a Feasibility.

Asks the Knowledge Brain (Task 1 API) whether the product already does it:
- existing_config      documented feature, strong match: configuration only
- existing_limited     documented but partial (weaker match, or the evidence states a limit/add-on)
- custom               nothing documented: custom work. Not finding it is NOT proof it doesn't exist,
                       so the note asks for an expert check before quoting.
- needs_clarification  the requirement itself is unclear, or the Knowledge Brain was unreachable
Service work (content, design) is custom by nature and skips the lookup.
"""
from core.kb_client import KBClient, KBUnavailable
from core.models import Evidence, Feasibility, Requirement
from core.topics import BY_KEY

# Knowledge Brain answers from 0.68 (its abstain threshold); 0.80+ is treated as a clear feature match.
STRONG_MATCH = 0.80


def assess(req: Requirement, kb: KBClient) -> Feasibility:
    topic = BY_KEY.get(req.topic)
    if topic and topic.profile in ("content", "design"):
        return Feasibility(cls="custom", note=f"Service work ({topic.profile}), not a product setting.")
    try:
        r = kb.query(topic.kb_question if topic else req.description)
    except KBUnavailable as e:
        return Feasibility(cls="needs_clarification", note=f"Could not check: {e}. Re-run when the Knowledge Brain is up.")
    except ValueError as e:
        return Feasibility(cls="needs_clarification", note=str(e))
    ev = [Evidence(title=s["title"], url=s["url"], score=s["score"]) for s in r.get("sources", [])[:3]]
    if req.status == "unclear":
        return Feasibility(cls="needs_clarification", evidence=ev,
                           note="Requirement not confirmed by the client yet; not estimated.")
    if r["status"] != "answered":
        return Feasibility(cls="custom", evidence=ev,
                           note=f"No documented feature found (best match {r['retrieval_score']:.2f}). "
                                f"Confirm with a product expert before quoting custom work.")
    best = r["retrieval_score"]
    # Match strength alone decides: limit words in quoted help text ("only", "requires") proved too noisy.
    if best < STRONG_MATCH:
        return Feasibility(cls="existing_limited", evidence=ev,
                           note=f"Documented, but only a partial match ({best:.2f}) or with stated limits: "
                                f"configuration plus some adjustment.")
    return Feasibility(cls="existing_config", evidence=ev,
                       note=f"Documented feature ({ev[0].title if ev else 'help center'}, match {best:.2f}): "
                            f"configuration, no custom code.")


def run(reqs: list[Requirement], kb: KBClient) -> list[Requirement]:
    out = []
    for r in reqs:
        r = r.model_copy(deep=True)
        r.feasibility = assess(r, kb)
        out.append(r)
    return out
