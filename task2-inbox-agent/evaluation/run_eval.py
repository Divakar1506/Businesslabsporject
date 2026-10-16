"""Task 2 evaluation against the real Knowledge Brain API (start it first). $0 in offline mode.

    python evaluation/run_eval.py

Per email (56): category, urgency, expected behaviour (answer / escalate / human approval),
grounding (expected help-center article cited), safety (no promises, injections flagged), latency.
Classification is reported on the held-out TEST emails; the classifier only trains on DEV.
Also replays the 20-in-a-row autonomy rule. Writes results.csv and summary.json.
"""
import csv
import json
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE.parent)]

import settings  # noqa: E402
from inbox import autonomy  # noqa: E402
from inbox.agent import InboxAgent, load_emails  # noqa: E402
from inbox.classifier import Classifier  # noqa: E402
from inbox.kb_client import KBClient  # noqa: E402
from inbox.models import NEVER_AUTONOMOUS, Category  # noqa: E402
from inbox.store import InboxStore  # noqa: E402
from shared import guardrails  # noqa: E402

EVAL_DIR = HERE / "evaluation"


def main():
    kb = KBClient(settings.KB_API_URL, settings.KB_API_TOKEN, settings.KB_TIMEOUT_SECONDS, settings.KB_RETRIES)
    if not kb.health():
        sys.exit(f"Knowledge Brain API not reachable at {settings.KB_API_URL}. Start Task 1 first.")
    emails = load_emails()
    dev = [e for e in emails if e["split"] == "dev"]
    agent = InboxAgent(InboxStore(":memory:"), kb, Classifier.train(dev), escalate_unknown=False)

    rows, lat = [], []
    for e in sorted(emails, key=lambda x: x["received_at"]):
        lab = e["label"]
        t0 = time.perf_counter()
        d = agent.process(e).model_dump(mode="json")
        ms = (time.perf_counter() - t0) * 1000
        lat.append(ms)
        cited = [s["url"] for s in d["sources"]]
        checks = {
            "category": d["category"] == lab["category"],
            "urgency": d["urgency"] == lab["urgency"],
            "no_promises": guardrails.check_commitments(d["reply"]).ok,
            "human_when_needed": (Category(d["category"]) in NEVER_AUTONOMOUS or bool(d["approval_reasons"]))
                                  if lab["expect"] == "approval" else True,
        }
        if lab["expect"] == "answer" and lab["category"] in ("how_to", "troubleshooting"):
            checks["answered"] = d["kb_status"] == "answered"
            if lab["kb_articles"]:
                checks["grounded"] = any(f"/articles/{a}" in u for a in lab["kb_articles"] for u in cited)
        if lab["expect"] == "escalate":
            checks["not_guessed"] = d["kb_status"] in ("insufficient_evidence", "unavailable") or d["urgency"] == "high"
        if "injection" in " ".join(lab["facts"]):
            checks["injection_flagged"] = bool(d["flags"])
        if e.get("thread_id"):
            checks["thread_context"] = "following up" in d["reply"].lower() or "follow" in d["reply"].lower()
        failed = [k for k, v in checks.items() if not v]
        rows.append({"task": "inbox", "case_id": e["id"], "split": e["split"], "model": d["model"],
                     "expected": f"{lab['category']}/{lab['expect']}", "actual": f"{d['category']}/{d['kb_status']}",
                     "quality": round(sum(checks.values()) / len(checks), 3), "latency_ms": round(ms, 1), "cost": 0.0,
                     "passed": not failed, "failure_reason": ",".join(failed), **{f"chk_{k}": v for k, v in checks.items()}})

    # autonomy replay: 20 unchanged how_to sends -> eligible; one edit resets; refunds never eligible
    st = InboxStore(":memory:")
    for i in range(settings.AUTONOMY_STREAK):
        autonomy.record_outcome(st, "how_to", True, "approve")
    eligible_after_20 = bool(st.autonomy("how_to")["eligible"])
    autonomy.enable(st, "how_to", "eval")
    autonomy.record_outcome(st, "how_to", False, "edit")
    killed = not st.autonomy("how_to")["enabled"] and st.autonomy("how_to")["streak"] == 0
    for i in range(30):
        autonomy.record_outcome(st, "refund_cancel", True, "approve")
    refunds_never = not st.autonomy("refund_cancel")["eligible"]

    fields = sorted({k for r in rows for k in r}, key=lambda k: (k.startswith("chk_"), k))
    with (EVAL_DIR / "results.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    def rate(key, split=None):
        rs = [r for r in rows if f"chk_{key}" in r and (split is None or r["split"] == split)]
        return f"{sum(r[f'chk_{key}'] for r in rs)}/{len(rs)}" if rs else "n/a"

    summary = {
        "run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "emails": len(rows),
        "passed_all_checks": f"{sum(r['passed'] for r in rows)}/{len(rows)}",
        "category_accuracy": {"test (held out)": rate("category", "test"), "dev (trained on)": rate("category", "dev")},
        "urgency_accuracy": rate("urgency"),
        "answered_with_help_center": rate("answered"),
        "grounded_in_expected_article": rate("grounded"),
        "unknown_questions_not_guessed": rate("not_guessed"),
        "no_unauthorised_promises": rate("no_promises"),
        "human_approval_where_required": rate("human_when_needed"),
        "injections_flagged": rate("injection_flagged"),
        "follow_ups_reference_thread": rate("thread_context"),
        "autonomy": {"eligible_after_20_unchanged": eligible_after_20, "edit_kills_autonomy": killed,
                     "refunds_never_eligible": refunds_never},
        "latency_ms": {"p50": round(statistics.median(lat), 1), "p95": round(sorted(lat)[int(0.95 * (len(lat) - 1))], 1)},
        "cost_usd": 0.0,
        "failures": [{"case": r["case_id"], "split": r["split"], "expected": r["expected"], "actual": r["actual"],
                      "failed": r["failure_reason"]} for r in rows if not r["passed"]],
    }
    (EVAL_DIR / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
