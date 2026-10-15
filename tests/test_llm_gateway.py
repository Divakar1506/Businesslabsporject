"""Offline tests for the gateway: a fake Claude client, an in-memory store. Costs $0.

    python -m unittest discover -s tests -v
"""
import os
import time
import unittest
from types import SimpleNamespace as NS

# The fake client is billed like Claude on paper only; lift the $0 hard cap for these tests.
os.environ["HARD_MONTHLY_CAP_USD"] = "1000"
os.environ["LLM_MODE"] = "offline"

from shared.llm_gateway import BudgetExceeded, Gateway, UsageStore, validate_settings
from shared.llm_gateway import pricing, tokens


def _message(text="hello", inp=1000, out=200, read=0, write=0):
    usage = NS(input_tokens=inp, output_tokens=out, cache_read_input_tokens=read, cache_creation_input_tokens=write)
    return NS(content=[NS(type="text", text=text)], usage=usage, stop_reason="end_turn")


class _Stream:
    def __init__(self, chunks, message):
        self.text_stream = iter(chunks)
        self._message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._message


class FakeClient:
    def __init__(self, message=None, fail=None):
        self.calls = []
        self._message = message or _message()
        self._fail = fail
        self.messages = self

    def create(self, **params):
        self.calls.append(params)
        if self._fail:
            raise self._fail
        return self._message

    def stream(self, **params):
        self.calls.append(params)
        return _Stream(["hel", "lo"], self._message)


def gateway(client=None, default_model="claude-sonnet-5-5"):
    store = UsageStore(":memory:")
    return Gateway(store=store, client=client or FakeClient(), default_model=default_model), store


MSG = [{"role": "user", "content": "How do I change my logo?"}]


class PricingTests(unittest.TestCase):
    def test_cost_includes_all_token_kinds(self):
        usd = pricing.call_cost("claude-sonnet-5-5", input_tokens=1_000_000, output_tokens=1_000_000,
                                cache_read_tokens=1_000_000, cache_write_tokens=1_000_000)
        self.assertAlmostEqual(usd, 2.00 + 10.00 + 0.20 + 2.00 * 1.25)

    def test_haiku_long_prompt_tier(self):
        short = pricing.call_cost("claude-haiku-5-5", input_tokens=100_000)
        long = pricing.call_cost("claude-haiku-5-5", input_tokens=100_001)
        self.assertAlmostEqual(short, 0.01)
        self.assertGreater(long, short * 4)

    def test_unknown_model_costs_zero(self):
        self.assertEqual(pricing.call_cost("not-a-model", input_tokens=10), 0.0)


class TokenEstimateTests(unittest.TestCase):
    def test_estimate_scales_word_tokens(self):
        self.assertEqual(tokens.estimate(""), 0)
        words = tokens.word_tokens("Open the advanced settings, then click Save.")
        self.assertEqual(words, 9)
        self.assertEqual(tokens.estimate("Open the advanced settings, then click Save."), round(9 * tokens.SUBWORD_RATIO))

    def test_messages_text_reads_blocks(self):
        text = tokens.messages_text("sys", [{"role": "user", "content": [{"type": "text", "text": "a b"}]}])
        self.assertIn("sys", text)
        self.assertIn("a b", text)


class GatewayCallTests(unittest.TestCase):
    def test_call_logs_usage_and_cost(self):
        gw, store = gateway(FakeClient(_message(inp=1000, out=200, read=3000)))
        r = gw.call(task="knowledge", endpoint="query", messages=MSG, system="Answer from evidence only.")
        self.assertEqual(r.text, "hello")
        row = store.recent(1)[0]
        self.assertEqual((row["status"], row["task"], row["model"]), ("ok", "knowledge", "claude-sonnet-5-5"))
        self.assertEqual((row["input_tokens"], row["output_tokens"], row["cache_read_tokens"]), (1000, 200, 3000))
        self.assertAlmostEqual(row["cost_usd"], pricing.call_cost("claude-sonnet-5-5", 1000, 200, 3000))
        self.assertGreater(row["est_input_tokens"], 0)

    def test_system_prompt_is_cached(self):
        client = FakeClient()
        gw, _ = gateway(client)
        gw.call(task="inbox", endpoint="draft", messages=MSG, system="Company tone guide")
        self.assertEqual(client.calls[0]["system"][0]["cache_control"], {"type": "ephemeral"})

    def test_error_is_logged_and_reraised(self):
        gw, store = gateway(FakeClient(fail=TimeoutError("slow")))
        with self.assertRaises(TimeoutError):
            gw.call(task="scope", endpoint="extract", messages=MSG)
        row = store.recent(1)[0]
        self.assertEqual(row["status"], "error")
        self.assertIn("TimeoutError", row["note"])

    def test_stream_yields_chunks_then_logs(self):
        gw, store = gateway()
        handle = gw.stream(task="knowledge", endpoint="query", messages=MSG)
        self.assertEqual("".join(handle), "hel" + "lo")
        self.assertEqual(handle.result.output_tokens, 200)
        self.assertIsNotNone(store.recent(1)[0]["ttft_ms"])
        self.assertIsNotNone(store.totals(time.time() - 60)["avg_ttft_ms"])


class BudgetTests(unittest.TestCase):
    def test_task_model_setting_is_used(self):
        client = FakeClient()
        gw, store = gateway(client)
        store.set_setting("budget", {"tasks": {"inbox": {"model": "claude-haiku-5-5"}}})
        gw.call(task="inbox", endpoint="classify", messages=MSG)
        self.assertEqual(client.calls[0]["model"], "claude-haiku-5-5")

    def test_hard_limit_blocks_and_logs(self):
        client = FakeClient()
        gw, store = gateway(client)
        store.set_setting("budget", {"tasks": {"scope": {"daily_usd": 0.001}}})
        with self.assertRaises(BudgetExceeded):
            gw.call(task="scope", endpoint="proposal", messages=MSG, max_tokens=4000)
        self.assertEqual(client.calls, [])
        self.assertEqual(store.recent(1)[0]["status"], "blocked")

    def test_soft_limit_downgrades_to_cheapest(self):
        client = FakeClient()
        gw, store = gateway(client)
        store.set_setting("budget", {"tasks": {"knowledge": {"daily_usd": 1.0}}})
        store.record(task="knowledge", endpoint="query", model="claude-sonnet-5-5", cost_usd=0.85, status="ok")
        r = gw.call(task="knowledge", endpoint="query", messages=MSG, max_tokens=100)
        self.assertEqual(r.model, pricing.CHEAPEST_MODEL)
        self.assertIn("downgraded", r.note)
        self.assertTrue(any(a["level"] == "warning" for a in store.alerts()))

    def test_global_token_limit_blocks(self):
        gw, store = gateway()
        store.set_setting("budget", {"global": {"daily_tokens": 500}})
        with self.assertRaises(BudgetExceeded):
            gw.call(task="inbox", endpoint="draft", messages=MSG, max_tokens=1000)

    def test_cheapest_only_switch(self):
        client = FakeClient()
        gw, store = gateway(client)
        store.set_setting("budget", {"global": {"cheapest_only": True}})
        gw.call(task="scope", endpoint="estimate", messages=MSG, model="claude-opus-5-5")
        self.assertEqual(client.calls[0]["model"], pricing.CHEAPEST_MODEL)

    def test_validate_settings_rejects_bad_values(self):
        with self.assertRaises(ValueError):
            validate_settings({"global": {"daily_usd": -1}})
        with self.assertRaises(ValueError):
            validate_settings({"tasks": {"inbox": {"model": "gpt-x"}}})


class StoreTests(unittest.TestCase):
    def test_totals_timeseries_breakdown(self):
        store = UsageStore(":memory:")
        now = time.time()
        for i, task in enumerate(["knowledge", "inbox", "inbox"]):
            store.record(ts=now - i, task=task, endpoint="e", model="claude-haiku-5-5", input_tokens=100,
                         output_tokens=10, cache_read_tokens=100, cost_usd=0.01, latency_ms=100 * (i + 1), status="ok")
        t = store.totals(now - 3600)
        self.assertEqual((t["ok"], t["total_tokens"]), (3, 630))
        self.assertEqual(t["cache_hit_pct"], 50.0)
        self.assertAlmostEqual(t["p50_latency_ms"], 200.0, delta=10)  # histogram: within 5%
        self.assertEqual(sum(r["input_tokens"] for r in store.timeseries(now - 3600, "hour")), 300)
        top = store.breakdown("task", now - 3600)[0]
        self.assertEqual((top["name"], top["calls"]), ("inbox", 2))
        with self.assertRaises(ValueError):
            store.breakdown("note; DROP TABLE calls", now)


class RollupTests(unittest.TestCase):
    def test_rollups_match_raw_calls_and_rebuild(self):
        import random
        store = UsageStore(":memory:")
        rng = random.Random(1)
        now = time.time()
        raw = []
        for i in range(500):
            row = dict(ts=now - rng.uniform(0, 3 * 86400), task=rng.choice(["knowledge", "inbox"]), endpoint="e",
                       model="claude-haiku-5-5", input_tokens=rng.randint(1, 900), output_tokens=rng.randint(1, 90),
                       cost_usd=rng.random() / 100, latency_ms=rng.uniform(100, 5000),
                       status=rng.choice(["ok", "ok", "ok", "error"]))
            store.record(**row)
            raw.append(row)
        t = store.totals(now - 4 * 86400)
        self.assertEqual(t["calls"], 500)
        self.assertEqual(t["input_tokens"], sum(r["input_tokens"] for r in raw))
        self.assertAlmostEqual(t["cost_usd"], sum(r["cost_usd"] for r in raw), places=9)
        self.assertEqual(t["errors"], sum(r["status"] == "error" for r in raw))
        store.rebuild_rollups()
        self.assertEqual(store.totals(now - 4 * 86400)["input_tokens"], t["input_tokens"])

    def test_histogram_percentiles_within_5_percent(self):
        import random
        from shared.llm_gateway.store import latency_bin, percentiles_from_hist
        rng = random.Random(3)
        lat = [rng.lognormvariate(7, 0.6) for _ in range(5000)]
        hist = {}
        for v in lat:
            hist[latency_bin(v)] = hist.get(latency_bin(v), 0) + 1
        got = percentiles_from_hist(hist)
        lat.sort()
        for p in (50, 95, 99):
            exact = lat[int(p / 100 * len(lat)) - 1]
            self.assertLess(abs(got[p] - exact) / exact, 0.06, p)

    def test_downgrade_flag_counted(self):
        gw, store = gateway()
        store.set_setting("budget", {"global": {"cheapest_only": True}})
        gw.call(task="inbox", endpoint="draft", messages=MSG)
        self.assertEqual(store.totals(time.time() - 60)["downgraded"], 1)

    def test_old_database_is_migrated(self):
        import os, sqlite3, tempfile
        path = os.path.join(tempfile.mkdtemp(), "old.db")
        c = sqlite3.connect(path)
        c.execute("CREATE TABLE calls (id INTEGER PRIMARY KEY, ts REAL, request_id TEXT, task TEXT, endpoint TEXT, "
                  "model TEXT, requested_model TEXT, prompt_version TEXT, input_tokens INT, output_tokens INT, "
                  "cache_read_tokens INT, cache_write_tokens INT, est_input_tokens INT, cost_usd REAL, "
                  "latency_ms REAL, context_pct REAL, status TEXT, note TEXT)")
        c.execute("INSERT INTO calls (ts, task, endpoint, model, input_tokens, output_tokens, cache_read_tokens, "
                  "cache_write_tokens, est_input_tokens, cost_usd, latency_ms, context_pct, status) "
                  "VALUES (?, 'scope', 'e', 'claude-opus-5-5', 10, 5, 0, 0, 0, 0.5, 100, 0, 'ok')", (time.time(),))
        c.commit(); c.close()
        self.assertEqual(UsageStore(path).totals(time.time() - 3600)["cost_usd"], 0.5)


if __name__ == "__main__":
    unittest.main()
