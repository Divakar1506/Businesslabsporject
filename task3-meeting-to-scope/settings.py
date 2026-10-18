"""Task 3 settings. Override any value with an environment variable of the same name."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent

KB_API_URL = os.environ.get("KB_API_URL", "http://localhost:8100")
KB_API_TOKEN = os.environ.get("KB_API_TOKEN", "")
DB_PATH = Path(os.environ.get("SCOPE_DB", str(ROOT / "data" / "scope.db")))
TRANSCRIPTS = ROOT / "transcripts"

# Pricing is configurable and never an approved quote by itself.
HOURLY_RATE = float(os.environ.get("SCOPE_HOURLY_RATE", "40"))
CURRENCY = os.environ.get("SCOPE_CURRENCY", "USD")
# Long transcripts are processed in sections of this many estimated tokens.
SECTION_TOKENS = int(os.environ.get("SCOPE_SECTION_TOKENS", "500"))
