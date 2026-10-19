"""Run one ingestion pass from the command line.

    python run_ingest.py --limit 15     # first test read: 15 articles spread across the help center
    python run_ingest.py                # daily refresh: every article in the sitemap
    python run_ingest.py --images       # read the text inside screenshots (OCR, resumable)
    python run_ingest.py --images --limit 50
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]

import settings  # noqa: E402
from ingestion.pipeline import run  # noqa: E402
from ingestion.store import KBStore  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="max articles to track, or screenshots to read")
    ap.add_argument("--images", action="store_true", help="read screenshot text (OCR) instead of crawling pages")
    args = ap.parse_args()
    store = KBStore(settings.DB_PATH)
    if args.images:
        from multimodal import ocr
        from retrieval import embedder
        pending = len(ocr.pending_images(store, args.limit))
        print(f"Reading {pending} screenshot(s), one at a time (about 7 s each). Safe to stop with Ctrl+C: "
              f"it resumes where it left off.")
        report = ocr.run(store, embedder.get(settings.EMBED_MODEL).encode, args.limit, user_agent=settings.USER_AGENT)
    else:
        report = asyncio.run(run(store, args.limit))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
