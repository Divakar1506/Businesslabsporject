"""Task 3 evaluation: 31 cases against the real Knowledge Brain API (start Task 1 first). $0.

    python evaluation/run_eval.py

Meeting D is held out: its labels were written before it was ever run. Writes results.csv and
summary.json. Failed cases are kept.
"""
import csv
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE.parent)]

import settings  # noqa: E402
from agents import analyst  # noqa: E402
from core.kb_client import KBClient  # noqa: E402
from core.orchestrator import load_sample_meetings, run_meeting  # noqa: E402
from core.store import ApprovalError, ScopeStore  # noqa: E402
from core.transcript import parse  # noqa: E402

EVAL_DIR = HERE / "evaluation"
TESTABLE = re.compile(r"\d|\bcan\b|\beach\b|\bappear|\bevery\b|\bshow", re.I)


def main():
    kb = KBClient(settings.KB_API_URL, settings.KB_API_TOKEN)
    if not kb.health():
        sys.exit(f"Knowledge Brain API not reachable at {settings.KB_API_URL}. Start Task 1 first.")
    exp = json.loads((EVAL_DIR / "expected.json").read_text(encoding="utf-8"))
    M = {m["id"]: m for m in load_sample_meetings()}
    store = ScopeStore(":memory:")
    rows, lat = [], {}

    def case(cid, split, name, passed, detail=""):
        rows.append({"task": "scope", "case_id": cid, "split": split, "case": name, "model": "rules+offline",
                     "passed": bool(passed), "quality": 1 if passed else 0, "cost": 0.0, "failure_reason": "" if passed else detail})

    data = {}
    for mid in ("meeting_a", "meeting_b", "meeting_d"):
        m = M[mid]
        t0 = time.perf_counter()
        v = run_meeting(store, kb, m["project"], m["client"], mid, m["date"], m["text"])
        lat[mid] = round((time.perf_counter() - t0) * 1000, 1)
        data[mid] = store.version(store.project(m["project"]), v)["data"]

    n = 1
    for mid in ("meeting_a", "meeting_b", "meeting_d"):
        e, d = exp[mid], data[mid]
        sp = e["split"]
        reqs = {r["id"]: r for r in d["requirements"]}
        got_conf = {k for k, r in reqs.items() if r["status"] == "confirmed"}
        got_uncl = {k for k, r in reqs.items() if r["status"] == "unclear"}
        expected_all = set(e["confirmed"]) | set(e["unclear"])
        missing, extra = expected_all - set(reqs), set(reqs) - expected_all
        case(f"SC-{n:02d}", sp, f"{mid} requirement recall", not missing, f"missing {sorted(missing)}"); n += 1
        case(f"SC-{n:02d}", sp, f"{mid} requirement precision", not extra, f"unexpected {sorted(extra)}"); n += 1
        wrong = [k for k, p in e["confirmed"].items() if k not in got_conf or reqs[k]["priority"] != p]
        wrong += [k for k in e["unclear"] if k not in got_uncl]
        case(f"SC-{n:02d}", sp, f"{mid} status and priority", not wrong, f"wrong {wrong}"); n += 1
        out = {r["id"] for r in d["facts"]["out_of_scope"]}
        ok = (d["scope"]["deadline"] == e["deadline"] and e["budget_contains"] in (d["scope"]["budget"] or "")
              and out == set(e["out_of_scope"]))
        case(f"SC-{n:02d}", sp, f"{mid} deadline, budget, exclusions", ok,
             f"deadline={d['scope']['deadline']} budget={d['scope']['budget']} out={sorted(out)}"); n += 1
        feas_wrong = [f"{k}: {reqs[k]['feasibility']['cls']} (expected {c})" for k, c in e["feasibility"].items()
                      if k in reqs and reqs[k]["feasibility"]["cls"] != c]
        case(f"SC-{n:02d}", sp, f"{mid} feasibility via Task 1 API", not feas_wrong, "; ".join(feas_wrong)); n += 1
        lo, hi = e["reference_hours_likely"]
        likely = d["estimate"]["total_likely"]
        case(f"SC-{n:02d}", sp, f"{mid} estimate within reference range {lo}-{hi} h", lo <= likely <= hi,
             f"likely {likely} h"); n += 1
        vague = [a for a in d["scope"]["acceptance"] if not TESTABLE.search(a)]
        case(f"SC-{n:02d}", sp, f"{mid} acceptance criteria testable", not vague, f"vague: {vague[:2]}"); n += 1
        if e.get("injection"):
            case(f"SC-{n:02d}", sp, f"{mid} injection flagged and not obeyed",
                 any("not obeyed" in f for f in d["facts"]["flags"]) and "free" not in d["proposal"].lower(), ""); n += 1

    # follow-up meeting C against the approved baseline of A
    pid = store.project(M["meeting_a"]["project"])
    try:
        run_meeting(store, kb, M["meeting_c"]["project"], M["meeting_c"]["client"], "meeting_c",
                    M["meeting_c"]["date"], M["meeting_c"]["text"], "meeting_a")
        refused = False
    except ApprovalError:
        refused = True
    case(f"SC-{n:02d}", "dev", "follow-up refused without an approved baseline", refused); n += 1
    store.approve(pid, 1, "eval")
    m = M["meeting_a"]
    run_meeting(store, kb, m["project"], m["client"], "meeting_a_redraft", m["date"], m["text"])  # unapproved draft v2
    t0 = time.perf_counter()
    vc = run_meeting(store, kb, M["meeting_c"]["project"], M["meeting_c"]["client"], "meeting_c",
                     M["meeting_c"]["date"], M["meeting_c"]["text"], "meeting_a")
    lat["meeting_c"] = round((time.perf_counter() - t0) * 1000, 1)
    cr = store.version(pid, vc)["data"]["change_report"]
    kinds = {}
    for c in cr["changes"]:
        kinds.setdefault(c["kind"], set()).add(c["req_id"])
    ec = exp["followup_c"]
    for kind in ("removed", "added", "modified", "unchanged"):
        case(f"SC-{n:02d}", "dev", f"follow-up: {kind} detected", kinds.get(kind, set()) == set(ec[kind]),
             f"got {sorted(kinds.get(kind, set()))}"); n += 1
    case(f"SC-{n:02d}", "dev", "follow-up: added items flagged out of scope",
         all(c["out_of_scope"] for c in cr["changes"] if c["kind"] == "added") and cr["hours_change_requests"] > 0); n += 1
    case(f"SC-{n:02d}", "dev", "follow-up: deadline change detected",
         (cr["deadline_before"], cr["deadline_after"]) == (ec["deadline_before"], ec["deadline_after"]),
         f"{cr['deadline_before']} -> {cr['deadline_after']}"); n += 1
    case(f"SC-{n:02d}", "dev", "compared with approved baseline, not the newer draft", cr["baseline_version"] == 1,
         f"baseline v{cr['baseline_version']}"); n += 1

    # approval enforcement
    try:
        store.send_to_client(pid, vc, "eval")
        sent_unapproved = True
    except ApprovalError:
        sent_unapproved = False
    case(f"SC-{n:02d}", "dev", "unapproved version cannot be sent", not sent_unapproved); n += 1

    # traceability and proposal safety across all meetings
    bad_quotes = []
    for mid in ("meeting_a", "meeting_b", "meeting_c", "meeting_d"):
        f = analyst.analyse(mid, M[mid]["text"])
        turns = parse(M[mid]["text"])
        for r in f.requirements + f.out_of_scope:
            for s in r.sources:
                if not any(s.quote in t.text and s.ts == t.ts for t in turns):
                    bad_quotes.append(f"{mid} {r.id}")
    case(f"SC-{n:02d}", "all", "every requirement quote is verbatim in its transcript", not bad_quotes, str(bad_quotes)); n += 1
    blockers = [f"{mid}: {i['issue']}" for mid, d in data.items() for i in d["review_issues"] if i["severity"] == "blocker"]
    case(f"SC-{n:02d}", "all", "generated proposals pass the review (no blockers, no unsupported promises)",
         not blockers, "; ".join(blockers)); n += 1
    same = all([(r.id, r.status) for r in analyst.analyse(mid, M[mid]["text"], 60).requirements] ==
               [(r.id, r.status) for r in analyst.analyse(mid, M[mid]["text"], 100000).requirements]
               for mid in ("meeting_b", "meeting_d"))
    case(f"SC-{n:02d}", "all", "long-transcript staging: small sections = one section (B, D)", same); n += 1

    with (EVAL_DIR / "results.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    by = lambda s: f"{sum(r['passed'] for r in rows if r['split'] == s)}/{sum(r['split'] == s for r in rows)}"  # noqa: E731
    summary = {"run_at": time.strftime("%Y-%m-%d %H:%M:%S"), "cases": len(rows),
               "passed": f"{sum(r['passed'] for r in rows)}/{len(rows)}",
               "by_split": {"dev": by("dev"), "test (meeting D, held out)": by("test"), "all-meetings checks": by("all")},
               "latency_ms_per_meeting": lat, "cost_usd": 0.0,
               "estimates_likely_hours": {k: v["estimate"]["total_likely"] for k, v in data.items()},
               "failures": [{"case": r["case_id"], "split": r["split"], "name": r["case"], "why": r["failure_reason"]}
                            for r in rows if not r["passed"]]}
    (EVAL_DIR / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
