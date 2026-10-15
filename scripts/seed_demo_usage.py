"""Fill data/demo_usage.db with synthetic calls so the dashboard can be viewed before any real usage.

The demo file is separate from the real data/usage.db, and the dashboard shows a
"demo data" banner whenever it reads a file with "demo" in its name.

    python scripts/seed_demo_usage.py [--days 30] [--db data/demo_usage.db]
"""
import argparse
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared.llm_gateway import BudgetPolicy, UsageStore, pricing  # noqa: E402

PLAN = {  # task: (endpoints, models, calls per day, prompt size, output size)
    "knowledge": (["query", "image_describe"], ["claude-haiku-5-5", "claude-sonnet-5-5"], 60, 2500, 350),
    "inbox": (["classify", "draft"], ["claude-haiku-5-5", "claude-sonnet-5-5"], 35, 1800, 450),
    "scope": (["extract", "feasibility", "estimate", "proposal"], ["claude-sonnet-5-5", "claude-opus-5-5"], 6, 9000, 1800),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--db", default="data/demo_usage.db")
    args = ap.parse_args()
    Path(args.db).unlink(missing_ok=True)
    store = UsageStore(args.db)
    rng = random.Random(7)
    now = time.time()
    n = 0
    for day in range(args.days, -1, -1):
        for task, (endpoints, models, per_day, prompt, out) in PLAN.items():
            for _ in range(max(1, int(rng.gauss(per_day, per_day * 0.25)))):
                ts = now - day * 86400 - rng.uniform(0, 86400 if day else (now % 86400))
                model = rng.choice(models)
                p = int(rng.gauss(prompt, prompt * 0.3)) + 200
                cached = int(p * rng.uniform(0.3, 0.8)) if rng.random() < 0.7 else 0
                write = int(p * 0.2) if not cached and rng.random() < 0.3 else 0
                usage = dict(input_tokens=p - cached - write, cache_read_tokens=cached, cache_write_tokens=write,
                             output_tokens=int(rng.gauss(out, out * 0.3)) + 20)
                status = "error" if rng.random() < 0.02 else "ok"
                latency = rng.lognormvariate(7.2 if "haiku" in model else 7.9, 0.35)
                streamed = rng.random() < 0.5
                store.record(ts=ts, request_id=f"demo{n}", task=task, endpoint=rng.choice(endpoints), model=model,
                             requested_model=model, prompt_version=rng.choice(["v1", "v1", "v2"]),
                             **(usage if status == "ok" else {}), est_input_tokens=int(p * rng.uniform(0.85, 1.1)),
                             cost_usd=pricing.call_cost(model, **usage) if status == "ok" else 0,
                             latency_ms=latency, ttft_ms=latency * rng.uniform(0.12, 0.3) if streamed else None,
                             downgraded=int(rng.random() < 0.05),
                             context_pct=pricing.context_pct(model, p), status=status,
                             note="demo" if status == "ok" else "demo: APIConnectionError")
                n += 1
    store.set_setting("budget", {"global": {"daily_usd": 2.0, "monthly_usd": 40.0},
                                 "tasks": {"scope": {"daily_usd": 0.6}, "knowledge": {"daily_tokens": 200000}}})
    policy = BudgetPolicy(store, "claude-sonnet-5-5")
    for task in PLAN:  # raise the same alerts the gateway would after a call
        policy.after_call(task)
    print(f"wrote {n} demo calls to {args.db}")


if __name__ == "__main__":
    main()
