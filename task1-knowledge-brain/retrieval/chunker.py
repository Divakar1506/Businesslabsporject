"""Split an article into retrievable chunks with spaCy (blank English + sentencizer, free, no download).

Rules:
- Sections (headings) are hard boundaries - a payments section never shares a chunk with a refunds one.
- Inside a section, lines (list items, table rows, paragraphs) are packed whole up to CHUNK_MAX_TOKENS.
- A line that is too long on its own is split at sentence boundaries.
- The last unit of a chunk is repeated at the start of the next one (overlap), so a step keeps its context.
- Every chunk carries the article title, section heading and the section's screenshots.
"""
import hashlib
from functools import lru_cache

from shared.llm_gateway import tokens


@lru_cache(maxsize=1)
def _nlp():
    import spacy
    nlp = spacy.blank("en")
    nlp.add_pipe("sentencizer")
    return nlp


def sentences(text: str) -> list[str]:
    return [s.text.strip() for s in _nlp()(text).sents if s.text.strip()]


def _units(text: str, max_tokens: int) -> list[str]:
    out = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if tokens.estimate(line) <= max_tokens:
            out.append(line)
            continue
        for sent in sentences(line):
            if tokens.estimate(sent) <= max_tokens:
                out.append(sent)
            else:  # no sentence breaks (code, long tables): fixed word windows
                words = sent.split()
                step = max(1, int(max_tokens / 1.6))
                out.extend(" ".join(words[i:i + step]) for i in range(0, len(words), step))
    return out


def chunk_article(article: dict, max_tokens: int = 320, overlap: int = 1) -> list[dict]:
    chunks = []
    for s_idx, sec in enumerate(article["sections"]):
        units = _units(sec["text"], max_tokens)
        if not units and sec["images"]:
            units = ["(screenshot)"]
        groups, cur = [], []
        for u in units:
            # measure the joined text: the newlines between lines count as tokens too
            if cur and tokens.estimate("\n".join(cur + [u])) > max_tokens:
                groups.append(cur)
                cur = cur[-overlap:] if overlap else []
                if cur and tokens.estimate("\n".join(cur + [u])) > max_tokens:
                    cur = []  # overlap would not fit with this unit
            cur.append(u)
        if cur:
            groups.append(cur)
        for g in groups:
            text = "\n".join(g)
            ord_ = len(chunks)
            digest = hashlib.sha1(f"{article['article_id']}|{s_idx}|{text}".encode()).hexdigest()[:10]
            chunks.append({
                "chunk_id": f"{article['article_id']}-{ord_}-{digest}",
                "ord": ord_,
                "heading": sec["heading"],
                "text": text,
                "tokens": tokens.estimate(text),
                "image_refs": sec["images"],
            })
    return chunks


def embed_text(title: str, heading: str, text: str) -> str:
    """What the embedder sees: title and heading give a short chunk its topic."""
    return f"{title}\n{heading}\n{text}" if heading else f"{title}\n{text}"
