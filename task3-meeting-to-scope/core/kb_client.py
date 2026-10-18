"""Typed client for the Task 1 Knowledge Brain API (Task 3 never imports Task 1 code)."""
import time

import httpx


class KBUnavailable(Exception):
    pass


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
                last = f"{type(e).__name__}"
            else:
                if r.status_code < 400:
                    return r.json()
                if r.status_code < 500:
                    raise ValueError(f"Knowledge Brain rejected the request ({r.status_code})")
                last = f"HTTP {r.status_code}"
            if attempt < self.retries:
                time.sleep(self.backoff * 2 ** attempt)
        raise KBUnavailable(f"Knowledge Brain unavailable at {self.base_url} ({last})")

    def query(self, question: str) -> dict:
        """Feasibility lookups never create expert tasks; a human decides what to escalate."""
        return self._post("/query", {"question": question[:2000], "top_k": 5, "source": "scope", "escalate": False})

    def escalate(self, question: str, reason: str) -> int:
        return self._post("/escalations", {"question": question[:2000], "reason": reason[:500], "source": "scope"})["id"]

    def health(self) -> bool:
        try:
            return self.http.get(f"{self.base_url}/health", headers=self.headers).status_code == 200
        except httpx.HTTPError:
            return False
