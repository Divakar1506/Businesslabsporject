"""Typed client for the Task 1 Knowledge Brain API. The Inbox Agent never imports Task 1 code.

- Retries connection errors and 5xx with backoff; never retries 4xx.
- Raises KBUnavailable when the service cannot be reached, so the agent shows a clear failure
  state instead of inventing a reply.
"""
import time
from dataclasses import dataclass, field

import httpx


class KBUnavailable(Exception):
    pass


@dataclass
class KBAnswer:
    status: str                      # answered | insufficient_evidence
    answer: str
    sources: list[dict] = field(default_factory=list)
    needs_human: bool = False
    retrieval_score: float = 0.0
    answer_mode: str = "none"
    escalation_id: int | None = None
    flags: list[str] = field(default_factory=list)


class KBClient:
    def __init__(self, base_url: str, token: str = "", timeout: float = 30.0, retries: int = 2,
                 http: httpx.Client | None = None, backoff: float = 0.5):
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.retries, self.backoff = retries, backoff
        self.http = http or httpx.Client(timeout=timeout)

    def _post(self, path: str, body: dict) -> dict:
        last = None
        for attempt in range(self.retries + 1):
            try:
                r = self.http.post(f"{self.base_url}{path}", json=body, headers=self.headers)
            except httpx.HTTPError as e:
                last = f"{type(e).__name__}: {e}"
            else:
                if r.status_code < 400:
                    return r.json()
                if r.status_code < 500:
                    raise ValueError(f"Knowledge Brain rejected the request ({r.status_code}): {r.text[:200]}")
                last = f"HTTP {r.status_code}"
            if attempt < self.retries:
                time.sleep(self.backoff * 2 ** attempt)
        raise KBUnavailable(f"Knowledge Brain unavailable at {self.base_url} ({last})")

    def query(self, question: str, escalate: bool = True, top_k: int = 5) -> KBAnswer:
        d = self._post("/query", {"question": question[:2000], "top_k": top_k, "source": "inbox",
                                  "escalate": escalate})
        return KBAnswer(status=d["status"], answer=d["answer"], sources=d["sources"], needs_human=d["needs_human"],
                        retrieval_score=d["retrieval_score"], answer_mode=d["answer_mode"],
                        escalation_id=d.get("escalation_id"), flags=d.get("flags", []))

    def escalate(self, question: str, reason: str) -> int:
        return self._post("/escalations", {"question": question[:2000], "reason": reason[:500], "source": "inbox"})["id"]

    def health(self) -> bool:
        try:
            return self.http.get(f"{self.base_url}/health", headers=self.headers).status_code == 200
        except httpx.HTTPError:
            return False
