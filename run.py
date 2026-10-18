"""One command to run everything from the VS Code terminal (Terminal > New Terminal, in this folder).

    python run.py all            start the Knowledge Brain API + all screens + cost dashboard (Ctrl+C stops all)
    python run.py api            Task 1 Knowledge Brain API      http://localhost:8100/docs
    python run.py chat           Task 1 chat + expert queue      http://localhost:8501
    python run.py inbox          Task 2 inbox review             http://localhost:8502
    python run.py scope          Task 3 scope builder            http://localhost:8503
    python run.py dashboard      token & cost dashboard          http://localhost:8600
    python run.py ingest [--limit N]   read help-center articles (polite crawl; no limit = all, resumes)
    python run.py test           every test suite (offline, $0)
    python run.py eval           every evaluation (starts the API if needed, $0)
    python run.py demo-data      fill the cost dashboard with demo data

Everything runs offline at $0 unless you change LLM_MODE and HARD_MONTHLY_CAP_USD in .env.
"""
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable
T1, T2, T3 = ROOT / "task1-knowledge-brain", ROOT / "task2-inbox-agent", ROOT / "task3-meeting-to-scope"
API = "http://localhost:8100"


def env() -> dict:
    e = {**os.environ, "PYTHONUTF8": "1", "KB_API_URL": API}
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                if v.strip() and k.strip() not in os.environ:
                    e[k.strip()] = v.strip()
    e.setdefault("LLM_MODE", "offline")
    e.setdefault("HARD_MONTHLY_CAP_USD", "0")
    return e


def streamlit(script: str, port: int, cwd: Path, extra: list[str] | None = None) -> list:
    return [[PY, "-m", "streamlit", "run", script, "--server.port", str(port), "--server.headless", "true",
             "--browser.gatherUsageStats", "false"] + (["--"] + extra if extra else []), cwd]


SERVICES = {
    "api": ([PY, "-m", "uvicorn", "app.main:app", "--port", "8100"], T1, f"{API}/docs"),
    "chat": (*streamlit("ui/chat.py", 8501, T1), "http://localhost:8501"),
    "inbox": (*streamlit("ui/review.py", 8502, T2), "http://localhost:8502"),
    "scope": (*streamlit("ui/app.py", 8503, T3), "http://localhost:8503"),
    "dashboard": (*streamlit("dashboard/app.py", 8600, ROOT), "http://localhost:8600"),
}


def api_up() -> bool:
    try:
        return urllib.request.urlopen(f"{API}/health", timeout=2).status == 200
    except Exception:
        return False


def wait_for_api(seconds: int = 60) -> bool:
    for _ in range(seconds):
        if api_up():
            return True
        time.sleep(1)
    return False


def start(names: list[str]) -> list[subprocess.Popen]:
    procs = []
    for name in names:
        if name == "api" and api_up():
            print("  api        already running ->", SERVICES["api"][2])
            continue
        cmd, cwd, url = SERVICES[name]
        procs.append(subprocess.Popen(cmd, cwd=cwd, env=env()))
        print(f"  {name:10} {url}")
        if name == "api" and not wait_for_api():
            print("  ! the API did not start - check the messages above")
    return procs


def hold(procs: list[subprocess.Popen]) -> None:
    print("\nRunning. Press Ctrl+C to stop everything.")
    try:
        while all(p.poll() is None for p in procs):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            p.terminate()
        print("Stopped.")


def run(cmd: list[str], cwd: Path) -> int:
    print(f"\n$ ({cwd.name}) {' '.join(cmd[1:])}")
    return subprocess.call(cmd, cwd=cwd, env=env())


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    what, rest = argv[0], argv[1:]
    if what == "all":
        if not (T1 / "data" / "kb.db").exists():
            print("No knowledge base yet: run  python run.py ingest --limit 20  first (about 1 minute).")
        print("Starting:")
        hold(start(["api", "chat", "inbox", "scope", "dashboard"]))
        return 0
    if what in SERVICES:
        procs = start(["api", what] if what in ("chat", "inbox", "scope") else [what])
        hold(procs) if procs else print("Nothing to start.")
        return 0
    if what == "ingest":
        return run([PY, "run_ingest.py", *rest], T1)
    if what == "test":
        codes = [run([PY, "-m", "unittest", "discover", "-s", "tests", "-t", "."], d) for d in (ROOT, T1, T2, T3)]
        print("\nALL TESTS PASSED" if not any(codes) else "\nSOME TESTS FAILED")
        return int(any(codes))
    if what == "eval":
        procs = [] if api_up() else start(["api"])
        try:
            codes = [run([PY, "evaluation/run_eval.py"], d) for d in (T1, T2, T3)]
        finally:
            for p in procs:
                p.terminate()
        return int(any(codes))
    if what == "demo-data":
        return run([PY, "scripts/seed_demo_usage.py"], ROOT)
    print(f"Unknown command {what!r}.\n{__doc__}")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
