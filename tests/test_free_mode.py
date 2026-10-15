"""Zero-cost guarantees: offline replay, Ollama client parsing, and the hard monthly cap."""
import json
import os
import tempfile
import time
import unittest
from types import SimpleNamespace as NS

import httpx

from shared.llm_gateway import BudgetExceeded, Gateway, UsageStore
from shared.llm_gateway.providers import OfflineClient, OllamaClient, RecordingClient, ReplayStore, request_key

MSG = [{"role": "user", "content": "How do I change my logo?"}]


def _env(**kv):
    old = {k: os.environ.get(k) for k in kv}
    os.environ.update({k: str(v) for k, v in kv.items()})
    return old


def _restore(old):
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


class OfflineModeTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.old = _env(LLM_MODE="offline", HARD_MONTHLY_CAP_USD="0", REPLAY_FILE=os.path.join(self.dir, "r.jsonl"))

    def tearDown(self):
        _restore(self.old)

    def test_default_offline_call_costs_zero(self):
        store = UsageStore(":memory:")
        r = Gateway(store=store).call(task="knowledge", endpoint="query", messages=MSG, system="sys")
        self.assertIn("[offline placeholder]", r.text)
        self.assertEqual(r.cost_usd, 0.0)
        self.assertEqual(r.model, "offline/fake")
        self.assertEqual(store.totals(time.time() - 60)["cost_usd"], 0.0)

    def test_offline_stream_works_under_zero_cap(self):
        store = UsageStore(":memory:")
        h = Gateway(store=store).stream(task="inbox", endpoint="draft", messages=MSG)
        self.assertIn("offline", "".join(h))
        self.assertEqual(h.result.cost_usd, 0.0)

    def test_replay_returns_recorded_answer(self):
        gw = Gateway(store=UsageStore(":memory:"), default_model="claude-haiku-5-5")
        params = {"model": "claude-haiku-5-5", "max_tokens": 1024, "messages": MSG}
        ReplayStore(os.environ["REPLAY_FILE"]).put(request_key(params), {"model": "claude-haiku-5-5", "text": "Go to Settings > Logo."})
        r = gw.call(task="knowledge", endpoint="query", messages=MSG)
        self.assertEqual((r.text, r.model, r.cost_usd), ("Go to Settings > Logo.", "offline/replay", 0.0))

    def test_recording_client_saves_for_replay(self):
        msg = NS(content=[NS(type="text", text="recorded")], usage=None, stop_reason="end_turn")
        inner = NS(messages=NS(create=lambda **p: msg))
        params = {"model": "claude-haiku-5-5", "max_tokens": 10, "messages": MSG}
        RecordingClient(inner, os.environ["REPLAY_FILE"]).messages.create(**params)
        replayed = OfflineClient(os.environ["REPLAY_FILE"]).create(**params)
        self.assertEqual(replayed.content[0].text, "recorded")


class HardCapTests(unittest.TestCase):
    def test_zero_cap_blocks_every_paid_call(self):
        old = _env(HARD_MONTHLY_CAP_USD="0")
        try:
            called = []
            client = NS(messages=NS(create=lambda **p: called.append(p)))
            store = UsageStore(":memory:")
            with self.assertRaises(BudgetExceeded) as e:
                Gateway(store=store, client=client).call(task="scope", endpoint="x", messages=MSG, max_tokens=10)
            self.assertIn("hard monthly cap", str(e.exception))
            self.assertEqual(called, [])  # never reached the API
            self.assertEqual(store.recent(1)[0]["status"], "blocked")
        finally:
            _restore(old)

    def test_cap_counts_spend_already_made(self):
        old = _env(HARD_MONTHLY_CAP_USD="1")
        try:
            store = UsageStore(":memory:")
            store.record(task="inbox", endpoint="d", model="claude-opus-5-5", cost_usd=0.9999, status="ok")
            client = NS(messages=NS(create=lambda **p: None))
            with self.assertRaises(BudgetExceeded):
                Gateway(store=store, client=client).call(task="inbox", endpoint="d", messages=MSG, max_tokens=1000)
        finally:
            _restore(old)


class OllamaClientTests(unittest.TestCase):
    def _client(self, handler):
        return OllamaClient(model="llama2", base_url="http://ollama.test", http=httpx.Client(transport=httpx.MockTransport(handler)))

    def test_create_parses_answer_and_counts(self):
        def handler(req):
            body = json.loads(req.content)
            self.assertEqual(body["messages"][0], {"role": "system", "content": "sys"})
            return httpx.Response(200, json={"message": {"content": "hi"}, "prompt_eval_count": 12, "eval_count": 3, "done": True})
        m = self._client(handler).create(model="claude-haiku-5-5", max_tokens=50, messages=MSG,
                                         system=[{"type": "text", "text": "sys"}])
        self.assertEqual((m.content[0].text, m.model, m.usage.input_tokens, m.usage.output_tokens),
                         ("hi", "ollama/llama2", 12, 3))

    def test_stream_through_gateway_is_free(self):
        lines = [{"message": {"content": "Hel"}}, {"message": {"content": "lo"}},
                 {"message": {"content": ""}, "done": True, "prompt_eval_count": 5, "eval_count": 2}]
        body = "\n".join(json.dumps(x) for x in lines)
        client = self._client(lambda req: httpx.Response(200, text=body))
        old = _env(HARD_MONTHLY_CAP_USD="0")
        try:
            gw = Gateway(store=UsageStore(":memory:"), mode="ollama")
            gw._client = client
            h = gw.stream(task="knowledge", endpoint="q", messages=MSG)
            self.assertEqual("".join(h), "Hello")
            self.assertEqual((h.result.model, h.result.cost_usd, h.result.output_tokens), ("ollama/llama2", 0.0, 2))
        finally:
            _restore(old)


if __name__ == "__main__":
    unittest.main()
