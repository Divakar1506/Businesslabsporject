# Evaluations

All numbers below come from real runs. Nothing here is estimated. Cases that were not run are marked **pending**.
Cost column: every run so far used offline mode, so the model cost is **$0**.

## Task 1 — Knowledge Brain

**Run:** `python task1-knowledge-brain/evaluation/run_eval.py` · 2026-10-15 23:37 · 208 help-center articles ingested (of 1,537; full crawl in progress)
**Cases:** 32 in `evaluation/cases.jsonl`: 24 answerable, 2 prompt-injection, 6 not in the help center. Split into 16 **dev** (used to pick the threshold) and 16 **test** (held out, only used to report).
**Raw results:** `evaluation/results.csv`, `evaluation/summary.json`.

### Retrieval: which search finds the right article? (24 + 2 cases with a known answer)

| Method | Right article ranked 1st | Right article in top 5 | MRR |
|---|---|---|---|
| Vector only (local bge-small) | **96%** | 96% | **0.96** |
| Keyword only (BM25, spaCy tokens) | 77% | 88% | 0.82 |
| Hybrid (RRF of both) | 85% | **100%** | 0.91 |

**Choice:** hybrid. Answers cite the top 5 results, and hybrid is the only method that never missed the right article there. Vector-only ranks better at position 1, so a reranker on top of hybrid is the next thing to test.

### "I don't know" threshold (abstain below this match score)

| | Value |
|---|---|
| Best dev range | 0.66 – 0.69 (dev accuracy 94%) |
| Chosen | **0.68** (middle of that range) |
| Held-out test accuracy at 0.68 | **100% (16/16)** |

### End to end (question → answer or escalation)

| Measure | Result |
|---|---|
| Passed | **31 / 32** (dev 15/16, test 16/16) |
| Prompt injections flagged and still answered correctly | 2 / 2 |
| Off-topic questions sent to a human instead of answered | 5 / 6 |
| Latency p50 / p95 | 125 ms / 312 ms (CPU, offline) |
| Cost per answer | $0 (offline extractive answers) |

### Failure we keep (honest)

**KB-031, "Can I pay my invoice with Bitcoin?"** gets answered instead of escalated. The Spam Form Protector article contains a list of spam keywords that includes "bitcoin", which gives a match score of 0.73. A high similarity score is not the same as an answer.
*Next fix to test:* a relevance check that the evidence actually answers the question (a reranker or an LLM judge), not just mentions the words.

### Limits of this eval
- 32 cases is small: one case is about 3 percentage points.
- The corpus is still growing while the full crawl runs, so scores will shift. **Re-run and re-calibrate after the full crawl.**
- Answers are extractive (offline, $0). Claude-written answers and the comparison of two models are **pending**: they need paid calls, which are blocked by the $0 cap.
- Screenshot-only answers can't be tested yet: image descriptions need a vision model (not free locally). This is **pending**.
- Two chunking strategies have not been compared yet (**pending**).

## Task 2 — Inbox Agent
**Pending** (not built yet).

## Task 3 — Meeting to Scope
**Pending** (not built yet).
