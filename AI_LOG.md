# AI Log

> **DRAFT for the author to review.** The prompts below are copied word for word from my Claude Code conversation for this project, which is kept for the review. Check each one against your own memory of why it mattered, and edit the "why it was useful" lines in your own words before submitting.

**Tool used:** Claude Code (one tool only). The whole project, from the first plan to the last commit, was done in a single conversation.

## My 5 most useful prompts

**1. Turning the brief into one plan, with my own additions**
> "Final list all todo here the porject should be cost optimizable, response speed high and i have found there is to auto test found here and gaurdials also wasnt avaialble and additonaly please spaCy free model for sub tokenization and please build new inbuilt dashboard if we have any api cost control management"

*Why it was useful:* it produced `TODO.md`. The plan covered every brief requirement, plus the needs I added: cost control, guardrails, auto-tests, free spaCy tokenization and a cost dashboard. Everything later was built from it.

**2. Setting the cost rule**
> "hey this is project we are just submittion so this porject build cost should 0"

*Why it was useful:* it changed the architecture. Offline mode became the default, Ollama became an option, and a hard monthly cap that defaults to $0 blocks paid calls *before* they are sent. Every test and eval ran at $0.

**3. Asking for speed through data structures**
> "addiotonly add the metrics to the dashaboard use only dsa method to seepd up the process"

*Why it was useful:* instead of rescanning every call, the dashboard reads hourly roll-ups. Percentiles come from a histogram and the top consumers from a heap, so a 30-day summary takes about 15 ms. It also added cache savings, a month-end projection, time to first token and error rate.

**4. Finishing a task completely, including evaluation**
> "Finish Task 1: escalations endpoint (human expert answers flow back), guardrails, a Streamlit chat screen, and 20+ eval cases with threshold calibration."

*Why it was useful:* one clear "definition of done" gave the expert queue, shared guardrails, the chat screen and 32 eval cases. The threshold was chosen on a dev half and checked on a held-out half (16/16).

**5. Making it runnable the way a reviewer will run it**
> "Pirotize based on the project request and everything should be run via vs coide terminaml make sure of that"

*Why it was useful:* it produced `run.py`, so one command (`python run.py all`) starts everything from the VS Code terminal, and `test` and `eval` run every suite. It also kept the work focused on the brief's mandatory items first.

## One time I rejected the AI's work, and why

**What the AI did:** it created the new project *inside* my other project's folder (`future-forecaster-app/ai-tech-consultant`) and added the new dashboard to that project's preview settings.

**What I said:**
> "no please create an new file and save this as Businees labls projects" … "please dont mix with the future forecaster app"

**Why I rejected it:** the capstone must be one clean repository that a reviewer can clone and run. Mixing it into an unrelated app would mix two git histories and settings files, and could leak files from the other project. The AI moved everything to `Desktop/Business Labs Projects/ai-tech-consultant` and removed its entry from the other project's settings. The first commit was made only after that move.

*(Other corrections: Streamlit instead of the FastAPI + HTML dashboard the AI had planned, and a $0 cap instead of the $5 it suggested.)*
