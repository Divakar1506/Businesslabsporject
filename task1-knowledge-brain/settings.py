"""Task 1 settings. Override any value with an environment variable of the same name."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent


def _env(name: str, default):
    raw = os.environ.get(name)
    return type(default)(raw) if raw is not None else default


SITEMAP_URL = _env("KB_SITEMAP_URL", "https://support.brilliantdirectories.com/support/sitemap.xml")
ARTICLE_PATH = "/support/solutions/articles/"
USER_AGENT = _env("KB_USER_AGENT", "AI-Tech-Consultant-capstone/0.1 (student project; polite crawler; 1 req/1.5s)")
DB_PATH = Path(_env("KB_DB", str(ROOT / "data" / "kb.db")))

# Politeness: at most 2 requests in flight, at least 1.5 s between request starts.
CONCURRENCY = _env("KB_CONCURRENCY", 2)
DELAY_SECONDS = _env("KB_DELAY_SECONDS", 1.5)
TIMEOUT_SECONDS = _env("KB_TIMEOUT_SECONDS", 20.0)
MAX_ATTEMPTS = _env("KB_MAX_ATTEMPTS", 3)

# Chunking (estimated Claude tokens, from spaCy word counts).
CHUNK_MAX_TOKENS = _env("KB_CHUNK_MAX_TOKENS", 320)
CHUNK_OVERLAP_SENTENCES = _env("KB_CHUNK_OVERLAP_SENTENCES", 1)

# Retrieval.
EMBED_MODEL = _env("KB_EMBED_MODEL", "BAAI/bge-small-en-v1.5")
TOP_K = _env("KB_TOP_K", 5)
# Below this cosine score for the best chunk, the brain says it does not know.
# Calibrated by evaluation/run_eval.py on 2026-10-15 (208 articles): best dev plateau 0.66-0.69, middle = 0.68,
# held-out test accuracy 16/16. Re-run the eval after the full crawl; more articles shift the scores.
MIN_SCORE = _env("KB_MIN_SCORE", 0.68)

API_TOKEN = os.environ.get("KB_API_TOKEN", "")  # empty = no auth (local development)
