"""Task 2 tests. Offline: the Knowledge Brain API is simulated with httpx.MockTransport.

    cd task2-inbox-agent && python -m unittest discover -s tests -t . -v
"""
import json
import os
import sys
import unittest
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE.parent)]
os.environ.setdefault("LLM_MODE", "offline")

import settings  # noqa: E402
from inbox import autonomy, learning  # noqa: E402
from inbox.agent import InboxAgent, load_emails  # noqa: E402
from inbox.classifier import Classifier  # noqa: E402
from inbox.kb_client import KBClient, KBUnavailable  # noqa: E402
from inbox.store import InboxStore  # noqa: E402
from shared import guardrails  # noqa: E402

EMAILS = {e["id"]: e for e in load_emails()}
DEV = [e for e in EMAILS.values() if e["split"] == "dev"]
ANSWER = {"status": "answered", "needs_human": False, "retrieval_score": 0.86, "answer_mode": "extractive",
          "answer": "From the help center:\n- Go to Settings » Security » IP Blocking. [1]\n- Enter the IP and click Save. [1]",
          "sources": [{"title": "Block An IP Address", "url": "https://support.brilliantdirectories.com/support/solutions/articles/12000008504",
                       "section": "", "score": 0.86, "kind": "article"}], "images": [], "model": None,
          "request_id": "r", "latency_ms": 5, "flags": [], "escalation_id": None}
NO_ANSWER = {**ANSWER, "status": "insufficient_evidence", "needs_human": True, "sources": [], "answer_mode": "none",
             "answer": "I could not find this.", "escalation_id": 7}


class FakeKB:
    def __init__(self, down=False, fail_first=0):
        self.down, self.fail_first, self.calls = down, fail_first, []

    def handler(self, req: httpx.Request):
        body = json.loads(req.content) if req.content else {}
        self.calls.append((req.url.path, body))
        if self.down:
            raise httpx.ConnectError("refused")
        if self.fail_first:
            self.fail_first -= 1
            return httpx.Response(503)
        if req.url.path == "/escalations":
            return httpx.Response(201, json={"id": 99})
        q = body.get("question", "").lower()
        return httpx.Response(200, json=ANSWER if any(w in q for w in ("ip", "domain", "payment", "outlook")) else NO_ANSWER)

    def client(self):
        return KBClient("http://kb.test", http=httpx.Client(transport=httpx.MockTransport(self.handler)), backoff=0)


def agent(kb=None, store=None):
    kb = kb or FakeKB()
    return InboxAgent(store or InboxStore(":memory:"), kb.client(), Classifier.train(DEV)), kb


class KBClientTests(unittest.TestCase):
    def test_retry_then_success(self):
        kb = FakeKB(fail_first=1)
        self.assertEqual(kb.client().query("block ip").status, "answered")
        self.assertEqual(len(kb.calls), 2)

    def test_down_raises_unavailable(self):
        with self.assertRaises(KBUnavailable):
            FakeKB(down=True).client().query("block ip")

    def test_inbox_source_tag_sent(self):
        kb = FakeKB()
        kb.client().query("block ip", escalate=False)
        self.assertEqual((kb.calls[0][1]["source"], kb.calls[0][1]["escalate"]), ("inbox", False))


class DraftTests(unittest.TestCase):
    def test_how_to_grounded_draft(self):
        a, _ = agent()
        d = a.process(EMAILS["E001"])
        self.assertEqual((d.category.value, d.kb_status), ("how_to", "answered"))
        self.assertTrue(d.reply.startswith("Hi Maria,"))
        self.assertIn("IP Blocking", d.reply)
        self.assertNotIn("[1]", d.reply)
        self.assertIn("support.brilliantdirectories.com", d.reply)
        self.assertTrue(guardrails.check_commitments(d.reply).ok)

    def test_kb_down_means_no_fabricated_reply(self):
        a, _ = agent(FakeKB(down=True))
        d = a.process(EMAILS["E001"])
        self.assertEqual((d.reply, d.kb_status), ("", "unavailable"))
        self.assertEqual(a.store.email("E001")["status"], "kb_unavailable")

    def test_unknown_question_is_escalated_not_guessed(self):
        a, kb = agent()
        d = a.process(EMAILS["E013"])
        self.assertEqual((d.kb_status, d.escalation_id), ("insufficient_evidence", 7))
        self.assertIn("product specialist", d.reply)
        self.assertTrue(kb.calls[0][1]["escalate"])

    def test_refund_never_promised_and_needs_human(self):
        a, _ = agent()
        for eid in ("E031", "E032", "E043"):
            d = a.process(EMAILS[eid])
            self.assertEqual(d.category.value, "refund_cancel", eid)
            self.assertTrue(guardrails.check_commitments(d.reply).ok, eid)
            self.assertIn("refund_cancel always needs a human", d.approval_reasons)

    def test_injection_flagged(self):
        a, _ = agent()
        d = a.process(EMAILS["E043"])
        self.assertTrue(d.flags)
        self.assertNotIn("12 free months", d.reply)

    def test_pii_redacted_in_summary(self):
        a, _ = agent()
        d = a.process(EMAILS["E022"])
        self.assertNotIn("555-0188", d.summary + json.dumps(d.model_dump(mode="json")["approval_reasons"]))

    def test_follow_up_references_thread(self):
        a, _ = agent()
        a.process(EMAILS["E002"])
        d = a.process(EMAILS["E050"])
        self.assertEqual(d.category.value, "follow_up")
        self.assertIn('"Which payment gateways?"', d.reply)

    def test_quote_mentions_existing_feature_without_price(self):
        a, kb = agent()
        d = a.process({**EMAILS["E044"], "body": "How much would it cost to add payment options for members?"})
        self.assertEqual(d.category.value, "quote")
        self.assertIn("existing feature", d.reply)
        self.assertFalse(kb.calls[0][1]["escalate"])
        self.assertTrue(guardrails.check_commitments(d.reply).ok)


class ReviewAutonomyTests(unittest.TestCase):
    def setUp(self):
        settings.AUTONOMY_STREAK = 3  # small streak keeps the test short; the rule is the same as 20

    def tearDown(self):
        settings.AUTONOMY_STREAK = 20

    def _how_to(self, a, i):
        e = {**EMAILS["E001"], "id": f"X{i}"}
        a.process(e)
        return e["id"]

    def test_send_is_idempotent_and_edit_counts_as_change(self):
        a, _ = agent()
        eid = self._how_to(a, 1)
        self.assertTrue(a.review(eid, "approve")["sent"])
        self.assertFalse(a.store.send(eid, "again", "human:x"))  # second send refused
        eid2 = self._how_to(a, 2)
        out = a.review(eid2, "edit", "Totally different reply", reason="too long")
        self.assertEqual(out["autonomy"]["streak"], 0)
        eid3 = self._how_to(a, 3)
        draft = a.store.latest_draft(eid3)[1]["reply"]
        a.review(eid3, "edit", draft + "  \n")  # whitespace only = unchanged
        self.assertEqual(a.store.reviews()[0]["action"], "approve")

    def test_streak_eligibility_auto_send_and_kill_switch(self):
        a, _ = agent()
        for i in range(3):
            a.review(self._how_to(a, i), "approve")
        st = a.store.autonomy("how_to")
        self.assertEqual((st["streak"], st["eligible"]), (3, 1))
        autonomy.enable(a.store, "how_to", "admin")
        eid = self._how_to(a, 10)
        self.assertEqual(a.store.email(eid)["status"], "sent")            # sent automatically
        self.assertEqual(a.store.outbox()[0]["sent_by"], "auto")
        eid = self._how_to(a, 11)
        a.store.set_status(eid, "drafted")
        a.review(eid, "edit", "Human rewrote this")                       # quality drop
        st = a.store.autonomy("how_to")
        self.assertEqual((st["enabled"], st["eligible"], st["streak"]), (0, 0, 0))
        self.assertIn("disabled", [e["event"] for e in a.store.events()])

    def test_never_autonomous_categories(self):
        a, _ = agent()
        for i in range(5):
            e = {**EMAILS["E031"], "id": f"R{i}"}
            a.process(e)
            a.review(e["id"], "approve")
        self.assertEqual(a.store.autonomy("refund_cancel")["eligible"], 0)
        with self.assertRaises(ValueError):
            autonomy.enable(a.store, "refund_cancel", "admin")

    def test_urgent_email_not_auto_sent_even_when_enabled(self):
        a, _ = agent()
        a.store.set_autonomy("troubleshooting", eligible=1, enabled=1)
        a.process(EMAILS["E015"])  # urgent firewall email
        self.assertEqual(a.store.email("E015")["status"], "drafted")


class LearningTests(unittest.TestCase):
    def test_learn_from_repeated_deletions_and_roll_back(self):
        a, _ = agent()
        cut = "If anything doesn't match what you see on your screen, reply with a screenshot and we'll take a look."
        for i in range(4):
            e = {**EMAILS["E001"], "id": f"L{i}"}
            draft = a.process(e).reply
            a.review(e["id"], "edit", draft.replace(cut, ""), reason="too long")
        e = {**EMAILS["E001"], "id": "L9"}
        a.process(e)
        a.review("L9", "approve")
        proposal = learning.propose(a.store)
        self.assertIsNotNone(proposal)
        self.assertGreater(proposal["evaluation"]["similarity_after"], proposal["evaluation"]["similarity_before"])
        self.assertEqual(proposal["evaluation"]["regressions_on_unchanged"], 1)  # L9 was approved WITH the sentence
        self.assertFalse(learning.adopt(a.store, proposal))                       # so it is not adopted
        a.store.activate_rules(proposal["version"])                               # admin forces it to test rollback
        self.assertNotIn(cut, a.process({**EMAILS["E001"], "id": "L10"}).reply)
        a.store.activate_rules("rules-v0")
        self.assertIn(cut, a.process({**EMAILS["E001"], "id": "L11"}).reply)


if __name__ == "__main__":
    unittest.main()
