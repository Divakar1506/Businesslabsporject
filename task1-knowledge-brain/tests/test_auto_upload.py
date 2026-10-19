"""File-only drafts and the approval gate, exercised through the service, API and Streamlit screen."""
import io
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE.parent)]
os.environ.setdefault("LLM_MODE", "offline")

import settings
from app.manual import ManualKnowledge
from ingestion.store import KBStore
from tests.test_manual import make_docx, make_pdf, make_png
from tests.test_task1 import fake_embed

DOCUMENT = b"# Holiday support hours\n\nSupport replies within 24 hours.\n\n## Weekends\nUse the support portal."


class AutoUploadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = KBStore(":memory:")
        self.addCleanup(self.store._mem.close)
        self.mk = ManualKnowledge(self.store, embed=fake_embed, upload_dir=Path(self.tmp.name))

    def test_file_only_populates_title_content_and_sections_but_waits_for_approval(self):
        result = self.mk.upload("holiday.md", DOCUMENT)
        self.assertEqual(result["title"], "Holiday support hours")
        art = self.mk.get(result["article_id"])
        self.assertEqual([s["heading"] for s in art["sections"]], ["Holiday support hours", "Weekends"])
        self.assertIn("24 hours", art["editable_text"])
        self.assertEqual(art["review_status"], "pending")
        self.assertEqual(self.store.active_chunks(), [])
        self.assertEqual(self.store.status()["pending_review"], 1)
        self.assertEqual(self.mk.file_path(result["article_id"], "holiday.md").read_bytes(), DOCUMENT)

    def test_word_pdf_text_and_ocr_use_source_content(self):
        cases = [
            ("refund.docx", make_docx([("Title", "Refund policy"), ("", "Annual plans get prorated refunds.")])),
            ("payments.pdf", make_pdf("Payment settings for annual plans")),
            ("support.txt", b"Support hours\nWe reply within 24 hours."),
            ("screen.png", make_png()),
        ]
        with patch("multimodal.ocr.ocr_bytes", return_value=("Payment settings\nChoose Save.", 2)):
            for name, content in cases:
                with self.subTest(name=name):
                    result = self.mk.upload(name, content)
                    self.assertEqual(result["status"], "pending")
                    self.assertTrue(self.mk.get(result["article_id"])["editable_text"])
            art = self.mk.get(result["article_id"])
            self.assertEqual(len(art["images"]), 1)
            self.mk.approve(result["article_id"], "Sam")
            self.assertIn("screen.png", str(self.store.active_chunks()))

    def test_long_opening_paragraph_uses_filename_without_losing_text(self):
        content = ("These instructions explain how to configure billing. " * 5).encode()
        result = self.mk.upload("billing_setup-guide.txt", content)
        self.assertEqual(result["title"], "billing setup guide")
        self.assertEqual(self.mk.get(result["article_id"])["editable_text"], content.decode().strip())

    def test_markdown_bom_and_trailing_hash_in_heading_are_preserved(self):
        result = self.mk.upload("guide.md", "\ufeff# Working with C#\n\nKeep this text.\n\n### Example ###\nCode here.".encode())
        art = self.mk.get(result["article_id"])
        self.assertEqual(result["title"], "Working with C#")
        self.assertEqual([s["heading"] for s in art["sections"]], ["Working with C#", "Example"])

    def test_retry_keeps_edits_and_review_decisions_even_in_a_new_service_instance(self):
        aid = self.mk.upload("holiday.md", DOCUMENT)["article_id"]
        self.mk.edit(aid, "Corrected support hours", "Support replies within 12 hours.", "Sam")
        again = ManualKnowledge(self.store, embed=fake_embed, upload_dir=Path(self.tmp.name))
        for status in ("pending", "approved"):
            if status == "approved":
                self.mk.approve(aid, "Sam")
            with patch("app.manual.extract", side_effect=AssertionError("must not re-extract")):
                result = again.upload("holiday.md", DOCUMENT)
            self.assertTrue(result["existing"])
            self.assertEqual((result["article_id"], result["status"]), (aid, status))
            self.assertEqual(self.mk.get(aid)["title"], "Corrected support hours")
        other = self.mk.upload("decline.md", DOCUMENT)["article_id"]
        self.mk.reject(other, "Sam")
        self.assertEqual(again.upload("decline.md", DOCUMENT)["status"], "rejected")
        self.assertEqual(len(self.mk.list()), 2)

    def test_unreadable_unsupported_and_oversized_files_do_not_create_articles(self):
        with patch("multimodal.ocr.ocr_bytes", return_value=("", 0)):
            for name, content in [("empty.txt", b""), ("bad.docx", b"invalid"), ("old.doc", b"x"),
                                  ("picture.png", make_png()), ("big.txt", b"x" * (10 * 1024 * 1024 + 1))]:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    self.mk.upload(name, content)
        self.assertEqual(self.mk.list(), [])
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])

    def test_blank_title_cannot_silently_approve_the_original_title(self):
        aid = self.mk.upload("holiday.md", DOCUMENT)["article_id"]
        with self.assertRaises(ValueError):
            self.mk.approve(aid, "Sam", title="")
        self.assertEqual(self.mk.get(aid)["review_status"], "pending")


class AutoUploadApiTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from app.answer import Brain
        from app.escalations import Escalations
        from shared.llm_gateway import Gateway, UsageStore

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        with patch.object(settings, "DB_PATH", Path(self.tmp.name) / "import.db"):
            from app import main
        self.store = KBStore(":memory:")
        self.addCleanup(self.store._mem.close)
        self.mk = ManualKnowledge(self.store, embed=fake_embed, upload_dir=Path(self.tmp.name) / "uploads")
        brain = Brain.__new__(Brain)
        brain.store, brain.gateway = self.store, Gateway(store=UsageStore(":memory:"), mode="offline")
        brain.embedder = type("E", (), {"dim": 384, "encode_query": staticmethod(lambda q: fake_embed([q])[0])})()
        brain.escalations = Escalations(self.store, eval_file=None)
        brain._index, brain._lock = None, threading.Lock()
        for target, attribute, value in [(main, "store", self.store), (main, "_brain", brain),
                                          (main, "manual", lambda: self.mk), (settings, "MIN_SCORE", 0.0),
                                          (settings, "API_TOKEN", "")]:
            p = patch.object(target, attribute, value)
            p.start()
            self.addCleanup(p.stop)
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)

    def upload(self, name="holiday.md", content=DOCUMENT):
        return self.client.post("/articles/upload", files={"file": (name, content)})

    def query_sources(self):
        response = self.client.post("/query", json={"question": "When does support reply?", "escalate": False})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["sources"]

    def test_upload_edit_pending_approve_and_decline_end_to_end(self):
        result = self.upload()
        self.assertEqual(result.status_code, 201, result.text)
        aid = result.json()["article_id"]
        self.assertEqual(self.query_sources(), [])
        response = self.client.put(f"/articles/{aid}", json={"title": "Holiday hours", "text": "We reply within 12 hours.",
                                                            "editor": "Sam"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["review_status"], "pending")
        self.assertEqual(self.query_sources(), [])
        self.assertEqual(self.client.post(f"/articles/{aid}/approve", json={"reviewer": "Sam"}).status_code, 200)
        self.assertEqual(self.query_sources()[0]["title"], "Holiday hours")
        self.assertIn("12 hours", self.mk.get(aid)["body_text"])
        self.assertEqual(self.upload().json()["status"], "approved")
        other = self.upload("decline.txt", b"Decline this article. All support is closed.").json()["article_id"]
        self.assertEqual(self.client.post(f"/articles/{other}/reject", json={"reviewer": "Sam"}).status_code, 200)
        self.assertEqual(self.upload("decline.txt", b"Decline this article. All support is closed.").json()["status"], "rejected")
        self.assertEqual(self.client.get("/articles", params={"status": "pending"}).json(), [])
        self.assertNotIn(other, {c["article_id"] for c in self.store.active_chunks()})

    def test_upload_auth_limits_and_bad_files(self):
        self.assertEqual(self.upload("too-big.txt", b"x" * (10 * 1024 * 1024 + 1)).status_code, 413)
        self.assertEqual(self.upload("bad.png", b"invalid").status_code, 422)
        self.assertEqual(self.client.post("/articles/upload").status_code, 422)
        with patch.object(settings, "API_TOKEN", "test-token"):
            self.assertEqual(self.upload().status_code, 401)
            result = self.client.post("/articles/upload", headers={"Authorization": "Bearer test-token"},
                                      files={"file": ("holiday.md", DOCUMENT)})
            self.assertEqual(result.status_code, 201, result.text)

    def test_screen_autofills_once_and_saves_edits_without_publishing(self):
        from streamlit.testing.v1 import AppTest

        uploaded = io.BytesIO(DOCUMENT)
        uploaded.name, uploaded.type = "holiday.md", "text/markdown"
        second = io.BytesIO(b"Decline this article\nSupport closes on Mondays.")
        second.name, second.type = "decline.txt", "text/plain"
        broken = io.BytesIO(b"not a picture")
        broken.name, broken.type = "broken.png", "image/png"
        calls = []

        def request(method, url, **kwargs):
            calls.append((method, url))
            kwargs.pop("timeout", None)
            return self.client.request(method, url, **kwargs)

        def files(label, **kwargs):
            return [uploaded, second, broken] if kwargs.get("key") == "auto_knowledge_files" else []

        with patch("httpx.request", side_effect=request), patch("streamlit.file_uploader", side_effect=files):
            screen = AppTest.from_file(str(HERE / "ui" / "chat.py"), default_timeout=30).run()
            self.assertFalse(screen.exception)
            self.assertEqual(len(self.mk.list()), 2)  # a bad file does not block the other uploads
            self.assertTrue(any("broken.png" in e.value for e in screen.error))
            aid = next(a["article_id"] for a in self.mk.list() if a["title"] == "Holiday support hours")
            other = next(a["article_id"] for a in self.mk.list() if a["article_id"] != aid)
            self.assertEqual(screen.text_input(key=f"rt_{aid}").value, "Holiday support hours")
            self.assertIn("24 hours", screen.text_area(key=f"rx_{aid}").value)
            screen.text_input(key="me").set_value("Sam").run()
            screen.text_area(key=f"rx_{aid}").set_value("Support replies within 12 hours.")
            next(b for b in screen.button if "Save edits (keep pending)" in b.label and aid in b.key).click().run()
            self.assertFalse(screen.exception)
            self.assertEqual(self.mk.get(aid)["review_status"], "pending")
            self.assertIn("12 hours", self.mk.get(aid)["editable_text"])
            self.assertEqual(self.query_sources(), [])
            next(b for b in screen.button if b.label == "✅ Approve" and aid in b.key).click().run()
            self.assertFalse(screen.exception)
            self.assertEqual(self.mk.get(aid)["review_status"], "approved")
            next(b for b in screen.button if b.label == "❌ Decline" and other in b.key).click().run()
            self.assertFalse(screen.exception)
            self.assertEqual(self.mk.get(other)["review_status"], "rejected")
            self.assertEqual(len([url for method, url in calls if method == "POST" and url.endswith("/articles/upload")]), 3)


if __name__ == "__main__":
    unittest.main()
