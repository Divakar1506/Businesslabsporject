"""Run one ingestion pass from the command line.

    python run_ingest.py --limit 15     # first test read: 15 articles spread across the help center
    python run_ingest.py                # daily refresh: every article in the sitemap
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
    ap.add_argument("--limit", type=int, default=None, help="max articles to track (default: all)")
    args = ap.parse_args()
    report = asyncio.run(run(KBStore(settings.DB_PATH), args.limit))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
