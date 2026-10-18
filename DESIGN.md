# Design: AI Tech Consultant

## 1. The problem, in my own words

A small software company used to charge $2,000 for a website change. Today the client can get most of it done with an AI tool for about $200. To pay the same salaries, the company has to serve 10–20 times more clients.

Writing code is no longer the slow part. Three things are:

1. **Knowing the product.** The answer to most customer questions is somewhere in hundreds of help articles and screenshots, and those articles change every week.
2. **Answering email.** Every email is read, looked up, written and remembered by a person.
3. **Turning meetings into quotes.** A client says "make search better". Someone has to work out what that means, check whether the product already does it, estimate it and write a proposal. Later the client changes their mind, and the scope grows without anyone noticing.

So I built one system for all three, around one idea: **the product knowledge lives in one place and everything else asks it.** If knowledge were copied into each tool, every help-center change would have to be made three times, and the tools would drift apart.

## 2. How the three tasks connect

```mermaid
flowchart LR
    HC[(Brilliant Directories<br/>help center<br/>sitemap.xml)] -->|polite crawl<br/>robots.txt, 1 req/1.5 s| ING

    subgraph T1[Task 1 - Knowledge Brain - Python]
        ING[ingestion/<br/>sitemap diff · crawler · parser] --> CH[retrieval/chunker<br/>spaCy sentences]
        CH --> EMB[retrieval/embedder<br/>bge-small, NumPy, local]
        EMB --> KB[(kb.db<br/>articles · chunks · vectors<br/>versions · escalations)]
        KB --> IDX[retrieval/index<br/>vector + BM25 + RRF]
        IDX --> ANS[app/answer<br/>abstain · cite · guardrails]
        ANS --> API{{app/main FastAPI<br/>/query · /escalations · /ingestion}}
        API --> CHAT[ui/chat<br/>Ask · Expert queue]
        CHAT -->|expert answer| KB
    end

    subgraph T2[Task 2 - Inbox Agent - Python]
        EM[56 emails] --> CL[classifier<br/>rules + Naive Bayes]
        CL --> KC2[kb_client] --> DR[drafter<br/>templates / LLM]
        DR --> RV[ui/review<br/>approve · edit · reject · escalate]
        RV --> AU[autonomy<br/>20-in-a-row + kill switch]
        RV --> LE[learning<br/>versioned style rules]
    end

    subgraph T3[Task 3 - Meeting to Scope - Python]
        TR[transcripts A B C D] --> A1[analyst] --> A2[feasibility] --> A4[estimator]
        A4 --> A3[scoper] --> A5[proposal + review]
        A5 --> ST[(store<br/>versions · approval · audit)]
        ST -->|approved baseline| DF[diff] --> A2
        ST --> UI3[ui/app scope builder]
    end

    KC2 -->|HTTP only| API
    A2 -->|HTTP only| API
    ANS -. every model call .-> GW
    DR -. .-> GW
    subgraph SH[shared/]
        GW[llm_gateway<br/>offline · ollama · claude<br/>budgets · $0 cap · usage log] --> DB[(usage.db)]
        GR[guardrails<br/>injection · PII · citations · promises]
    end
    DB --> DASH[dashboard/app<br/>tokens · cost · budgets]
```

**The rule that matters most:** Task 2 (`inbox/kb_client.py`) and Task 3 (`core/kb_client.py`) only call Task 1 over HTTP. Neither imports a line of Task 1 code. Each sends `source: "inbox"` or `source: "scope"`, so the expert queue shows where a question came from. When the API is down, both say so clearly ("Knowledge service unavailable") and never invent an answer.

## 3. Features I chose, and why

### Task 1 – Knowledge Brain
| Feature | Why |
|---|---|
| **Sitemap diff + content hash** | A daily run only re-reads changed pages. The second run of 20 articles made **zero** requests. Removed pages are deactivated, not deleted, so they stop appearing in answers but can be restored. |
| **Polite, resumable crawler** | At most 2 requests at a time, at least 1.5 s apart, robots.txt checked first, retries with backoff for temporary errors only, and progress saved after every page. A stopped crawl continues where it stopped. |
| **Parser that reads only the article body** | Menus and footers would pollute search. Each screenshot keeps its section and the sentence before and after it. |
| **Chunks split at headings, sized with spaCy** | A payment section and a refund section never share a chunk. The free blank spaCy model counts tokens; there is no download. |
| **Local embeddings (bge-small in NumPy)** | $0 and private. The model was already on this computer, and PyTorch is not needed. |
| **Hybrid search** | Measured on 26 questions: vector search ranks the right article first more often (96%), but only hybrid always has it in the top 5 (100%), which is what citations need. |
| **"I don't know" threshold (0.68)** | Chosen on 16 dev questions and checked on 16 held-out ones (16/16). An unanswerable question becomes an **expert task** instead of a guess. |
| **Expert answers flow back** | *General* answers become versioned, searchable articles. *Customer-specific* ones are kept on record only, so one customer's case never becomes a product fact. Expiry dates retire stale answers. |

### Task 2 – Inbox Agent
| Feature | Why |
|---|---|
| **7 categories + urgency + risk** | Routing and safety depend on the type of email, not just its words. |
| **Rules first, Naive Bayes second** | With 36 training emails, Naive Bayes alone got 10/36 in leave-one-out; rules first got 35/36. Held-out test: **20/20**. |
| **Template drafts in the company voice** | $0, predictable, and every sentence can be traced to a template or a help article. The model path (Ollama/Claude) uses the same output schema and the same guardrails. |
| **Never promise money, dates or free work** | Refunds, complaints, quotes and project requests always need a human. A guardrail blocks promise phrases. **56/56** emails passed. |
| **20-in-a-row, made precise** | "Same kind" = same category. "Without change" = the sent text equals the draft after normalising whitespace; clicking Approve is not enough. Eligible categories still need an admin to switch them on. One human edit switches autonomy off, and so does an edit rate above 10% over the last 20. Every change is logged. |
| **Learning that can be measured and undone** | Sentences reviewers delete in 3+ edits become a new **style-rules version**. It is adopted only if replaying past reviews shows drafts got closer to what humans sent **and** no draft approved unchanged would change. Rollback is one click. |

### Task 3 – Meeting to Scope
| Feature | Why |
|---|---|
| **Five agents, one job each** | Analyst → Feasibility → Estimation → Scope → Proposal & Review. Each hands over a validated pydantic model, so a bad output stops at the stage that made it. |
| **Staged reading of long meetings** | The transcript is cut into overlapping sections and merged. A test proves that many small sections give exactly the same requirements as one big section. |
| **Every requirement keeps its quote** | Each quote is checked word for word against the transcript; a quote that isn't found is rejected. Reviewers can verify dates, prices and promises at the source. |
| **Feasibility through Task 1** | "Existing setting" can save the client money. "Not found" is never treated as proof: the note asks an expert to confirm before custom work is quoted. |
| **Unclear means not estimated** | "Maybe add AI" goes to open questions, never into the price. |
| **Compare with the approved baseline** | A follow-up is compared with the last **approved** version, never a newer draft, so an AI suggestion can't quietly become an agreement. New items are flagged and quoted separately. |
| **Approval enforced in the store, not the screen** | It needs a named reviewer. Nothing unapproved can be sent, and neither can content changed after approval (hash check). Everything is in the audit log. |

### Shared
- **LLM gateway.** Every model call goes through one place: offline/Ollama/Claude modes, per-task budgets, automatic downgrade near a limit, and a **hard monthly cap that defaults to $0**. It logs tokens, cost and latency, never the message text.
- **Guardrails.** Injection detection, PII redaction (including Indian phone formats), citation and link checks, and promise checks are shared by all three tasks.
- **Dashboard.** Tokens and cost over time, budgets and alerts. Hourly roll-ups and a latency histogram keep it fast (30-day summary in about 15 ms).
- **One command.** `python run.py all` from the VS Code terminal starts everything; `test` and `eval` run every suite.

## 4. Features I dropped or postponed, and why

| Dropped | Why |
|---|---|
| **Claude at the centre, with measured model comparisons** | The owner set the build cost at **$0** for this submission. Claude calls are paid, so the Claude path is built (gateway, prompts, schema, guardrails) but blocked by the $0 cap. This is the biggest gap against the brief, and it is stated in EVALS.md. |
| **Screenshot understanding with a vision model** | No free local vision model is available. Screenshots are linked to their section and shown with answers, but their *content* is not searchable yet. |
| **TypeScript for Task 2** | Node.js isn't installed. The brief allows Python ("don't mix"), at the cost of the 10% bonus. |
| **Full crawl of all 1,537 articles** | Started and resumable, but it stopped at 208. At the observed speed (CPU embeddings, about 5 articles a minute), the full read takes about 5 hours. All evals ran on 208 articles. |
| **Reranker** | A cross-encoder is cached locally, but hybrid already reached 100% top-5. Reranking would improve position 1 and is the next experiment. |
| **Real email sending and recorded meetings** | Sending is simulated (an outbox table). The four meetings are role-play scripts, not recordings. |

## 5. Model choices

| Job | Choice | Evidence |
|---|---|---|
| Embeddings | `bge-small-en-v1.5`, local | 96% first-rank / 100% top-5 in hybrid, $0 |
| Tokens | spaCy blank English × 1.3 | Free. The dashboard compares the estimate with real usage so the ratio can be recalibrated. |
| Email category | Rules + Naive Bayes | Leave-one-out on dev: 10/36 → 35/36. Test: 20/20. |
| Answers / drafts / extraction | Extractive answers, templates and rules (offline); Ollama or Claude through the same gateway | Offline: 31/32 (Task 1), 53/56 (Task 2), 30/34 (Task 3) at $0 |
| Paid model default | `claude-opus-5-5`, with a "cost-optimized" preset (Haiku for knowledge and inbox, Sonnet for scope) | **Not measured.** It needs 20–30 paid evals per decision, which are blocked by the $0 cap. |

## 6. What breaks (known failures)

- **Similar words fool the retriever.** "Can I pay with Bitcoin?" is answered from a spam-keyword list that contains "bitcoin" (Task 1, KB-031). A high similarity score is not an answer.
- **Rules only understand phrasings they have seen.** On the held-out meeting D, "We need **that** too" was missed (4/7 on D vs 23/24 on dev). This is the clearest case for a model-based extractor.
- **Urgency words are incomplete.** "We **lost** customers all weekend" is not marked urgent (Task 2, E038).
- **The thresholds are first guesses on small sets:** 0.68 abstain, 0.80 "existing setting". 32 cases means one case moves the score by 3 points.
- **The corpus is only 208 articles.** Scores will shift after the full crawl, so the evals must be re-run.

## 7. What I would build next

1. Finish the full crawl, then re-calibrate both thresholds on a larger labelled set.
2. Turn on the model path (local Ollama first, then Claude with a small cap) and run the same 122 eval cases for a real **quality / cost / speed** comparison of at least two models.
3. Add a reranker and an "is this evidence actually an answer?" check to fix the Bitcoin-type failure.
4. Use a vision model for screenshot descriptions, stored next to each image.
5. Add TypeScript for Task 2 if Node.js becomes available.
