"""Turn a help-center article page into clean, structured text.

Only the <article id="article-body"> element is read, so navigation, footers and
related-article lists never reach retrieval. Headings start sections; lists and
table rows are kept as lines; every image keeps its section and nearby text.
"""
import hashlib
import re
from html import unescape
from html.parser import HTMLParser

from .sitemap import article_id

BLOCK = {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote", "br", "table", "ul", "ol"}
HEADINGS = {"h2", "h3", "h4"}
SKIP = {"script", "style", "noscript"}
VOID = {"img", "br", "hr", "meta", "link", "input", "source", "wbr", "col", "area", "base", "embed", "param", "track"}


class _ArticleHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0            # >0 while inside <article id="article-body">
        self.skip = 0             # >0 inside script/style or the title/link header
        self.blocks: list[dict] = []
        self.buf: list[str] = []
        self.kind = "p"
        self.title = ""
        self._in_title = False
        self.breadcrumb: list[str] = []
        self._in_crumb = 0
        self._crumb_a = False

    # --- breadcrumb (outside the article) ---
    def _crumb_start(self, tag, attrs):
        if tag == "div" and "breadcrumb" in (attrs.get("class") or "").split():
            self._in_crumb = 1
        elif self._in_crumb:
            if tag == "div":
                self._in_crumb += 1
            if tag == "a":
                self._crumb_a = True
                self.breadcrumb.append("")

    def flush(self):
        text = re.sub(r"\s+", " ", "".join(self.buf)).strip()
        if text:
            self.blocks.append({"kind": self.kind, "text": text})
        self.buf, self.kind = [], "p"

    def handle_starttag(self, tag, attrs_list):
        attrs = dict(attrs_list)
        if not self.depth:
            self._crumb_start(tag, attrs)
            if tag == "article" and attrs.get("id") == "article-body":
                self.depth = 1
            return
        if tag not in VOID:
            self.depth += 1
        if tag in SKIP:
            self.skip += 1
        elif tag == "h1" and "post-title" in (attrs.get("class") or ""):
            self._in_title = True
        elif "the-article-link" in (attrs.get("class") or ""):
            self.skip += 1
            self._skip_until = self.depth
        if self.skip:
            return
        if tag == "img":
            self.flush()
            src = attrs.get("src") or ""
            if src and not src.endswith(".svg"):
                self.blocks.append({"kind": "img", "src": src, "alt": attrs.get("alt") or ""})
        elif tag in BLOCK:
            self.flush()
            self.kind = ("h" if tag in HEADINGS else "li" if tag == "li" else "tr" if tag == "tr" else "p")
        elif tag in ("td", "th"):
            self.buf.append(" | ")

    def handle_endtag(self, tag):
        if not self.depth:
            if self._in_crumb:
                if tag == "a":
                    self._crumb_a = False
                elif tag == "div":
                    self._in_crumb -= 1
            return
        if tag == "h1" and self._in_title:
            self._in_title = False
            self.buf = []
        if tag in SKIP and self.skip:
            self.skip -= 1
        if getattr(self, "_skip_until", None) == self.depth and self.skip:
            self.skip -= 1
            self._skip_until = None
        if tag in BLOCK and not self.skip:
            self.flush()
        if tag not in VOID:
            self.depth -= 1
            if self.depth == 0:
                self.flush()

    def handle_data(self, data):
        if self._crumb_a and self.breadcrumb:
            self.breadcrumb[-1] += data
        if not self.depth:
            return
        if self._in_title:
            self.title += data
        elif not self.skip:
            self.buf.append(data)


def parse_article(url: str, html: str, lastmod: str | None = None) -> dict:
    """Return {article_id, url, title, breadcrumb, sections, images, body_text, content_hash}."""
    p = _ArticleHTML()
    p.feed(html)
    p.close()
    title = re.sub(r"\s+", " ", unescape(p.title)).strip()
    if not title:
        m = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
        title = re.sub(r"\s+", " ", unescape(m.group(1))).strip() if m else url
    if not p.blocks:
        raise ValueError("no article body found")

    sections: list[dict] = [{"heading": "", "lines": []}]
    images: list[dict] = []
    for i, b in enumerate(p.blocks):
        if b["kind"] == "h":
            sections.append({"heading": b["text"], "lines": []})
        elif b["kind"] == "img":
            before = next((x["text"] for x in reversed(p.blocks[:i]) if x["kind"] != "img"), "")
            after = next((x["text"] for x in p.blocks[i + 1:] if x["kind"] != "img"), "")
            images.append({"src": b["src"], "alt": b["alt"], "heading": sections[-1]["heading"],
                           "before": before[:300], "after": after[:300], "section": len(sections) - 1})
            sections[-1].setdefault("images", []).append(b["src"])
        else:
            prefix = "- " if b["kind"] == "li" else ""
            sections[-1]["lines"].append(prefix + b["text"])

    out_sections = [{"heading": s["heading"], "text": "\n".join(s["lines"]), "images": s.get("images", [])}
                    for s in sections if s["lines"] or s.get("images")]
    body = "\n\n".join((f"## {s['heading']}\n" if s["heading"] else "") + s["text"] for s in out_sections)
    crumbs = [c.strip() for c in p.breadcrumb if c.strip()]
    return {
        "article_id": article_id(url),
        "url": url,
        "title": title,
        "breadcrumb": crumbs[1:-1] if len(crumbs) > 2 else crumbs,  # drop "Solution home" and the title itself
        "sections": out_sections,
        "images": images,
        "body_text": body,
        "lastmod": lastmod,
        "content_hash": hashlib.sha256((title + "\n" + body + "".join(i["src"] for i in images)).encode()).hexdigest()[:16],
    }
