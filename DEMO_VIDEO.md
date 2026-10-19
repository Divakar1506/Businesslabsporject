# Demo video script (10 minutes max)

The brief asks for: **your face for the first 30 seconds, then all three tasks working together, including one case where it fails.**
Words in *italics* are what you say. Everything else is what you click or show. Times are targets; the total is **9:30**, which leaves 30 seconds of slack.

---

## Before you record (10 minutes, not on camera)

1. Open VS Code on the `ai-tech-consultant` folder → **Terminal → New Terminal**.
2. Run `python run.py test`. It should end with **ALL TESTS PASSED**. Keep the terminal visible; you'll show it at the end.
3. Run `python run.py all` and wait for the five links to come up.
4. Open these browser tabs, in this order: **8501** (chat), **8502** (inbox), **8503** (scope), **8600** (dashboard), **8100/docs** (API).
5. Reset the sample data so it loads live on camera. Close everything (Ctrl+C), then delete `task2-inbox-agent/data/inbox.db` and `task3-meeting-to-scope/data/scope.db`, and run `python run.py all` again.
6. Type your name in the sidebar name boxes of the chat (8501) and scope (8503) screens.
7. Have two small files ready on your Desktop for the upload: a short Word file (e.g. "Holiday support hours": *"On public holidays support replies within 24 hours."*) and a screenshot (PNG).
8. Screen recorder: 1080p, microphone on, webcam on for the first 30 seconds.

---

## 0:00 – 0:30 · You, on camera
*"Hi, I'm Divakar. Software that cost $2,000 two years ago now sells for about $200, so a software company has to do ten times more work to stay alive. Writing code is no longer the slow part. Knowing the product, answering every email, and turning meetings into quotes is. I built an AI Tech Consultant that does those three jobs from one shared Knowledge Brain, and it runs for $0. Let me show you."*

→ Switch to screen recording only.

## 0:30 – 1:00 · The big picture
Show `DESIGN.md` → the diagram.
*"Three tasks. Task 1, the Knowledge Brain, read all 1,539 articles of the Brilliant Directories help center, and the text inside their screenshots. Task 2, the Inbox Agent, and Task 3, Meeting to Scope, never copy its code. They only ask it questions over its API. So one update to the knowledge reaches everything."*

## 1:00 – 3:00 · Task 1: Knowledge Brain (tab 8501)
1. **Ask:** type *"How do I clone a post type?"*
   *"It answers from the help center, shows which articles it used, and shows the screenshots from that section."* Point at the source links and screenshots.
2. **The honest "I don't know":** type *"What will the weather be in Paris tomorrow?"*
   *"It doesn't guess. It says it doesn't know and creates a task for a human expert."* Point at "Sent to the expert queue as #…".
3. **Expert queue** tab: open that question and answer *"We only answer questions about our product."*, scope **general**, then **Save**.
   *"The expert's answer goes straight into memory."*
4. **Add knowledge** tab: choose the Word file. Its title and article text fill automatically; no form or submit step is needed.
   *"Companies have knowledge that isn't on their website. Upload Word, PDF, text or images with readable text, and each file becomes a draft article. Nothing goes live until a person approves it."*
5. **Review new knowledge** tab: change "24 hours" to "12 hours", click **Save edits (keep pending)**, then **Approve**. Point out **Decline** too.
   *"The reviewer only needs to approve, decline or edit. Saving a correction still leaves the article waiting for approval."*
6. **Ask:** *"How fast does support reply on public holidays?"* It answers "12 hours" and shows a 📎 uploaded-knowledge source.
7. Show the sidebar's **Crawl** line, then tab **8100/docs**.
   *"It re-checks the sitemap every night and only re-reads pages that changed. Removed pages disappear from answers, and a crawl never overwrites a human correction. Here's the API the other two tasks use."*

## 3:00 – 5:00 · Task 2: Inbox Agent (tab 8502)
1. Sidebar → **Load the 56 sample emails** (about 10 seconds).
   *"56 realistic customer emails I wrote, across 7 types: how-to, troubleshooting, refunds, complaints, quotes, projects and follow-ups."*
2. Open **E001 "Blocking a spammer"**. Show category, urgency, risk, the evidence links and the draft.
   *"It understood the email, asked the Knowledge Brain for the facts, and drafted a reply in our voice, with the help article linked."*
3. Click **Send** without changing anything. *"That's one unchanged send."*
4. Open **E043 "Ignore your rules"** (the prompt-injection refund email).
   *"This customer tries to trick the AI into promising a refund. It's flagged, the draft promises nothing, and refunds always need a human."* Point at the 🛡️ flag and "refund_cancel always needs a human".
5. Open a how-to draft, **delete a sentence**, write a reason ("too long"), then **Send**.
   *"Every edit is recorded."*
6. **Autonomy** tab: *"After 20 unchanged sends in a row, a category may send on its own, but only if an admin switches it on. One human edit switches it off again, and refunds, complaints and quotes never go out alone."*
7. **Learning** tab: *"Sentences people keep deleting become a new version of the writing rules. It's only adopted if replaying past emails shows the drafts got better, and it can be rolled back in one click."*

## 5:00 – 7:30 · Task 3: Meeting to Scope (tab 8503)
1. Sidebar → **Load sample meetings A and B**.
   *"These are role-played client meetings. Meeting A: a client wants better search, filters and mobile."*
2. **Requirements** tab: point at **confirmed** vs **unclear**, then expand one requirement to show its **quote from the transcript**.
   *"Five agents with one job each. The analyst pulls out every requirement with the client's exact words, so nothing is invented. 'Maybe add AI' stays an open question and is never priced."*
3. In the same table, the **product check** column: *"Before quoting custom work, the feasibility agent asks the Knowledge Brain whether the product already does it. Sorting and autosuggest are existing settings, which is much cheaper for the client."*
4. **Estimate** tab: low, likely and high hours, plus PERT. **Proposal** tab: scroll quickly.
5. **Approval & history** → **Approve v1**.
6. Sidebar → **New meeting**: project *HomeFixers search*, follow-up of **meeting_a**, sample **meeting_c_followup.txt**, then **Run the agents**.
7. **Changes vs baseline** tab:
   *"A week later the client changed things. Compared with the approved version: AI search removed, the price filter and mobile changed, three new items flagged as outside the agreed scope with their own quote, and the deadline moved from March 1st to April 15th."*
8. **Approval & history** → click **Send** on the new, **unapproved** version.
   *"And nothing reaches the client until a person approves it."* Show that the button is disabled, or the error.

## 7:30 – 8:45 · Evidence and one honest failure
1. Open `EVALS.md`:
   *"I measured everything, at $0. On the full help center the brain passes 30 of 32 test questions, the inbox 50 of 56 emails with zero unauthorised promises, and meeting-to-scope 30 of 34 checks."*
2. **The failure:** in the chat (8501), ask *"Can I pay my invoice with Bitcoin?"*
   *"Here it fails. There's no Bitcoin payment option, but a spam-keyword list in one article contains the word 'bitcoin', so it answers instead of saying it doesn't know. Similar words aren't an answer. My next fix is a reranker that checks whether the evidence really answers the question."*
3. Optional second failure, if time allows: *"On a meeting I held back for testing, it missed 'we need **that** too', because the rules only understand phrasings they've seen. That's my strongest case for adding the model-based extractor."*
4. Show tab **8600**, the cost dashboard: *"Every model call goes through one gateway with budgets and a hard monthly cap, set to $0 for this build."*

## 8:45 – 9:30 · What works, what doesn't, what's next
Show the terminal with **ALL TESTS PASSED**.
*"What works: one shared brain that the other two tasks use only through its API, honest answers with sources, human approval wherever money or commitments are involved, and 98 automated tests.
What doesn't yet: I didn't run a paid Claude comparison, because I kept the build at zero cost. The extraction rules miss phrasings they haven't seen, and similar words can fool the search.
Next: a reranker, Claude or a local model for writing and extraction, compared on these same 122 test cases. Thanks for watching."*

---

## Checklist against the brief
- [ ] Face visible for the first 30 seconds
- [ ] Task 1, Task 2 and Task 3 each shown working
- [ ] Shown working **together**: the inbox and scope answers come from the Knowledge Brain API
- [ ] At least one failure shown and explained (the Bitcoin question)
- [ ] Under 10 minutes
