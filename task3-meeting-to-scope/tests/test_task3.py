"""Task 3 tests. Offline: the Knowledge Brain API is simulated with httpx.MockTransport.

    cd task3-meeting-to-scope && python -m unittest discover -s tests -t . -v
"""
import json
import sys
import unittest
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE.parent)]

from agents import analyst, estimator, feasibility, proposal, scoper  # noqa: E402
from core.kb_client import KBClient  # noqa: E402
from core.models import Feasibility, Requirement, SourceRef  # noqa: E402
from core.orchestrator import load_sample_meetings, run_meeting  # noqa: E402
from core.store import ApprovalError, ScopeStore  # noqa: E402
from core.transcript import parse, sections  # noqa: E402

M = {m["id"]: m for m in load_sample_meetings()}
STRONG = ("sorting", "autosuggest", "paypal", "invoice", "coupon", "stripe", "email template", "verify", "map")
WEAK = ("filter", "mobile", "relevance", "price")


def kb_handler(req: httpx.Request):
    q = json.loads(req.content)["question"].lower()
    score = 0.9 if any(w in q for w in STRONG) else 0.72 if any(w in q for w in WEAK) else 0.5
    status = "answered" if score >= 0.68 else "insufficient_evidence"
    src = [{"title": "Help article", "url": "https://support.brilliantdirectories.com/x", "section": "", "score": score}]
    return httpx.Response(200, json={"status": status, "retrieval_score": score, "answer": "From the help center: ...",
                                     "sources": src if status == "answered" else [], "answer_mode": "extractive"})


def kb(down=False):
    def handler(req):
        if down:
            raise httpx.ConnectError("refused")
        return kb_handler(req)
    return KBClient("http://kb.test", http=httpx.Client(transport=httpx.MockTransport(handler)), retries=0, backoff=0)


def ids(reqs, status=None):
    return {r.id for r in reqs if status is None or r.status == status}


class TranscriptTests(unittest.TestCase):
    def test_parse_roles_and_wrapped_lines(self):
        t = parse("# header\n[00:00:01] Ana (Client): We need maps.\nsecond line\n[00:00:05] Dev (Consultant): Ok.")
        self.assertEqual([(x.speaker, x.role) for x in t], [("Ana", "client"), ("Dev", "consultant")])
        self.assertEqual(t[0].text, "We need maps. second line")

    def test_long_transcript_staging_gives_same_result(self):
        """Processing in many small sections must give the same requirements as one big section."""
        text = M["meeting_a"]["text"]
        self.assertGreater(len(sections(parse(text), 60)), 5)
        small = analyst.analyse("A", text, max_tokens=60)
        big = analyst.analyse("A", text, max_tokens=100000)
        self.assertEqual([(r.id, r.status, r.priority) for r in small.requirements],
                         [(r.id, r.status, r.priority) for r in big.requirements])


class AnalystTests(unittest.TestCase):
    def test_meeting_a(self):
        f = analyst.analyse("meeting_a", M["meeting_a"]["text"])
        self.assertEqual(ids(f.requirements, "confirmed"), {"REQ-SEARCH-RELEVANCE", "REQ-FILTERS", "REQ-MOBILE-SEARCH",
                                                            "REQ-AUTOSUGGEST", "REQ-SORTING"})
        self.assertEqual(ids(f.requirements, "unclear"), {"REQ-PRICE-FILTER", "REQ-AI-SEARCH"})
        prio = {r.id: r.priority for r in f.requirements}
        self.assertEqual((prio["REQ-FILTERS"], prio["REQ-MOBILE-SEARCH"]), ("must", "must"))
        self.assertEqual(f.deadline, "March 1st, 2027")
        self.assertIn("$5,000", f.budget)
        self.assertEqual(ids(f.out_of_scope), {"REQ-HOMEPAGE-REDESIGN"})
        self.assertEqual(len(f.client_responsibilities), 2)
        self.assertTrue(f.consultant_commitments)

    def test_quotes_are_verbatim(self):
        f = analyst.analyse("meeting_b", M["meeting_b"]["text"])
        turns = parse(M["meeting_b"]["text"])
        for r in f.requirements:
            for s in r.sources:
                self.assertTrue(any(s.quote in t.text and s.ts == t.ts for t in turns), s.quote)

    def test_injection_flagged_and_ignored(self):
        f = analyst.analyse("meeting_b", M["meeting_b"]["text"])
        self.assertTrue(any("not obeyed" in x for x in f.flags))
        self.assertNotIn("REQ-AI-SEARCH", ids(f.requirements))
        self.assertFalse(any("free" in r.description.lower() for r in f.requirements))

    def test_empty_and_unlabelled_transcripts(self):
        self.assertEqual(analyst.analyse("x", "").flags, ["no speaker turns found in the transcript"])
        f = analyst.analyse("x", "[00:00:01] Ana: We need a map view.")
        self.assertTrue(any("no client speaker" in x for x in f.flags))

    def test_tampered_quote_rejected(self):
        f = analyst.analyse("meeting_a", M["meeting_a"]["text"])
        f.requirements[0].sources[0].quote = "We want everything for free."
        with self.assertRaises(ValueError):
            analyst.validate(f, parse(M["meeting_a"]["text"]))


class FeasibilityEstimateTests(unittest.TestCase):
    def _req(self, topic, status="confirmed"):
        return Requirement(id=f"REQ-{topic}", topic=topic, title=topic, description="d", status=status, priority="should",
                           explicit=True, sources=[SourceRef(meeting_id="m", ts="0", speaker="a", quote="d")])

    def test_classes(self):
        out = {r.topic: r.feasibility.cls for r in feasibility.run(
            [self._req("SORTING"), self._req("FILTERS"), self._req("SHIPPING"), self._req("AI_SEARCH", "unclear"),
             self._req("CONTENT_WRITING")], kb())}
        self.assertEqual(out, {"SORTING": "existing_config", "FILTERS": "existing_limited", "SHIPPING": "custom",
                               "AI_SEARCH": "needs_clarification", "CONTENT_WRITING": "custom"})

    def test_missing_result_is_not_proof(self):
        r = feasibility.run([self._req("SHIPPING")], kb())[0]
        self.assertIn("Confirm with a product expert", r.feasibility.note)

    def test_kb_down(self):
        r = feasibility.run([self._req("SORTING")], kb(down=True))[0]
        self.assertEqual(r.feasibility.cls, "needs_clarification")

    def test_estimate_skips_unclear_and_adds_pm(self):
        reqs = feasibility.run([self._req("SORTING"), self._req("AI_SEARCH", "unclear")], kb())
        est, out = estimator.run(reqs, 40, "USD")
        self.assertEqual(est.not_estimated, ["REQ-AI_SEARCH"])
        self.assertTrue(any(t.req_id == "PROJECT" for t in est.tasks))
        self.assertAlmostEqual(est.expected, round((est.total_low + 4 * est.total_likely + est.total_high) / 6, 1), 1)
        self.assertLessEqual(est.total_low, est.total_likely)
        self.assertLessEqual(est.total_likely, est.total_high)

    def test_review_blocks_inconsistent_proposal(self):
        f = analyst.analyse("meeting_a", M["meeting_a"]["text"])
        reqs = feasibility.run(f.requirements, kb())
        est, reqs = estimator.run(reqs, 40, "USD")
        scope = scoper.build("P", "C", f, reqs, M["meeting_a"]["text"])
        scope.deliverables.append("REQ-AI-SEARCH AI-assisted search — maybe")  # unclear item sneaked in
        md, issues = proposal.build("P", "C", scope, est, 1)
        self.assertTrue(any(i["severity"] == "blocker" and "REQ-AI-SEARCH" in i["issue"] for i in issues))


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.s = ScopeStore(":memory:")
        a = M["meeting_a"]
        self.v1 = run_meeting(self.s, kb(), a["project"], a["client"], "meeting_a", a["date"], a["text"])
        self.pid = self.s.project(a["project"])

    def followup(self):
        c = M["meeting_c"]
        return run_meeting(self.s, kb(), c["project"], c["client"], "meeting_c", c["date"], c["text"], "meeting_a")

    def test_followup_needs_approved_baseline(self):
        with self.assertRaises(ApprovalError):
            self.followup()

    def test_send_rules(self):
        with self.assertRaises(ApprovalError):
            self.s.send_to_client(self.pid, self.v1, "x")      # not approved
        with self.assertRaises(ApprovalError):
            self.s.approve(self.pid, self.v1, "  ")             # no reviewer name
        self.s.approve(self.pid, self.v1, "Divakar")
        with self.s.conn() as c:                                # someone edits the approved content directly
            v = self.s.version(self.pid, self.v1)
            v["data"]["proposal"] += "\nEverything free!"
            c.execute("UPDATE versions SET data = ? WHERE project_id = ? AND version = ?",
                      (json.dumps(v["data"]), self.pid, self.v1))
        with self.assertRaises(ApprovalError):
            self.s.send_to_client(self.pid, self.v1, "x")      # changed after approval
        self.assertIn("send_refused", [a["event"] for a in self.s.audit(self.pid)])

    def test_change_report_against_approved_baseline(self):
        self.s.approve(self.pid, self.v1, "Divakar")
        v2 = self.followup()
        cr = self.s.version(self.pid, v2)["data"]["change_report"]
        kinds = {c["req_id"]: c["kind"] for c in cr["changes"]}
        self.assertEqual(kinds["REQ-AI-SEARCH"], "removed")
        self.assertEqual(kinds["REQ-MOBILE-SEARCH"], "modified")
        self.assertEqual(kinds["REQ-PRICE-FILTER"], "modified")
        self.assertEqual({k for k, v in kinds.items() if v == "added"},
                         {"REQ-MAP-VIEW", "REQ-VERIFIED-FILTER", "REQ-CONTENT-WRITING"})
        self.assertTrue(all(c["out_of_scope"] for c in cr["changes"] if c["kind"] == "added"))
        self.assertEqual((cr["deadline_before"], cr["deadline_after"]), ("March 1st, 2027", "April 15th, 2027"))
        self.assertEqual(cr["baseline_version"], 1)
        self.assertGreater(cr["hours_change_requests"], 0)

    def test_baseline_is_approved_version_not_latest_draft(self):
        self.s.approve(self.pid, self.v1, "Divakar")
        a = M["meeting_a"]
        # an unapproved draft (v2) exists; the follow-up must still compare with v1
        run_meeting(self.s, kb(), a["project"], a["client"], "meeting_a_redo", a["date"], a["text"])
        v3 = self.followup()
        self.assertEqual(self.s.version(self.pid, v3)["data"]["change_report"]["baseline_version"], 1)


if __name__ == "__main__":
    unittest.main()
