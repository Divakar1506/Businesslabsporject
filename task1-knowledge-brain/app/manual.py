"""Knowledge added and corrected by people.

Upload  : a form (title, section, text, image description) plus attached files becomes an article
          `manual-<n>` with status PENDING. It is not used in answers until a human approves it.
Review  : the reviewer reads the extracted text, corrects it if needed, then approves (it moves into the
          knowledge base and is searchable at once) or rejects it.
Edit    : any article - uploaded, expert or crawled - can be corrected. The text is re-indexed, the old
          version is kept, and the crawler never overwrites a human edit; if the help-center page changes
          later, the article is flagged `source_changed` for a person to check.
Delete  : hides an article from answers (kept for audit); restore brings it back.
Files are kept under data/uploads/ and served by the API so answers can show the original image.
"""
import hashlib
import json
import re
import threading
from pathlib import Path

import settings
from ingestion.documents import Extracted, extract, safe_name
from ingestion.store import KBStore
from multimodal import ocr
from retrieval import embedder
from retrieval.chunker import chunk_article, embed_text
from shared import guardrails

UPLOAD_DIR = settings.DB_PATH.parent / "uploads"
MAX_FILES = 5
_upload_lock = threading.Lock()


class ManualKnowledge:
    def __init__(self, store: KBStore, embed=None, upload_dir: Path = UPLOAD_DIR):
        self.store = store
        self.embed = embed
        self.upload_dir = Path(upload_dir)

    def _embed(self, texts):
        return (self.embed or embedder.get(settings.EMBED_MODEL).encode)(texts)

    def _next_id(self) -> str:
        with self.store.conn() as c:
            ids = [r[0] for r in c.execute("SELECT article_id FROM articles WHERE article_id LIKE 'manual-%'")]
        n = max((int(i.split("-")[1]) for i in ids if i.split("-")[1].isdigit()), default=0) + 1
        return f"manual-{n}"

    def upload(self, name: str, data: bytes, author: str = "") -> dict:
        """A file alone becomes a populated draft. Retries never replace a reviewed or edited article."""
        digest = hashlib.sha256(name.encode() + b"\0" + data).hexdigest()[:32]
        aid = f"manual-upload-{digest}"
        with _upload_lock:
            old = self.store.article(aid)
            if old:
                return {"article_id": aid, "title": old["title"], "status": old["review_status"],
                        "existing": True, "files": [], "flags": []}
            ex = extract(name, data)
            ex.text = ex.text.lstrip("\ufeff").strip()
            if ex.kind == "unsupported" or not ex.text.strip():
                detail = "; ".join(ex.warnings) or "no readable text found"
                raise ValueError(f"{name}: {detail}. Use Write manually to add text or an image description.")
            check = guardrails.check_input(ex.text, max_chars=199_000)
            if not check.ok:
                raise ValueError(f"{name}: {'; '.join(check.issues)}")
            title = draft_title(name, ex.text)
            # Preserve the source wording; make Markdown/Word headings editable section boundaries.
            text = re.sub(r"^#{1,6}[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$", r"## \1", ex.text, flags=re.M)
            sections = from_editable(text)
            fname = safe_name(name)
            src = f"/files/{aid}/{fname}"
            images = []
            if ex.kind == "image":
                sections[0]["images"] = [src]
                images = [{"src": src, "alt": title, "heading": sections[0]["heading"],
                           "before": "", "after": "", "section": 0}]
            body = to_editable(sections)
            art = {"article_id": aid, "url": f"kb://manual/{aid}", "title": title,
                   "breadcrumb": ["Manual knowledge"], "sections": sections, "images": images,
                   "body_text": body, "lastmod": None,
                   "content_hash": hashlib.sha256((title + body).encode()).hexdigest()[:16]}
            chunks = self._index(art)
            folder = self.upload_dir / aid
            folder.mkdir(parents=True, exist_ok=True)
            (folder / fname).write_bytes(data)
            return {**self._save_pending(art, chunks, author, [ex]), "existing": False}

    def create(self, title: str, body: str = "", section: str = "", tags: str = "", image_description: str = "",
               author: str = "", files: list[tuple[str, bytes]] | None = None) -> dict:
        title = (title or "").strip()
        if len(title) < 3:
            raise ValueError("a title of at least 3 characters is required")
        files = files or []
        if len(files) > MAX_FILES:
            raise ValueError(f"at most {MAX_FILES} files per article")
        aid = self._next_id()
        folder = self.upload_dir / aid
        reports: list[Extracted] = []
        sections, images = [], []
        if body.strip():
            sections.append({"heading": "", "text": body.strip(), "images": []})
        for name, data in files:
            ex = extract(name, data)
            reports.append(ex)
            if ex.kind == "unsupported":
                continue
            fname = safe_name(name)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / fname).write_bytes(data)
            src = f"/files/{aid}/{fname}"
            if ex.kind == "image":
                caption = image_description.strip()
                text = "\n".join(x for x in (f"Image: {fname}", caption, ex.text) if x)
                images.append({"src": src, "alt": caption, "heading": f"Attachment: {fname}",
                               "before": caption[:300], "after": "", "section": len(sections)})
                sections.append({"heading": f"Attachment: {fname}", "text": text, "images": [src]})
            elif ex.text:
                sections.append({"heading": f"Attachment: {fname}", "text": ex.text, "images": []})
        if not any(s["text"].strip() for s in sections):
            raise ValueError("nothing to learn: add some text, or a file whose text can be read")

        meta = [x for x in (f"Section: {section}" if section else "", f"Tags: {tags}" if tags else "",
                            f"Added by: {author}" if author else "") if x]
        body_text = "\n\n".join((f"## {s['heading']}\n" if s["heading"] else "") + s["text"] for s in sections)
        art = {"article_id": aid, "url": f"kb://manual/{aid}", "title": title,
               "breadcrumb": ["Manual knowledge"] + ([section] if section else []),
               "sections": sections, "images": images, "body_text": "\n".join(meta) + "\n\n" + body_text,
               "lastmod": None, "content_hash": hashlib.sha256(body_text.encode()).hexdigest()[:16]}
        chunks = self._index(art)
        return self._save_pending(art, chunks, author, reports)

    def _save_pending(self, art: dict, chunks: list[dict], author: str, reports: list[Extracted]) -> dict:
        self.store.save_article(art, chunks, review_status="pending", edited_by=author or None)
        flags = [f"text contains something that looks like an instruction to the AI ({s!r}); "
                 f"it will be ignored when answering" for s in guardrails.injection_signals(art["body_text"])]
        return {"article_id": art["article_id"], "title": art["title"], "chunks": len(chunks),
                "flags": flags, "status": "pending",
                "files": [{"name": r.name, "kind": r.kind, "method": r.method, "chars": r.chars,
                           "warnings": r.warnings} for r in reports]}

    def _index(self, art: dict) -> list[dict]:
        # help-center screenshots already read by OCR stay searchable after a human edit
        ocr.ensure_schema(self.store)
        srcs = [src for s in art["sections"] for src in s.get("images", []) if src.startswith("http")]
        art = ocr.enrich(art, ocr.lookup(self.store, srcs))
        chunks = chunk_article(art, settings.CHUNK_MAX_TOKENS, settings.CHUNK_OVERLAP_SENTENCES)
        for ch, v in zip(chunks, self._embed([embed_text(art["title"], ch["heading"], ch["text"]) for ch in chunks])):
            ch["embedding"] = v.tobytes()
        return chunks

    # --- review -------------------------------------------------------------------------
    def get(self, article_id: str) -> dict:
        a = self.store.article(article_id)
        if a is None:
            raise KeyError(article_id)
        sections = json.loads(a["sections"])
        a["sections"] = sections
        a["breadcrumb"] = json.loads(a["breadcrumb"] or "[]")
        a["images"] = json.loads(a["images"] or "[]")
        a["editable_text"] = to_editable(sections)
        return a

    def approve(self, article_id: str, reviewer: str, title: str | None = None, text: str | None = None) -> dict:
        """Move a pending article into the knowledge base, with the reviewer's corrections if any."""
        if not (reviewer or "").strip():
            raise ValueError("a reviewer name is required")
        a = self.get(article_id)
        if a["review_status"] != "pending":
            raise ValueError(f"{article_id} is {a['review_status']}, not pending")
        if (title is not None and title.strip() != a["title"]) or (text is not None and text.strip() != a["editable_text"].strip()):
            self.edit(article_id, title if title is not None else a["title"],
                      text if text is not None else a["editable_text"], reviewer)
        self.store.set_review(article_id, "approved", reviewer)
        self.store.reactivate_article(article_id)
        return self.get(article_id)

    def reject(self, article_id: str, reviewer: str) -> None:
        a = self.get(article_id)
        if a["review_status"] != "pending":
            raise ValueError(f"{article_id} is {a['review_status']}, not pending")
        self.store.set_review(article_id, "rejected", reviewer or "unknown")
        self.store.deactivate_article(article_id)

    # --- edit -----------------------------------------------------------------------------
    def edit(self, article_id: str, title: str, text: str, editor: str) -> dict:
        """Correct any article. Re-indexes it, keeps the old version, and protects it from crawl overwrites."""
        if not (editor or "").strip():
            raise ValueError("an editor name is required")
        if len((title or "").strip()) < 3 or not (text or "").strip():
            raise ValueError("title (3+ characters) and text are required")
        a = self.get(article_id)
        old_images = {s["heading"]: s.get("images", []) for s in a["sections"]}
        sections = from_editable(text)
        for s in sections:
            s["images"] = old_images.get(s["heading"], [])
        body = "\n\n".join((f"## {s['heading']}\n" if s["heading"] else "") + s["text"] for s in sections)
        art = {"article_id": article_id, "url": a["url"], "title": title.strip(), "breadcrumb": a["breadcrumb"],
               "sections": sections, "images": [i for i in a["images"] if any(i["src"] in s["images"] for s in sections)],
               "body_text": body, "lastmod": a["lastmod"],
               "content_hash": hashlib.sha256((title + body).encode()).hexdigest()[:16]}
        crawled = not article_id.startswith(("manual-", "expert-"))
        self.store.save_article(
            art, self._index(art), human_edited=1, edited_by=editor.strip(), source_changed=0,
            source_hash=(a["source_hash"] or a["content_hash"]) if crawled else None,
            review_status=a["review_status"])  # editing never approves or un-approves by itself
        if not a["active"] and a["review_status"] != "pending":
            self.store.deactivate_article(article_id)  # editing a deleted article does not bring it back
        return self.get(article_id)

    def list(self, source: str = "all", query: str = "", include_removed: bool = True, limit: int = 200,
             status: str | None = None) -> list[dict]:
        where, args = [], []
        if status:
            where.append("review_status = ?")
            args.append(status)
        if source == "manual":
            where.append("article_id LIKE 'manual-%'")
        elif source == "expert":
            where.append("article_id LIKE 'expert-%'")
        elif source == "crawled":
            where.append("article_id NOT LIKE 'manual-%' AND article_id NOT LIKE 'expert-%'")
        if query:
            where.append("title LIKE ?")
            args.append(f"%{query}%")
        if not include_removed:
            where.append("active = 1")
        sql = ("SELECT article_id, title, url, breadcrumb, version, active, ingested_at, review_status, reviewed_by, "
               "human_edited, edited_by, source_changed FROM articles"
               + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY ingested_at DESC LIMIT ?")
        with self.store.conn() as c:
            rows = c.execute(sql, (*args, limit)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["breadcrumb"] = json.loads(d["breadcrumb"] or "[]")
            d["source"] = ("manual" if d["article_id"].startswith("manual-") else
                           "expert" if d["article_id"].startswith("expert-") else "crawled")
            d["files"] = sorted(p.name for p in (self.upload_dir / d["article_id"]).glob("*")) \
                if d["source"] == "manual" and (self.upload_dir / d["article_id"]).exists() else []
            out.append(d)
        return out

    def remove(self, article_id: str) -> None:
        if self.store.article(article_id) is None:
            raise KeyError(article_id)
        self.store.deactivate_article(article_id)

    def restore(self, article_id: str) -> None:
        if self.store.article(article_id) is None:
            raise KeyError(article_id)
        self.store.reactivate_article(article_id)

    def file_path(self, article_id: str, name: str) -> Path | None:
        p = (self.upload_dir / safe_name(article_id) / safe_name(name)).resolve()
        return p if p.is_file() and self.upload_dir.resolve() in p.parents else None


def draft_title(name: str, text: str) -> str:
    """Use a short opening heading/line, falling back to a readable filename. No invented content."""
    first = next((line.strip().lstrip("\ufeff") for line in text.splitlines() if line.strip()), "")
    first = re.sub(r"^#{1,6}\s+|\s+#+$", "", first).strip()
    if 3 <= len(first) <= 120 and not first.startswith(("- ", "* ", "|", "```")):
        return first
    stem = Path(name.replace("\\", "/")).stem
    title = re.sub(r"[_-]+", " ", stem).strip()
    return (title if len(title) >= 3 else "Uploaded knowledge")[:300]


def to_editable(sections: list[dict]) -> str:
    """Sections as plain text a person can edit: '## Heading' lines start new sections."""
    return "\n\n".join((f"## {s['heading']}\n" if s["heading"] else "") + s["text"] for s in sections).strip()


def from_editable(text: str) -> list[dict]:
    sections, cur = [], {"heading": "", "lines": []}
    for line in text.replace("\r\n", "\n").split("\n"):
        if line.startswith("## "):
            sections.append(cur)
            cur = {"heading": line[3:].strip(), "lines": []}
        else:
            cur["lines"].append(line)
    sections.append(cur)
    out = [{"heading": s["heading"], "text": "\n".join(s["lines"]).strip(), "images": []} for s in sections]
    return [s for s in out if s["text"] or s["heading"]]
