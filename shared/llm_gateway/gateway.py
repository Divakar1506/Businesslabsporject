"""Single entry point for every LLM call in the project.

Each call: picks the model (budget policy), caches the system prompt, calls the
client for the current LLM_MODE (offline by default, ollama, or claude), streaming
or not, then logs tokens, cost, latency and status to the usage store.
"""
import os
import time
import uuid
from dataclasses import dataclass, field

from . import pricing, tokens
from .budget import BudgetExceeded, BudgetPolicy
from .providers import current_mode, make_client
from .store import UsageStore


@dataclass
class Result:
    text: str
    model: str
    request_id: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    stop_reason: str | None = None
    note: str = ""
    ttft_ms: float | None = None


@dataclass
class _Prepared:
    params: dict
    requested_model: str | None
    est_input_tokens: int
    reason: str
    meta: dict = field(default_factory=dict)


class Gateway:
    def __init__(self, store: UsageStore | None = None, client=None, default_model: str | None = None,
                 mode: str | None = None):
        """client: inject any SDK-shaped client (tests use a fake; it is treated as paid Claude)."""
        self.store = store or UsageStore(os.environ.get("USAGE_DB", "data/usage.db"))
        self.mode = "custom" if client is not None else (mode or current_mode())
        self.paid = self.mode in ("claude", "custom")
        self.default_model = default_model or os.environ.get("DEFAULT_MODEL", "claude-opus-5-5")
        self.policy = BudgetPolicy(self.store, self.default_model)
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = make_client(self.mode)
        return self._client

    # ------------------------------------------------------------------
    def _prepare(self, task, endpoint, messages, system, max_tokens, model, prompt_version, effort, request_id):
        meta = {"task": task, "endpoint": endpoint, "prompt_version": prompt_version,
                "request_id": request_id or uuid.uuid4().hex[:16]}
        est = tokens.estimate(tokens.messages_text(system, messages))
        try:
            decision = self.policy.decide(task, model, est, max_tokens, paid=self.paid)
        except BudgetExceeded as e:
            self.store.record(**meta, model=model or "-", requested_model=model, est_input_tokens=est,
                              status="blocked", note=str(e))
            raise
        params = {"model": decision.model, "max_tokens": max_tokens, "messages": messages}
        if system:
            # Stable system prompt first, marked for caching: repeated calls read it at ~0.05-0.1x price.
            params["system"] = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        if effort:
            params["output_config"] = {"effort": effort}
        return _Prepared(params, model, est, decision.reason, meta)

    def _finish(self, prep: _Prepared, message, started: float, ttft_ms: float | None = None) -> Result:
        u = message.usage
        usage = {"input_tokens": getattr(u, "input_tokens", 0) or 0,
                 "output_tokens": getattr(u, "output_tokens", 0) or 0,
                 "cache_read_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
                 "cache_write_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0}
        model = getattr(message, "model", None) or prep.params["model"]  # offline/ollama report their own name
        cost = pricing.call_cost(model, **usage)
        latency = (time.perf_counter() - started) * 1000
        prompt = usage["input_tokens"] + usage["cache_read_tokens"] + usage["cache_write_tokens"]
        note = "; ".join(n for n in (prep.reason, "" if pricing.known(model) else "unknown model price") if n)
        self.store.record(**prep.meta, model=model, requested_model=prep.requested_model, **usage,
                          est_input_tokens=prep.est_input_tokens, cost_usd=cost, latency_ms=latency,
                          ttft_ms=ttft_ms, downgraded=int(bool(prep.reason)),
                          context_pct=pricing.context_pct(model, prompt), status="ok", note=note)
        self.policy.after_call(prep.meta["task"])
        text = "".join(b.text for b in message.content if getattr(b, "type", "") == "text")
        return Result(text, model, prep.meta["request_id"], **usage, cost_usd=cost, latency_ms=latency,
                      stop_reason=getattr(message, "stop_reason", None), note=note, ttft_ms=ttft_ms)

    def _fail(self, prep: _Prepared, started: float, err: Exception) -> None:
        self.store.record(**prep.meta, model=prep.params["model"], requested_model=prep.requested_model,
                          est_input_tokens=prep.est_input_tokens, latency_ms=(time.perf_counter() - started) * 1000,
                          status="error", note=f"{type(err).__name__}: {err}"[:500])

    # ------------------------------------------------------------------
    def call(self, *, task: str, endpoint: str, messages: list, system: str | None = None,
             max_tokens: int = 1024, model: str | None = None, prompt_version: str = "v1",
             effort: str | None = None, request_id: str | None = None) -> Result:
        """Blocking call. Raises BudgetExceeded when a hard limit would be broken."""
        prep = self._prepare(task, endpoint, messages, system, max_tokens, model, prompt_version, effort, request_id)
        started = time.perf_counter()
        try:
            message = self.client.messages.create(**prep.params)
        except Exception as e:  # logged, then re-raised for the caller's typed handling
            self._fail(prep, started, e)
            raise
        return self._finish(prep, message, started)

    def stream(self, *, task: str, endpoint: str, messages: list, system: str | None = None,
               max_tokens: int = 4096, model: str | None = None, prompt_version: str = "v1",
               effort: str | None = None, request_id: str | None = None) -> "StreamHandle":
        """Streaming call: iterate the handle for text chunks; handle.result is set when the stream ends."""
        prep = self._prepare(task, endpoint, messages, system, max_tokens, model, prompt_version, effort, request_id)
        return StreamHandle(self, prep)


class StreamHandle:
    def __init__(self, gateway: Gateway, prep: _Prepared):
        self._gw, self._prep = gateway, prep
        self.result: Result | None = None
        self.model = prep.params["model"]

    def __iter__(self):
        started = time.perf_counter()
        first = None
        try:
            with self._gw.client.messages.stream(**self._prep.params) as s:
                for text in s.text_stream:
                    if first is None:
                        first = (time.perf_counter() - started) * 1000
                    yield text
                message = s.get_final_message()
        except Exception as e:
            self._gw._fail(self._prep, started, e)
            raise
        self.result = self._gw._finish(self._prep, message, started, first)
