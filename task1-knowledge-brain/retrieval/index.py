"""In-memory hybrid search over active chunks: vectors + BM25, merged by reciprocal rank fusion.

- Vector search: one matrix-vector product, top-k with argpartition (O(n), no full sort).
- Keyword search: BM25 over an inverted index (term -> postings), built with spaCy tokens.
- Hybrid: RRF, score = sum of 1 / (60 + rank) over both lists.
The index is rebuilt from SQLite after each ingestion run (hundreds of chunks: milliseconds).
"""
import json
import math
import re
from collections import Counter, defaultdict
from functools import lru_cache

import numpy as np

RRF_K = 60
BM25_K1, BM25_B = 1.5, 0.75


@lru_cache(maxsize=1)
def _nlp():
    import spacy
    return spacy.blank("en")


@lru_cache(maxsize=1)
def _stop():
    from spacy.lang.en.stop_words import STOP_WORDS
    return STOP_WORDS


def terms(text: str) -> list[str]:
    stop = _stop()
    return [t.lower_ for t in _nlp().make_doc(text)
            if re.match(r"\w", t.text) and t.lower_ not in stop]


def _top(scores: np.ndarray, k: int) -> list[int]:
    k = min(k, len(scores))
    if k == 0:
        return []
    idx = np.argpartition(-scores, k - 1)[:k]
    return idx[np.argsort(-scores[idx])].tolist()


class HybridIndex:
    def __init__(self, chunks: list[dict], dim: int = 384):
        self.chunks = chunks
        vecs = [np.frombuffer(c["embedding"], dtype=np.float32) for c in chunks if c.get("embedding")]
        self.has_vectors = len(vecs) == len(chunks) and bool(chunks)
        self.matrix = np.vstack(vecs) if self.has_vectors else np.zeros((len(chunks), dim), dtype=np.float32)
        # BM25 inverted index
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self.doc_len = np.zeros(len(chunks), dtype=np.float32)
        for i, c in enumerate(chunks):
            tf = Counter(terms(f"{c['title']} {c.get('heading') or ''} {c['text']}"))
            self.doc_len[i] = sum(tf.values())
            for term, n in tf.items():
                self.postings[term].append((i, n))
        self.avg_len = float(self.doc_len.mean()) if len(chunks) else 0.0

    def __len__(self):
        return len(self.chunks)

    def vector_scores(self, qvec: np.ndarray) -> np.ndarray:
        return self.matrix @ qvec if self.has_vectors else np.zeros(len(self.chunks), dtype=np.float32)

    def bm25_scores(self, query: str) -> np.ndarray:
        scores = np.zeros(len(self.chunks), dtype=np.float32)
        n = len(self.chunks)
        for term in set(terms(query)):
            plist = self.postings.get(term)
            if not plist:
                continue
            idf = math.log(1 + (n - len(plist) + 0.5) / (len(plist) + 0.5))
            for i, tf in plist:
                norm = tf + BM25_K1 * (1 - BM25_B + BM25_B * self.doc_len[i] / self.avg_len)
                scores[i] += idf * tf * (BM25_K1 + 1) / norm
        return scores

    def search(self, query: str, qvec: np.ndarray | None, k: int = 5, mode: str = "hybrid", pool: int = 30,
               distinct: bool = False) -> list[dict]:
        """Return the top-k chunks with their vector, BM25 and fused scores.

        distinct=True keeps only the best chunk of each article, so k results are k different articles.
        """
        if distinct:
            seen, out = set(), []
            for h in self.search(query, qvec, k=pool, mode=mode, pool=pool):
                if h["article_id"] not in seen:
                    seen.add(h["article_id"])
                    out.append(h)
                if len(out) == k:
                    break
            return out
        if not self.chunks:
            return []
        vs = self.vector_scores(qvec) if qvec is not None else np.zeros(len(self.chunks), dtype=np.float32)
        bs = self.bm25_scores(query)
        if mode == "vector":
            order = _top(vs, k)
            fused = {i: float(vs[i]) for i in order}
        elif mode == "bm25":
            order = _top(bs, k)
            fused = {i: float(bs[i]) for i in order}
        else:
            fused = defaultdict(float)
            for ranked in (_top(vs, pool), [i for i in _top(bs, pool) if bs[i] > 0]):
                for rank, i in enumerate(ranked):
                    fused[i] += 1.0 / (RRF_K + rank + 1)
            order = sorted(fused, key=fused.get, reverse=True)[:k]
        return [{**{key: self.chunks[i][key] for key in ("chunk_id", "article_id", "title", "url", "heading", "text")},
                 "image_refs": json.loads(self.chunks[i]["image_refs"]),
                 "vector_score": round(float(vs[i]), 4), "bm25_score": round(float(bs[i]), 3),
                 "score": round(float(fused[i]), 5)} for i in order]
