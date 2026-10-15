"""LLM clients the gateway can use. All expose the same small surface as the Anthropic SDK:
client.messages.create(**params) -> message, client.messages.stream(**params) -> context manager.

LLM_MODE picks one (default "offline", so development and tests cost $0):
- offline : replay recorded Claude answers from data/replay.jsonl; unknown requests get a
            deterministic placeholder answer. No network, no cost.
- ollama  : a local model through Ollama (http://localhost:11434). No cost.
- claude  : the real Anthropic API (paid). With REPLAY_RECORD=1 every answer is also saved to
            the replay file, so the same request is free next time in offline mode.
"""
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace as NS

MODES = ("offline", "ollama", "claude")
FREE_PREFIXES = ("offline/", "ollama/")


def current_mode() -> str:
    mode = os.environ.get("LLM_MODE", "offline").strip().lower()
    if mode not in MODES:
        raise ValueError(f"LLM_MODE must be one of {MODES}, got {mode!r}")
    return mode


def request_key(params: dict) -> str:
    """Stable hash of everything that shapes the answer (model, system, messages, max_tokens, effort)."""
    keep = {k: params.get(k) for k in ("model", "system", "messages", "max_tokens", "output_config")}
    return hashlib.sha256(json.dumps(keep, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _message(text: str, model: str, input_tokens: int = 0, output_tokens: int = 0,
             cache_read: int = 0, cache_write: int = 0, stop_reason: str = "end_turn"):
    usage = NS(input_tokens=input_tokens, output_tokens=output_tokens,
               cache_read_input_tokens=cache_read, cache_creation_input_tokens=cache_write)
    return NS(content=[NS(type="text", text=text)], usage=usage, stop_reason=stop_reason, model=model)


class _ListStream:
    """Stream over a ready message, chunked by words, matching the SDK's stream helper."""

    def __init__(self, message, chunks=None):
        self._message = message
        words = message.content[0].text.split(" ")
        self.text_stream = iter(chunks if chunks is not None else [w + " " for w in words[:-1]] + words[-1:])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._message


# --- offline ------------------------------------------------------------------
class ReplayStore:
    """Hash map from request key to a recorded answer, persisted as JSONL (append-only)."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._index: dict[str, dict] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rec = json.loads(line)
                    self._index[rec["key"]] = rec

    def get(self, key: str) -> dict | None:
        return self._index.get(key)

    def put(self, key: str, rec: dict) -> None:
        rec = {"key": key, **rec}
        self._index[key] = rec
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def __len__(self):
        return len(self._index)


class OfflineClient:
    """$0 client: replays recorded answers, otherwise returns a clearly marked placeholder."""

    def __init__(self, replay_path: str | Path | None = None):
        self.replay = ReplayStore(replay_path or os.environ.get("REPLAY_FILE", "data/replay.jsonl"))
        self.messages = self

    def _answer(self, params: dict):
        from . import tokens  # local import: avoids a cycle at module load
        rec = self.replay.get(request_key(params))
        prompt_tokens = tokens.estimate(tokens.messages_text(_system_text(params.get("system")), params["messages"]))
        if rec:
            return _message(rec["text"], "offline/replay", prompt_tokens, tokens.estimate(rec["text"]))
        last = _last_user_text(params["messages"])
        text = (f"[offline placeholder] No recorded answer for this request. "
                f"Question was: {last[:200]}")
        return _message(text, "offline/fake", prompt_tokens, tokens.estimate(text))

    def create(self, **params):
        return self._answer(params)

    def stream(self, **params):
        return _ListStream(self._answer(params))


# --- ollama ---------------------------------------------------------------------
class OllamaClient:
    """Local model through Ollama's /api/chat. Token counts come from Ollama's own counters."""

    def __init__(self, model: str | None = None, base_url: str | None = None, http=None):
        import httpx
        self.model = model or os.environ.get("OLLAMA_MODEL", "llama2")
        self.base_url = (base_url or os.environ.get("OLLAMA_URL", "http://localhost:11434")).rstrip("/")
        self.http = http or httpx.Client(timeout=float(os.environ.get("OLLAMA_TIMEOUT", "180")))
        self.messages = self

    def _body(self, params: dict, stream: bool) -> dict:
        msgs = []
        if params.get("system"):
            msgs.append({"role": "system", "content": _system_text(params["system"])})
        for m in params["messages"]:
            msgs.append({"role": m["role"], "content": _content_text(m["content"])})
        return {"model": self.model, "messages": msgs, "stream": stream,
                "options": {"num_predict": params.get("max_tokens", 1024)}}

    def _done(self, text: str, data: dict):
        return _message(text, f"ollama/{self.model}", data.get("prompt_eval_count", 0), data.get("eval_count", 0),
                        stop_reason="max_tokens" if data.get("done_reason") == "length" else "end_turn")

    def create(self, **params):
        r = self.http.post(f"{self.base_url}/api/chat", json=self._body(params, False))
        r.raise_for_status()
        data = r.json()
        return self._done(data["message"]["content"], data)

    def stream(self, **params):
        return _OllamaStream(self, params)


class _OllamaStream:
    def __init__(self, client: OllamaClient, params: dict):
        self._client, self._params = client, params
        self._final = None
        self._ctx = None

    def __enter__(self):
        self._ctx = self._client.http.stream("POST", f"{self._client.base_url}/api/chat",
                                             json=self._client._body(self._params, True))
        self._resp = self._ctx.__enter__()
        self._resp.raise_for_status()
        self.text_stream = self._chunks()
        return self

    def _chunks(self):
        parts = []
        for line in self._resp.iter_lines():
            if not line:
                continue
            data = json.loads(line)
            piece = data.get("message", {}).get("content", "")
            if piece:
                parts.append(piece)
                yield piece
            if data.get("done"):
                self._final = self._client._done("".join(parts), data)

    def __exit__(self, *exc):
        return self._ctx.__exit__(*exc)

    def get_final_message(self):
        for _ in self.text_stream:  # drain if the caller stopped early
            pass
        return self._final


# --- claude with recording -----------------------------------------------------------
class RecordingClient:
    """Wraps the Anthropic client and saves each answer to the replay file."""

    def __init__(self, inner, replay_path: str | Path | None = None):
        self.inner = inner
        self.replay = ReplayStore(replay_path or os.environ.get("REPLAY_FILE", "data/replay.jsonl"))
        self.messages = self

    def _save(self, params, message):
        text = "".join(b.text for b in message.content if getattr(b, "type", "") == "text")
        self.replay.put(request_key(params), {"model": params["model"], "text": text})

    def create(self, **params):
        message = self.inner.messages.create(**params)
        self._save(params, message)
        return message

    def stream(self, **params):
        outer = self

        class _Rec:
            def __enter__(self_):
                self_.s = outer.inner.messages.stream(**params).__enter__()
                self_.text_stream = self_.s.text_stream
                return self_

            def __exit__(self_, *exc):
                return self_.s.__exit__(*exc)

            def get_final_message(self_):
                message = self_.s.get_final_message()
                outer._save(params, message)
                return message

        return _Rec()


def make_client(mode: str | None = None):
    mode = mode or current_mode()
    if mode == "offline":
        return OfflineClient()
    if mode == "ollama":
        return OllamaClient()
    import anthropic  # paid API; only imported in claude mode
    client = anthropic.Anthropic()
    return RecordingClient(client) if os.environ.get("REPLAY_RECORD") == "1" else client


# --- helpers ---------------------------------------------------------------------------
def _system_text(system) -> str:
    if isinstance(system, list):
        return "\n".join(b.get("text", "") for b in system if isinstance(b, dict))
    return system or ""


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")


def _last_user_text(messages) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            return _content_text(m["content"])
    return ""
