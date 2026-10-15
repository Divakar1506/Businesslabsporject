"""Question answering: retrieve -> abstain if weak -> answer with citations.

Answer modes:
- llm        : the gateway's model wrote it (Claude or Ollama), grounded in numbered evidence.
- extractive : offline/$0, or the model failed - the best matching sentences quoted with citations.
- none       : not enough evidence; the question should go to a human expert.
"""
import re
import threading
import time
import uuid
from pathlib import Path

import settings
from ingestion.store import KBStore
from retrieval import embedder
from retrieval.chunker import sentences
from retrieval.index import HybridIndex, terms
from shared import guardrails
from shared.llm_gateway import BudgetExceeded, Gateway

from .escalations import Escalations

PROMPT_VERSION = "kb-answer-v1"
SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "answer_v1.md").read_text(encoding="utf-8")
NO_ANSWER = ("I could not find this in the help center. I have flagged it for a human expert "
             "so you get a correct answer instead of a guess.")
ALLOWED_LINK_DOMAINS = {"support.brilliantdirectories.com", "brilliantdirectories.com", "s3.amazonaws.com"}


class Brain:
    def __init__(self, store: KBStore, gateway: Gateway | None = None, embed_model: str | None = None):
        self.store = store
        self.gateway = gateway or Gateway()
        self.embedder = embedder.get(embed_model or settings.EMBED_MODEL)
        self.escalations = Escalations(store)
        self._index: HybridIndex | None = None
        self._lock = threading.Lock()

    @property
    def index(self) -> HybridIndex:
        with self._lock:
            if self._index is None:
                self._index = HybridIndex(self.store.active_chunks(), self.embedder.dim)
            return self._index

    def reload(self) -> None:
        """Call after an ingestion run so new, changed and removed articles take effect."""
        with self._lock:
            self._index = None

    # ------------------------------------------------------------------
    def query(self, question: str, top_k: int = 5, context: str | None = None, escalate: bool = True,
              min_score: float | None = None) -> dict:
        t0 = time.perf_counter()
        rid = uuid.uuid4().hex[:16]
        flags = [f"question looks like a prompt injection: {s!r}" for s in guardrails.injection_signals(question)]
        raw = self.index.search(question, self.embedder.encode_query(question), k=top_k)
        # Retrieved text is data. A chunk that tries to give instructions is dropped from the evidence.
        hits = []
        for h in raw:
            sig = guardrails.injection_signals(h["text"])
            if sig:
                flags.append(f"dropped evidence from {h['url']}: contains {sig[0]!r}")
            else:
                hits.append(h)
        best = max((h["vector_score"] for h in hits), default=0.0)
        threshold = settings.MIN_SCORE if min_score is None else min_score

        def finish(**kw):
            return {"request_id": rid, "retrieval_score": round(best, 4), "flags": flags, "escalation_id": None,
                    "latency_ms": round((time.perf_counter() - t0) * 1000, 1), **kw}

        def abstain(reason: str, mode: str, model=None):
            esc = None
            if escalate:
                esc = self.escalations.create(question, reason, [{"title": h["title"], "url": h["url"],
                                                                  "score": h["vector_score"]} for h in raw[:3]])
            return finish(answer=NO_ANSWER, status="insufficient_evidence", needs_human=True, sources=[], images=[],
                          answer_mode=mode, model=model, escalation_id=esc["id"] if esc else None)

        if not hits or best < threshold:
            return abstain(f"best evidence score {best:.3f} below {threshold}", "none")

        answer, mode, model = self._llm_answer(question, hits, context, rid)
        if answer == "INSUFFICIENT_EVIDENCE":
            return abstain("model judged the evidence insufficient", "llm", model)
        if answer is not None:
            problems = (guardrails.check_citations(answer, len(hits)).issues
                        + guardrails.check_links(answer, ALLOWED_LINK_DOMAINS).issues)
            if problems:  # an unverifiable model answer is replaced by quoted evidence
                flags.extend(f"model answer rejected: {p}" for p in problems)
                answer = None
        if answer is None:
            answer, mode = self._extractive_answer(question, hits), "extractive"
        return finish(answer=answer, status="answered", needs_human=False, sources=_sources(hits),
                      images=_images(hits), answer_mode=mode, model=model)

    def _llm_answer(self, question, hits, context, rid):
        evidence = "\n\n".join(f"[{i}] {h['title']} — {h['heading'] or 'Intro'}\n{h['text']}"
                               for i, h in enumerate(hits, 1))
        user = f"<evidence>\n{evidence}\n</evidence>\n\n"
        if context:
            user += f"<customer_context>\n{context}\n</customer_context>\n\n"
        user += f"Question: {question}"
        try:
            r = self.gateway.call(task="knowledge", endpoint="query", system=SYSTEM_PROMPT, max_tokens=600,
                                  messages=[{"role": "user", "content": user}], prompt_version=PROMPT_VERSION,
                                  request_id=rid)
        except BudgetExceeded:
            return None, "extractive", None
        except Exception:  # model unreachable: fall back to quoting the evidence, never invent
            return None, "extractive", None
        if r.model.startswith("offline/fake"):
            return None, "extractive", r.model
        text = r.text.strip()
        return ("INSUFFICIENT_EVIDENCE" if text.startswith("INSUFFICIENT_EVIDENCE") else text), "llm", r.model

    @staticmethod
    def _extractive_answer(question: str, hits: list[dict], n: int = 3) -> str:
        """Pick the sentences that share the most question terms, from the top chunks. $0, no model."""
        q = set(terms(question))
        scored = []
        for ci, h in enumerate(hits[:3]):
            for si, line in enumerate(x for x in h["text"].split("\n") if x.strip()):
                for sent in (sentences(line) or [line]):
                    overlap = len(q & set(terms(sent)))
                    if overlap and len(sent) > 20:
                        # more shared terms first, then better chunk, then earlier in the chunk
                        scored.append((-overlap, ci, si, sent.lstrip("- ").strip(), ci + 1))
        if not scored:
            first = hits[0]["text"].split("\n")[0]
            return f"From the help center: {first} [1]"
        scored.sort()
        picked = sorted(scored[:n], key=lambda x: (x[1], x[2]))
        lines = [f"- {s} [{cite}]" for _, _, _, s, cite in picked]
        return "From the help center:\n" + "\n".join(lines)


def _sources(hits: list[dict]) -> list[dict]:
    best: dict[str, dict] = {}
    for h in hits:
        cur = best.get(h["article_id"])
        if cur is None or h["vector_score"] > cur["score"]:
            best[h["article_id"]] = {"title": h["title"], "url": h["url"], "section": h["heading"] or "",
                                     "score": h["vector_score"],
                                     "kind": "expert" if h["article_id"].startswith("expert-") else "article"}
    return sorted(best.values(), key=lambda s: -s["score"])


def _images(hits: list[dict], limit: int = 6) -> list[dict]:
    out, seen = [], set()
    for h in hits:
        for src in h["image_refs"]:
            if src not in seen:
                seen.add(src)
                out.append({"src": src, "article_url": h["url"], "section": h["heading"] or ""})
    return out[:limit]


_CITE = re.compile(r"\[(\d+)\]")
