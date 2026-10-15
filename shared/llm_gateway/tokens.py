"""Free, offline token estimates using spaCy's blank English tokenizer (no model download).

Claude counts sub-word tokens, so spaCy word tokens are scaled by SUBWORD_RATIO.
The dashboard compares this estimate with the real usage numbers so the ratio
can be recalibrated from data.
"""
import re
from functools import lru_cache

SUBWORD_RATIO = 1.3


@lru_cache(maxsize=1)
def _nlp():
    try:
        import spacy
        return spacy.blank("en")
    except Exception:  # spaCy missing: fall back to a regex split
        return None


def word_tokens(text: str) -> int:
    nlp = _nlp()
    if nlp is None:
        return len(re.findall(r"\w+|[^\w\s]", text))
    return len(nlp.make_doc(text))


def estimate(text: str) -> int:
    """Estimated Claude tokens for a piece of text."""
    return int(round(word_tokens(text) * SUBWORD_RATIO)) if text else 0


def messages_text(system, messages) -> str:
    """Flatten a system prompt and a messages list into plain text for estimating."""
    parts = []

    def add(content):
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))

    add(system or "")
    for m in messages:
        add(m.get("content", ""))
    return "\n".join(parts)
