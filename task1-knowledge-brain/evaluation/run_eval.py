"""Task 1 evaluation. Runs offline at $0 against the current knowledge base.

    python evaluation/run_eval.py

1. Retrieval: vector vs BM25 vs hybrid - hit@1, hit@5, MRR on answerable cases.
2. Threshold calibration: sweep the "I don't know" cut-off on the DEV split only, pick the best,
   then report accuracy on the held-out TEST split (so the number is not tuned to itself).
3. End to end: every case through Brain.query at the chosen threshold - answered/abstained,
   correct citation, injection flagged, latency. Cost is $0 (offline extractive answers).

Writes evaluation/results.csv (one row per case and mode) and evaluation/summary.json.
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
from app.answer import Brain  # noqa: E402
from ingestion.store import KBStore  # noqa: E402

EVAL_DIR = HERE / "evaluation"
MODES = ("vector", "bm25", "hybrid")


def load_cases():
    return [json.loads(l) for l in (EVAL_DIR / "cases.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]


def rank_of(hits, expect):
    for i, h in enumerate(hits, 1):
        if h["article_id"] in expect:
            return i
    return None


def accuracy(rows, t):
    """A case is right if: answerable -> score >= t AND an expected article is in the top 5;
    unanswerable -> score < t."""
    ok = 0
    for r in rows:
        if r["type"] == "unanswerable":
            ok += r["best"] < t
        else:
            ok += r["best"] >= t and r["rank"] is not None
    return ok / len(rows) if rows else 0.0


def main():
    store = KBStore(settings.DB_PATH)
    brain = Brain(store)
    active = {c["article_id"] for c in store.active_chunks()}
    cases = load_cases()
    valid, invalid = [], []
    for c in cases:
        (valid if all(e in active for e in c["expect"][:1]) or not c["expect"] else invalid).append(c)

    # 1. retrieval comparison: 6 configurations, measured separately on dev and test
    configs = [(m, d) for m in MODES for d in (False, True)]
    name = lambda m, d: f"{m}{' + distinct articles' if d else ''}"  # noqa: E731
    stats = {(cfg, sp): {"hit1": 0, "hit5": 0, "rr": []} for cfg in configs for sp in ("dev", "test")}
    hits_by = {}
    for c in valid:
        qvec = brain.embedder.encode_query(c["question"])
        for cfg in configs:
            hits = brain.index.search(c["question"], qvec, k=5, mode=cfg[0], distinct=cfg[1])
            hits_by[(c["id"], cfg)] = hits
            if c["expect"]:
                r = rank_of(hits, c["expect"])
                st = stats[(cfg, c["split"])]
                st["hit1"] += r == 1
                st["hit5"] += r is not None
                st["rr"].append(1 / r if r else 0.0)

    def summary_of(cfg, sp):
        st = stats[(cfg, sp)]
        n = len(st["rr"]) or 1
        return {"hit@1": round(st["hit1"] / n, 3), "hit@5": round(st["hit5"] / n, 3),
                "MRR": round(statistics.mean(st["rr"]) if st["rr"] else 0, 3)}

    retrieval_summary = {name(*cfg): {"dev": summary_of(cfg, "dev"), "test": summary_of(cfg, "test")} for cfg in configs}
    # choose on DEV only: best hit@5, then best MRR, then the simpler configuration
    best_cfg = max(configs, key=lambda cfg: (summary_of(cfg, "dev")["hit@5"], summary_of(cfg, "dev")["MRR"],
                                             -configs.index(cfg)))
    settings.SEARCH_MODE, settings.DISTINCT_ARTICLES = best_cfg
    per_case = []
    for c in valid:
        hits = hits_by[(c["id"], best_cfg)]
        per_case.append({"id": c["id"], "split": c["split"], "type": c["type"],
                         "best": max((h["vector_score"] for h in hits), default=0.0),
                         "rank": rank_of(hits, c["expect"]) if c["expect"] else None})

    # 2. threshold calibration on dev, reported on test
    dev = [r for r in per_case if r["split"] == "dev"]
    test = [r for r in per_case if r["split"] == "test"]
    grid = [round(0.50 + i * 0.01, 2) for i in range(31)]
    dev_acc = {t: accuracy(dev, t) for t in grid}
    best_acc = max(dev_acc.values())
    plateau = [t for t in grid if dev_acc[t] == best_acc]
    chosen = plateau[len(plateau) // 2]  # middle of the best plateau: furthest from both failure edges
    calibration = {"chosen_threshold": chosen, "dev_accuracy": round(best_acc, 3),
                   "test_accuracy": round(accuracy(test, chosen), 3),
                   "test_accuracy_at_current_setting": round(accuracy(test, settings.MIN_SCORE), 3),
                   "current_setting": settings.MIN_SCORE, "best_plateau": [plateau[0], plateau[-1]],
                   "scores": {r["id"]: round(r["best"], 3) for r in per_case}}

    # 3. end to end
    results, lat = [], []
    for c in valid:
        t0 = time.perf_counter()
        r = brain.query(c["question"], escalate=False, min_score=chosen)
        ms = (time.perf_counter() - t0) * 1000
        lat.append(ms)
        cited = [s["url"] for s in r["sources"]]
        cited_ok = any(f"/articles/{e}" in u for e in c["expect"] for u in cited)
        reason = ""
        if c["type"] == "unanswerable":
            passed = r["status"] == "insufficient_evidence"
            reason = "" if passed else f"answered with score {r['retrieval_score']}"
        else:
            passed = r["status"] == "answered" and cited_ok
            if r["status"] != "answered":
                reason = f"abstained (score {r['retrieval_score']})"
            elif not cited_ok:
                reason = "expected article not cited"
            if c["type"] == "injection" and not r["flags"]:
                passed, reason = False, "injection not flagged"
        results.append({"task": "knowledge", "case_id": c["id"], "split": c["split"], "type": c["type"],
                        "model": r["model"] or "offline/extractive", "expected": "|".join(c["expect"]) or "abstain",
                        "actual": r["status"] + (":" + "|".join(cited[:3]) if cited else ""),
                        "quality": 1 if passed else 0, "latency_ms": round(ms, 1), "cost": 0.0,
                        "passed": passed, "failure_reason": reason})

    with (EVAL_DIR / "results.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0]))
        w.writeheader()
        w.writerows(results)
    by_split = {s: f"{sum(r['passed'] for r in results if r['split'] == s)}/{sum(r['split'] == s for r in results)}"
                for s in ("dev", "test")}
    summary = {
        "run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "corpus": {"active_articles": len(active), "cases": len(cases), "valid_cases": len(valid),
                   "invalid_cases": [c["id"] for c in invalid]},
        "retrieval": retrieval_summary,
        "search_config_chosen_on_dev": name(*best_cfg),
        "calibration": calibration,
        "end_to_end": {"passed": f"{sum(r['passed'] for r in results)}/{len(results)}", "by_split": by_split,
                       "p50_latency_ms": round(statistics.median(lat), 1),
                       "p95_latency_ms": round(sorted(lat)[int(0.95 * (len(lat) - 1))], 1),
                       "cost_usd": 0.0,
                       "failures": [{"case": r["case_id"], "reason": r["failure_reason"]} for r in results if not r["passed"]]},
    }
    (EVAL_DIR / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "calibration"} |
                     {"calibration": {k: v for k, v in calibration.items() if k != "scores"}}, indent=2))


if __name__ == "__main__":
    main()
