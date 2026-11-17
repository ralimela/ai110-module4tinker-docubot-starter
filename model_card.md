# DocuBot Model Card

This model card is a short reflection on your DocuBot system. Fill it out after you have implemented retrieval and experimented with all three modes:

1. Naive LLM over full docs  
2. Retrieval only  
3. RAG (retrieval plus LLM)

Use clear, honest descriptions. It is fine if your system is imperfect.

---

## 1. System Overview

**What is DocuBot trying to do?**  
Describe the overall goal in 2 to 3 sentences.

> DocuBot answers developer questions about a codebase (for example, "Where is the auth token generated?") using the project's own documentation instead of general knowledge. The goal is to give answers that are grounded in the docs, and to say "I do not know" when the docs don't cover the question rather than guessing.

**What inputs does DocuBot take?**  
For example: user question, docs in folder, environment variables.

> - A developer question typed by the user
> - The Markdown files in the `docs/` folder (`API_REFERENCE.md`, `AUTH.md`, `DATABASE.md`, `SETUP.md`)
> - The `GEMINI_API_KEY` environment variable (from `.env`), needed only for modes 1 and 3
> - The chosen mode (1, 2 or 3)

**What outputs does DocuBot produce?**

> - Mode 1: a free-form answer written by Gemini
> - Mode 2: the raw text of the top 3 matching doc sections, each labeled with its filename
> - Mode 3: a short answer written by Gemini from the retrieved sections, naming the files it used
> - In modes 2 and 3: a refusal ("I do not know based on these docs.") when no section is strong enough evidence

---

## 2. Retrieval Design

**How does your retrieval system work?**  
Describe your choices for indexing and scoring.

- How do you turn documents into an index?
- How do you score relevance for a query?
- How do you choose top snippets?

> **Chunking:** Each file is split at Markdown headings (`#`, `##`, `###`), so every chunk is one heading plus the text under it. The docs are already written one topic per heading, so this keeps related sentences together (for example, the whole "Token Generation" section). A `###` subsection also gets its parent `##` heading copied on top so it keeps its context (for example, `## Tables` › `### users`). The 4 docs become 40 chunks.
>
> **Tokenizing:** Text is lowercased and split on anything that isn't a letter or digit, so punctuation is dropped and `auth_utils` becomes `auth` + `utils`. Each word then has one common ending stripped (`ing`, `ion`, `ed`, `es`, `e`, `s`), so `generated`, `generation` and `generate` all match. Common words like "the", "how" and "where" (stopwords) are ignored in questions.
>
> **Index:** An inverted index maps each word to the list of chunks that contain it, for example `"token" → [3, 4, 9, ...]`. It's used to quickly find candidate chunks that share at least one word with the question.
>
> **Scoring:** For each candidate chunk I compute (1) how many *different* question words it contains and (2) the total number of times question words appear in it.
>
> **Choosing top snippets:** Chunks are sorted by (1) first, then (2) to break ties, and the top 3 are returned. Sorting by different words first stops a chunk that repeats one word many times from beating a chunk that matches the whole question.

**What tradeoffs did you make?**  
For example: speed vs precision, simplicity vs accuracy.

> - **Simplicity over accuracy:** Word matching is easy to explain and debug, but it matches words, not meaning. "log in" does not match "login", and it can't connect real synonyms like "sign in" and "authenticate".
> - **Crude normalization:** Stripping endings fixed many misses, but it's rough (`mention` becomes `ment`) and only helps with word forms, not synonyms.
> - **Heading-based chunks depend on the docs:** This works because these docs have clear headings. Docs with long sections or no headings would need a different chunking rule.
> - **Guardrail threshold (50%):** Higher means more false refusals; lower lets coincidental matches through. I tuned it on only about 10 test questions.
> - **Fixed top 3:** Simple, but it sometimes includes loosely related extra sections, and if the right section ranks 4th it is lost entirely.

---

## 3. Use of the LLM (Gemini)

**When does DocuBot call the LLM and when does it not?**  
Briefly describe how each mode behaves.

- Naive LLM mode:
- Retrieval only mode:
- RAG mode:

> - **Naive LLM mode:** Always calls Gemini. Despite the menu label "over full docs", the code only sends the question, not the docs, so Gemini answers from general knowledge.
> - **Retrieval only mode:** Never calls Gemini. It prints the top retrieved sections as-is.
> - **RAG mode:** Runs retrieval first. If no section passes the evidence check, it refuses right away and **does not call Gemini**. Otherwise it sends the question plus the top 3 sections to Gemini and returns Gemini's answer.

**What instructions do you give the LLM to keep it grounded?**  
Summarize the rules from your prompt. For example: only use snippets, say "I do not know" when needed, cite files.

> - Answer using only the information in the provided snippets
> - Do not invent functions, endpoints or configuration values
> - If the snippets aren't enough to answer confidently, reply exactly "I do not know based on the docs I have."
> - When answering, mention which files were used
>
> Each snippet is labeled with its filename so the model can cite it.

---

## 4. Experiments and Comparisons

Run the **same set of queries** in all three modes. Fill in the table with short notes.

You can reuse or adapt the queries from `dataset.py`.

| Query | Naive LLM: helpful or harmful? | Retrieval only: helpful or harmful? | RAG: helpful or harmful? | Notes |
|------|---------------------------------|--------------------------------------|---------------------------|-------|
| Where is the auth token generated? | Unhelpful: asked which codebase I meant | Helpful but long: correct "Token Generation" section plus 2 extra sections (~35 lines) | Helpful: `generate_access_token` in `auth_utils.py`, cites `AUTH.md` | Naive had no evidence, so it couldn't answer |
| How do I connect to the database? | **Harmful:** invented `DB_HOST`/`DB_PORT`/`DB_USER` variables and JavaScript code | Helpful: "Connection Configuration" section | Helpful: `DATABASE_URL`, SQLite default `app.db`, Postgres option, `db.py`; cites `DATABASE.md` | Naive sounded confident, but every specific detail was wrong |
| Which endpoint lists all users? | **Harmful:** `GET /users` "based on REST conventions" (wrong path, no admin note) | Helpful: `GET /api/users` section | Helpful: `GET /api/users`, cites `API_REFERENCE.md` | Naive was close enough to trust but would fail. RAG left out "admin only" |
| How does a client refresh an access token? | **Harmful:** long OAuth answer (`/oauth/token`, `grant_type=refresh_token`) that doesn't apply to this app | Helpful: Client Workflow + `POST /api/refresh` sections | Helpful: call `/api/refresh` with the existing token as a Bearer header; combined and cited 2 files | Best example of naive looking impressive but being wrong |
| Is there any mention of payment processing in these docs? | Unhelpful: "you forgot to include the documentation" | Correct refusal | Correct refusal (Gemini never called) | Retrieval guardrail worked |
| What does the /api/projects/<project_id> route return? | Harmful: invented fields like `created_at` and team members | Partly: wrong section ranked 1st, right one 3rd | Helpful: correct fields (`project_id`, `name`, `description`, `status`, `owner`) | RAG made up for weak ranking, since the right section was still in the top 3 |
| How do I log in? | **Harmful:** invented a web UI ("Sign In" button, SSO, Forgot Password) | **Harmful:** returned only "Common Failure Cases" | **Failed:** "I do not know", even though `POST /api/login` is documented | Retrieval failure: "log" doesn't match "login" |

**What patterns did you notice?**  

- When does naive LLM look impressive but untrustworthy?  
- When is retrieval only clearly better?  
- When is RAG clearly better than both?

> - **Naive LLM looks impressive but is untrustworthy** when the question is about common topics (databases, OAuth tokens, login). It fills in "typical" industry answers with confident formatting and code samples, but the details (variable names, endpoints, language) don't match this project. When it couldn't find a common pattern, it asked for the codebase instead. Whether it guessed or refused was unpredictable.
> - **Retrieval only is clearly better** whenever accuracy matters more than convenience. Everything it shows comes word for word from the docs, so it can't hallucinate. The downside is that it doesn't answer the question; you have to read the sections and find the answer yourself.
> - **RAG is clearly better than both** when retrieval finds the right section. It gave short, correct answers, cited files, and even combined facts from two files (refresh-token question). It also handled a weak ranking (the projects route) by picking the right section out of the top 3.
> - **RAG is only as good as retrieval.** When retrieval missed ("How do I log in?"), RAG could not recover. Gemini correctly refused instead of guessing, but the user still got no answer.

---

## 5. Failure Cases and Guardrails

**Describe at least two concrete failure cases you observed.**  
For each one, say:

- What was the question?  
- What did the system do?  
- What should have happened instead?

> **Failure case 1: "How do I log in?" (RAG and retrieval)**  
> - *What happened:* Retrieval returned only AUTH.md › "Common Failure Cases", which matched only because of the phrase "log in again". RAG then said "I do not know based on the docs I have."  
> - *What should have happened:* It should have answered "send username and password to `POST /api/login` to receive an access token", which is documented in `API_REFERENCE.md` and in AUTH.md › Client Workflow.  
> - *Why:* Word matching treats "log" and "login" as different words, so the right sections never reached Gemini.

> **Failure case 2: "How do I connect to the database?" (naive mode)**  
> - *What happened:* Gemini confidently listed five `DB_*` environment variables and JavaScript connection code (`require('./config/database')`, `db.authenticate()`).  
> - *What should have happened:* "Set the single `DATABASE_URL` variable (SQLite `sqlite:///app.db` by default, or a `postgres://` URL); connections go through `db.py`."  
> - *Why:* Naive mode gives the model no evidence, so it filled the gap with a generic answer.

> **Failure case 3 (smaller): "What does /api/projects/<project_id> return?" (retrieval ranking)**  
> - *What happened:* Retrieval ranked the neighboring `GET /api/projects` section above the correct `GET /api/projects/<project_id>` section.  
> - *What should have happened:* The exact matching endpoint should rank first. RAG still answered correctly because the right section was 3rd, but with `top_k=2` it would have been missed.

**When should DocuBot say “I do not know based on the docs I have”?**  
Give at least two specific situations.

> 1. When the topic isn't in the docs at all, for example "Is there any mention of payment processing?" or "How do I bake a cake?"
> 2. When retrieval finds sections that share words with the question but don't actually answer it. Here Gemini should refuse rather than stretch loosely related text into an answer.
> 3. When the question asks for details the docs don't specify, for example the exact signing algorithm for tokens or rate limits. The docs mention JWTs but not these details, so the bot should not fill them in from general knowledge.

**What guardrails did you implement?**  
Examples: refusal rules, thresholds, limits on snippets, safe defaults.

> - **Evidence threshold (before the LLM):** A section only counts as evidence if it contains at least 50% of the question's meaningful words (`MIN_QUERY_COVERAGE = 0.5` in `docubot.py`). If no section passes, DocuBot replies "I do not know based on these docs." and Gemini is never called, so it has no chance to make something up.
> - **Prompt rules (inside the LLM):** Gemini is told to use only the snippets, not invent functions, endpoints or config, refuse with an exact phrase, and cite files. This caught the "log in" case, where the snippet was off-topic.
> - **Snippet limit:** Only the top 3 sections are sent, which keeps the context small and focused.
> - **Safe default:** An empty or stopword-only question has no evidence, so it is refused.
>
> The two refusal messages are worded differently ("these docs" vs. "the docs I have"), which makes it easy to tell which layer refused.

---

## 6. Limitations and Future Improvements

**Current limitations**  
List at least three limitations of your DocuBot system.

1. **Matches words, not meaning.** It can't connect "log in" with "login" or "sign in" with "authenticate", which causes missed answers and false refusals.
2. **Crude normalization and a hand-tuned threshold.** Suffix stripping sometimes mangles words (`mention` becomes `ment`), and the 50% threshold was tuned on only about 10 questions, so it may not generalize.
3. **The guardrail checks wording, not answers.** A section can contain the right words without answering the question. Only Gemini's prompt rules catch that, and that relies on the model following instructions.
4. **Fixed top 3 and heading-based chunks.** Ranking errors can push the right section out of the top 3, and the chunking depends on the docs having clean headings.
5. **No evaluation of answer quality.** `evaluation.py` only checks whether the right *file* was retrieved, not whether the right *section* was found or whether RAG's answer was correct. RAG also sometimes leaves out details, such as "admin only" for `GET /api/users`.

**Future improvements**  
List two or three changes that would most improve reliability or usefulness.

1. **Semantic search with embeddings** (or at least a synonym list), so questions match by meaning and "log in" finds the `/api/login` section.
2. **Better ranking and evaluation:** weight rare words more than common ones (TF-IDF or BM25), give extra weight to matches in headings, and extend the evaluation to check the right section and the correctness of the final answer.
3. **Show sources with every RAG answer:** print the exact sections used under each answer so users can verify it, and ask Gemini to include important conditions (permissions, errors) from those sections.

---

## 7. Responsible Use

**Where could this system cause real world harm if used carelessly?**  
Think about wrong answers, missing information, or over trusting the LLM.

> - **Confident wrong answers (naive mode):** A developer could follow invented instructions, like setting `DB_*` variables that the app ignores or calling an `/oauth/token` endpoint that doesn't exist, and waste time debugging something the docs never said.
> - **Missing important conditions:** Even a grounded RAG answer can leave out details like "admin only" or failure cases, which can cause permission or security mistakes.
> - **False refusals:** "I do not know" can make someone think a feature isn't documented when it is (the login case), so they might build their own version or guess.
> - **Outdated docs:** DocuBot is only as accurate as the docs. If the docs are wrong or stale, it repeats the mistakes with a source attached, which makes them more convincing.
> - **Security-sensitive topics:** Answers about auth secrets and tokens should be checked carefully; a small mistake there can create a vulnerability.

**What instructions would you give real developers who want to use DocuBot safely?**  
Write 2 to 4 short bullet points.

- Use RAG or retrieval-only mode for project questions. Don't trust naive mode for anything project-specific.
- Check the cited file before acting on an answer, especially for auth, permissions and configuration.
- Treat "I do not know" as "I couldn't find it", not "it doesn't exist". Try rewording the question or search the docs directly.
- Keep the docs up to date. DocuBot can't be more accurate than the documentation it reads.

---
