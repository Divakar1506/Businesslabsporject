"""Run from VS Code's Run and Debug dropdown, or run this file to open every screen.
With no arguments, starts all tasks and opens their localhost pages automatically.
Add --open-browser to a service command to open its page once it is ready.

Each task on its own (Ctrl+C stops it):
    python run.py task1          Task 1: Knowledge Brain API + chat     http://localhost:8501  (API :8100/docs)
    python run.py task2          Task 2: Inbox Agent (+ the Task 1 API it calls)     http://localhost:8502
    python run.py task3          Task 3: Meeting to Scope (+ the Task 1 API it calls) http://localhost:8503
    python run.py dashboard      token & cost dashboard                  http://localhost:8600
    python run.py api            only the Task 1 API                     http://localhost:8100/docs

Tests and evaluations, per task or all:
    python run.py test  [task1|task2|task3|shared]     offline, $0 (no name = every suite)
    python run.py eval  [task1|task2|task3]            starts the Task 1 API if needed, $0

Other:
    python run.py all            everything at once (API + all screens + dashboard)
    python run.py ingest [--limit N]   read help-center articles (polite crawl; no limit = all, resumes)
    python run.py images [--limit N]   read the text inside screenshots with offline OCR (resumable, ~7 s each)
    python run.py demo-data      fill the cost dashboard with demo data
    (older names still work: chat = task1, inbox = task2, scope = task3)

Everything runs offline at $0 unless you change LLM_MODE and HARD_MONTHLY_CAP_USD in .env.
"""
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
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
             "--browser.gatherUsageStats", "false", "--server.maxUploadSize", "10"]  # same 10 MB limit as the API
            + (["--"] + extra if extra else []), cwd]


SERVICES = {
    "api": ([PY, "-m", "uvicorn", "app.main:app", "--port", "8100"], T1, f"{API}/docs"),
    "chat": (*streamlit("ui/chat.py", 8501, T1), "http://localhost:8501"),
    "inbox": (*streamlit("ui/review.py", 8502, T2), "http://localhost:8502"),
    "scope": (*streamlit("ui/app.py", 8503, T3), "http://localhost:8503"),
    "dashboard": (*streamlit("dashboard/app.py", 8600, ROOT), "http://localhost:8600"),
}


def api_up() -> bool:
    return service_up("api")


def service_up(name: str) -> bool:
    url = f"{API}/health" if name == "api" else SERVICES[name][2] + "/_stcore/health"
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return response.status == 200
    except Exception:
        return False


def wait_for_service(name: str, process: subprocess.Popen, seconds: int = 60) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        if service_up(name):
            return True
        time.sleep(0.25)
    return False


def stop(procs: list[subprocess.Popen]) -> None:
    for process in procs:
        if process.poll() is None:
            process.terminate()
    for process in procs:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def start(names: list[str], open_browser: bool = False) -> list[subprocess.Popen]:
    procs = []
    try:
        for name in names:
            cmd, cwd, url = SERVICES[name]
            if service_up(name):
                print(f"  {name:10} already running -> {url}")
            else:
                process = subprocess.Popen(cmd, cwd=cwd, env=env())
                procs.append(process)
                print(f"  {name:10} starting -> {url}", flush=True)
                if not wait_for_service(name, process):
                    raise RuntimeError(f"{name} did not become ready. Check the messages above and port {url}.")
            # The API is a dependency; open its docs only when launched on its own.
            if open_browser and (name != "api" or names == ["api"]):
                if not webbrowser.open_new_tab(url):
                    print(f"  Browser could not open automatically. Open {url}")
    except BaseException:
        stop(procs)
        raise
    return procs


def hold(procs: list[subprocess.Popen]) -> None:
    print("\nRunning. Press Ctrl+C to stop everything.")
    try:
        while all(p.poll() is None for p in procs):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        stop(procs)
        print("Stopped.")


def run(cmd: list[str], cwd: Path) -> int:
    print(f"\n$ ({cwd.name}) {' '.join(cmd[1:])}")
    return subprocess.call(cmd, cwd=cwd, env=env())


def main(argv: list[str]) -> int:
    if not argv:
        argv = ["all", "--open-browser"]
    if argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    open_browser = "--open-browser" in argv
    argv = [arg for arg in argv if arg != "--open-browser"]
    what, rest = (argv[0], argv[1:]) if argv else ("all", [])
    what = {"task1": "chat", "task2": "inbox", "task3": "scope"}.get(what, what)
    if what == "all" or what in SERVICES:
        names = (["api", "chat", "inbox", "scope", "dashboard"] if what == "all" else
                 ["api", what] if what in ("chat", "inbox", "scope") else [what])
        try:
            procs = start(names, open_browser=open_browser)
            hold(procs) if procs else print("Already running. Pages are available at the URLs above.")
        except (RuntimeError, OSError) as exc:
            print(f"Could not start: {exc}")
            return 1
        return 0
    if what == "ingest":
        return run([PY, "run_ingest.py", *rest], T1)
    if what == "images":
        return run([PY, "run_ingest.py", "--images", *rest], T1)
    folders = {"shared": ROOT, "task1": T1, "task2": T2, "task3": T3}
    if what == "test":
        pick = [folders[t] for t in rest if t in folders] or list(folders.values())
        if any(t not in folders for t in rest):
            print(f"Unknown test target. Use one of: {', '.join(folders)}")
            return 2
        codes = [run([PY, "-m", "unittest", "discover", "-s", "tests", "-t", "."], d) for d in pick]
        print("\nALL TESTS PASSED" if not any(codes) else "\nSOME TESTS FAILED")
        return int(any(codes))
    if what == "eval":
        targets = {k: v for k, v in folders.items() if k != "shared"}
        if any(t not in targets for t in rest):
            print(f"Unknown eval target. Use one of: {', '.join(targets)}")
            return 2
        pick = [targets[t] for t in rest] or list(targets.values())
        procs = [] if api_up() else start(["api"])
        try:
            codes = [run([PY, "evaluation/run_eval.py"], d) for d in pick]
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
