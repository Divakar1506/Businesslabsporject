"""Task 2 settings. Override any value with an environment variable of the same name."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent

KB_API_URL = os.environ.get("KB_API_URL", "http://localhost:8100")
KB_API_TOKEN = os.environ.get("KB_API_TOKEN", "")
KB_TIMEOUT_SECONDS = float(os.environ.get("INBOX_KB_TIMEOUT", "30"))
KB_RETRIES = int(os.environ.get("INBOX_KB_RETRIES", "2"))

DB_PATH = Path(os.environ.get("INBOX_DB", str(ROOT / "data" / "inbox.db")))
EMAILS_FILE = ROOT / "data" / "emails.jsonl"

COMPANY = os.environ.get("INBOX_COMPANY", "Brilliant Directories")
SIGNATURE = os.environ.get("INBOX_SIGNATURE", "Best regards,\nThe Support Team")

# Autonomy: 20 unchanged sends in a row make a category eligible (the brief's rule).
AUTONOMY_STREAK = int(os.environ.get("INBOX_AUTONOMY_STREAK", "20"))
# Kill switch: autonomy turns off when more than this share of the last N reviewed drafts were changed.
AUTONOMY_MAX_EDIT_RATE = float(os.environ.get("INBOX_AUTONOMY_MAX_EDIT_RATE", "0.10"))
AUTONOMY_WINDOW = int(os.environ.get("INBOX_AUTONOMY_WINDOW", "20"))
