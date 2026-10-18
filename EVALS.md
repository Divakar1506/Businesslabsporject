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

**Run:** `python task2-inbox-agent/evaluation/run_eval.py` against the live Task 1 API · 2026-10-16 08:44 · offline mode, $0
**Data:** 56 realistic customer emails written for this project (`data/emails.jsonl`), covering 7 categories. 36 are **dev** (the classifier trains only on these) and 20 are **test** (held out). Each email is labelled with category, urgency, risk, expected behaviour, key facts and the help-center article that should be cited.
**Raw results:** `evaluation/results.csv`, `evaluation/summary.json`.

| Check | Result |
|---|---|
| Category, held-out test emails | **20/20** |
| Category, dev emails (trained on) / leave-one-out | 35/36 / 35/36 |
| Urgency | 54/56 |
| How-to/troubleshooting answered from the help center | 23/23 |
| Expected help-center article cited | 23/23 |
| Unknown questions not guessed (escalated or flagged urgent) | 3/3 |
| **No unauthorised promises** (refund, price, date, guarantee) | **56/56** |
| Human approval where required (refunds, complaints, quotes, projects) | 56/56 |
| Prompt injections flagged | 2/2 |
| Follow-ups reference the earlier email | 4/5 |
| 20-in-a-row rule: eligible after 20 · one edit switches it off · refunds never eligible | ✔ · ✔ · ✔ |
| Latency p50 / p95 | 110 ms / 340 ms |
| Cost per email | $0 |
| **Emails passing every check** | **53 / 56** |

**How the classifier was chosen (dev only, no test peeking).** Leave-one-out on the 36 dev emails:
Naive Bayes plus weak rules got 10/36, and stronger rules got 30/36. Rules that decide when they fire, with Naive Bayes as the fallback, got 31/36. After adding patterns for the 4 dev misses, it got 35/36.
An earlier version scored 16/20 on test. Its four test misses were not used to change any rule; the fix came from the dev analysis above.

**Failures we keep:**
- **E052** "I haven't heard back about my cancellation" is labelled follow-up but classified refund/cancel. This is on purpose: money rules take priority, so a follow-up about a refund can never fall into a category that may become autonomous. It is a safe miss.
- **E038** "we lost customers all weekend" is not marked urgent. The urgent list has "losing customers" but not "lost customers". This is a real gap to fix.
- **E007** is an urgency label judgement call (low vs normal).

**Learning loop (tested):** sentences that reviewers delete in 3 or more edits become a new style-rules version. It is adopted only if a replay of past reviews shows the drafts moved closer to what humans sent **and** no draft that was approved unchanged would be altered. A test showed the second guard blocking an adoption. Rolling back is one click.

**Pending:** comparing two models (Claude Haiku vs Sonnet drafts) needs paid calls, which the $0 cap blocks. In offline mode the drafts come from versioned templates.

## Task 3 — Meeting to Scope

**Run:** `python task3-meeting-to-scope/evaluation/run_eval.py` against the live Task 1 API · 2026-10-18 16:37 · rules + offline, $0
**Data:** four role-played meetings written for the capstone:
- **A** (search improvements)
- **B** (checkout and payments, contains a prompt-injection line)
- **C** (follow-up to A: adds, removes and changes items, and moves the deadline)
- **D** (salon booking), **held out**: its expected labels were written before D was ever run, and no rule was changed after seeing its result

Labels and reference hours are in `evaluation/expected.json`. The reference hour ranges are the author's own senior-developer estimates, not a second reviewer's.
**Raw results:** `evaluation/results.csv`, `evaluation/summary.json`.

| Group | Passed |
|---|---|
| **All cases** | **30 / 34** |
| Dev (A, B, follow-up C, approval rules) | 23 / 24 |
| **Held-out meeting D** | **4 / 7** |
| Checks across all meetings (verbatim quotes, proposal review, long-transcript staging) | 3 / 3 |

What passed, in plain words:
- Follow-up C was compared with the **approved** v1 even though a newer draft existed. It found the removed item (AI search), the 2 modified items (price filter, mobile), the 3 added items (all flagged as outside the agreed scope with their own 9.9 h change-request subtotal), and the deadline move from March 1 to April 15, 2027.
- A follow-up before any approval is **refused**. An unapproved version **cannot be sent**, and neither can one edited after approval (tested).
- Every requirement quote is found word for word in its transcript. Processing a meeting in many small sections gives exactly the same result as one big section.
- The injection line in B is flagged and has no effect on the scope or the price.
- Feasibility through the Task 1 API: in B, Stripe, PayPal, coupons, invoices and order emails were found as **existing features**. Guest checkout and ShipStation were found as custom work, with a note to confirm with an expert before quoting.
- Estimates (likely hours): A 27 h, B 69.9 h, D 35.8 h, all inside the reference ranges.
- Latency per meeting: 0.4–3.8 s (the first call warms up the embedding model).

**Failures we keep:**
- **Held-out D, 2 cases:** "A monthly newsletter… **We need that too.**" was missed. The request is in a second sentence that points back with "that", and the rules only link sentences that *start* with it/that. This is a real generalisation gap: rule extraction works on the phrasings it was built on and misses others. It is the strongest reason to add the model path (`LLM_MODE=ollama|claude`).
- **Held-out D, 1 case:** "photos in reviews" was classed *existing + adjustment* instead of *existing setting*, because the help-center match was below the 0.80 cut-off. The cut-off is a first guess and needs calibration on more labelled requirements.
- **Dev, 1 case:** the eval's "acceptance criteria are testable" check is too crude. It flags "Card payments through Stripe succeed in test and live mode" as vague, although that is testable. This is a weakness of the eval, not of the scope.

**Pending:** comparing a model-written extraction (Claude or a local Ollama model) with the rules on the same 34 cases. The paid side needs a cap above $0.
