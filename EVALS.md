# Evaluations

All numbers below come from real runs. Nothing here is estimated. Cases that were not run are marked **pending**.
Cost column: every run so far used offline mode, so the model cost is **$0**.

## Task 1 — Knowledge Brain

**Cases:** 32 in `evaluation/cases.jsonl`: 24 answerable, 2 prompt-injection, 6 not in the help center. Split into 16 **dev** (used for every choice) and 16 **test** (held out, only used to report).
**Runs:** `python run.py eval task1`. First run 2026-10-15 on **208** articles; re-run 2026-10-19 on the **full help center (1,539 articles)**. Raw results: `evaluation/results.csv`, `evaluation/summary.json`.

### What changed when the knowledge base grew 7 times
| | 208 articles | 1,539 articles (before any change) | 1,539 articles, after re-choosing on dev |
|---|---|---|---|
| Search used | hybrid | hybrid | **vector** |
| Right article in top 5 (dev / test) | 100% (all) | 92% / 92% | **100% / 92%** |
| End to end passed | 31 / 32 | 28 / 32 | **30 / 32** (dev 15/16, test 15/16) |

**Why it dropped:** with more articles, more near-duplicates compete. Of the 4 new failures, 2 were not wrong. "Translate my website" and "Sign in with Facebook" were answered from newer, equally correct articles ("Google Website Translator", "Using Facebook Login"). Their labels were **widened, and this is recorded in the case file** (`label_note`). The other 2 were real.

### Search configuration, compared on the full help center (choice made on dev only)
| Configuration | Dev: in top 5 / MRR | Test: in top 5 / MRR |
|---|---|---|
| **Vector (local bge-small)** ← chosen | **100% / 0.92** | **92% / 0.89** |
| Vector + one chunk per article | 100% / 0.92 | 92% / 0.89 |
| Hybrid (vector + BM25, RRF) | 92% / 0.79 | 92% / 0.77 |
| Hybrid + one chunk per article | 92% / 0.79 | 92% / 0.77 |
| Keyword only (BM25, spaCy tokens) | 69% / 0.53 | 85% / 0.73 |

On 208 articles hybrid had won (100% top-5). On the full help center, the keyword half adds noise ("cancel" matches billing pages), so plain vector search is better. "One chunk per article" made no difference, so it stays off.

### "I don't know" threshold
Re-checked on the full help center: the best dev range is 0.66 – 0.70, and the middle is **0.68 again** (dev 94%, held-out test 94%). The setting did not need to change.

### End to end on the full help center
| Measure | Result |
|---|---|
| Passed | **30 / 32** (dev 15/16, test 15/16) |
| Prompt injections flagged and still answered correctly | 2 / 2 |
| Off-topic questions sent to a human instead of answered | 5 / 6 |
| Latency p50 / p95 | 127 ms / 3.0 s (p95 includes the first query loading the larger index) |
| Cost per answer | $0 (offline extractive answers) |

### Failures we keep (honest)
- **KB-031, "Can I pay my invoice with Bitcoin?"** is answered instead of escalated, from a spam-keyword list that contains "bitcoin" (score 0.73). A similar word is not an answer.
- **KB-008, "Can I review new member sign-ups before they go live?"** The right article ("Manually Approve New Member Signups") is not in the top 5. Articles about *review* approval and *sign-up* settings outrank it. This new failure appeared with the full help center.
- *Next fix to test for both:* a reranker or relevance check that asks "does this evidence answer the question?", not just "is it similar?".

### Screenshots (OCR)
Text inside screenshots is now read with offline OCR (`python run.py images`). On real help-center screenshots it reads interface labels such as "Post Type Copied", "Edit Post Types" and "Search by Keyword", at about 7 s per screenshot on CPU. **6,150 screenshots** are being read in the background, and the evals above were run **before** most of them were added. A screenshot-only eval set is **pending** until the job finishes.

### Limits of this eval
- 32 cases is small: one case is about 3 percentage points.
- Answers are extractive (offline, $0). Claude-written answers and a comparison of two models are **pending**: they need paid calls, which the $0 cap blocks.
- Two chunking strategies have not been compared yet (**pending**).

## Task 2 — Inbox Agent

**Run:** `python task2-inbox-agent/evaluation/run_eval.py` against the live Task 1 API · 2026-10-16 08:44 · offline mode, $0
**Data:** 56 realistic customer emails written for this project (`data/emails.jsonl`), covering 7 categories. 36 are **dev** (the classifier trains only on these) and 20 are **test** (held out). Each email is labelled with category, urgency, risk, expected behaviour, key facts and the help-center article that should be cited.
**Raw results:** `evaluation/results.csv`, `evaluation/summary.json`.

**Re-run 2026-10-19 on the full help center (1,539 articles, vector search): 50 / 56** (was 53 / 56 on 208 articles). Two labels were widened to newer, equally correct articles (E010, E011). Three new failures are real: **E005** "Approve members first" is answered with the *review* approval article (the same confusion as Task 1 KB-008); **E024** misses the review-approval setting; **E013** "Salesforce sync" is now answered from a Zapier article at score 0.694, just over the 0.68 cut-off, instead of going to an expert. Still **56 / 56 with no unauthorised promises**, and held-out category **20 / 20**. The table below is the first run, on 208 articles.

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

**Re-run 2026-10-19 on the full help center: unchanged at 30 / 34**, with the same feasibility classes and the same estimates (27 / 69.9 / 35.8 h).

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
