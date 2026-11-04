# HF Papers Digest

Detailed English and Chinese walkthroughs of the [Hugging Face daily papers](https://huggingface.co/papers).

- **GitHub Actions** runs the pipeline Tuesday–Saturday. It fetches the previous workday's papers, has an LLM write a long English walkthrough and a Chinese version of each paper, and assigns each paper a research category. Everything is stored in Cloudflare D1.
- **Cloudflare Pages** serves the website, plus a small read-only API (Pages Functions) that reads D1.

```
pipeline/            Python pipeline (shared by GitHub Actions and the local backfill)
  main.py            scheduled entry point (python -m pipeline.main)
  backfill.py        local CLI for historical dates (python -m pipeline.backfill)
  core.py            one run: health check → fetch lists → plan → worker pools → readiness
  pool.py            worker pools, rate limiter, failover, one model per paper
  llm.py, hf.py      provider client (OpenAI-compatible streaming) and Hugging Face client
  store.py, d1.py    all SQL; D1 REST client and a local SQLite stand-in
  prompts.py         the beginner-level English prompt, the Chinese prompt and the category prompt (bump PROMPT_VERSION when you edit them)
  taxonomy.py        loads lib/taxonomy.json; parses the <category> tag
  classify.py        end-of-run pass: categorizes papers that have none, from title + abstract
  tests/             unit tests + end-to-end tests with fake providers
migrations/          D1 schema, default llm_models rows, translate_options, paper categories (applied by deploy-web.yml, or npm run db:migrate:*)
functions/api/       Pages Functions: /api/dates, /api/papers, /api/papers/:id
lib/api.ts           helpers for the functions (edge cache, JSON)
lib/taxonomy.json    the paper categories (top level + sub-categories), shared by the pipeline and the website
web/                 React + Tailwind frontend (Vite); web/src/markdown.ts renders Markdown + LaTeX (KaTeX)
.github/workflows/   pipeline.yml (schedule), deploy-web.yml, tests.yml
scripts/             probe_concurrency.py (measure a provider's limits)
```

---

## 1. Set up on your machine

You need Python 3.12+, Node 20+ and a Cloudflare account.

```bash
python3 -m venv .venv
.venv/bin/pip install -r pipeline/requirements-dev.txt
npm install
cp .env.example .env        # then fill in the keys you have
```

---

## 2. Test locally

### 2.1 Automated tests (no keys, no network)

```bash
.venv/bin/python -m pytest pipeline/tests -q
```

The end-to-end tests run the real pipeline against a fake Hugging Face, fake LLM providers and an in-memory D1. They cover:

- failover from a failing provider to a healthy one;
- English and Chinese always written by the same model;
- "all providers down" changing nothing;
- a retired model's half-done papers being redone by another model;
- paper errors becoming `fatal`;
- the run lock.

Frontend tests (math rendering, sanitizing, table of contents) and type checks:

```bash
npm test && npm run typecheck && npm run build
```

### 2.2 Try real summaries without any database (dry run)

```bash
.venv/bin/python -m pipeline.backfill --date 2026-10-21 --limit 3 --dry-run
```

This uses the provider keys in `.env` and a throwaway in-memory database. The summaries are written to `backfill_out/2026-10-21/<paper>.en.md` and `.zh.md`. Use it to judge prompt quality before anything goes live.

Add `--family glm-4.7-flash` to try only one model.

### 2.3 The whole system locally (local D1 + pipeline + website)

```bash
npm run db:migrate:local                       # creates a local D1 under .wrangler/
LOCAL_D1_PATH=auto .venv/bin/python -m pipeline.backfill --date 2026-10-21 --limit 3
```

With `--limit 3` that date stays `processing`. The website still shows it: a date appears as soon as at least one of its papers is done, with the finished papers listed and a note saying how many are still being analyzed (the date button shows e.g. `42/45`). A date with no finished papers only shows a progress banner.

Then start the site in two terminals:

```bash
npm run dev:api      # terminal 1: Pages Functions + local D1 on http://localhost:8788
npm run dev          # terminal 2: the website with hot reload on http://localhost:5173 (proxies /api)
```

Or run one terminal with `npm run preview`, which builds the site and serves everything on <http://localhost:8788>.

Check the API directly:

```bash
curl -s localhost:8788/api/dates
curl -s "localhost:8788/api/papers?date=2026-10-21" | head -c 600
```

To check what a local or remote database holds, without changing anything:

```bash
LOCAL_D1_PATH=auto .venv/bin/python -m pipeline.backfill --from 2026-10-19 --to 2026-10-23 --status
```

---

## 3. Deploy to Cloudflare (database + website)

All commands run from the repository root.

1. **Log in:** `npx wrangler login`, then `npx wrangler whoami` to see your **Account ID**.
2. **Create the database:**
   ```bash
   npx wrangler d1 create hf-papers
   ```
   Copy the printed `database_id` into `wrangler.toml`, replacing `00000000-…`.
3. **Create the tables and the default models:**
   ```bash
   npm run db:migrate:remote
   ```
4. **Create the Pages project and deploy the site:**
   ```bash
   npx wrangler pages project create hf-papers-digest --production-branch main
   npm run deploy
   ```
   Wrangler prints the URL, `https://hf-papers-digest.pages.dev`. The D1 binding comes from `wrangler.toml`. The site shows "No papers yet" until the pipeline has run.
5. **Recommended: custom domain and rate limit.**
   - In the dashboard, open Workers & Pages → hf-papers-digest → Custom domains, and add a domain you manage on Cloudflare.
   - Then, under that domain, open Security → WAF → Rate limiting rules, and add a rule for URI path starting with `/api/`. This protects D1 if someone floods the API.

---

## 4. Deploy to GitHub (the daily pipeline)

1. **Create two Cloudflare API tokens.** Use My Profile → API Tokens → Create Token → Custom token:
   - **`CF_API_TOKEN`** (for the pipeline): permission *Account → D1 → Edit*, limited to your account.
   - **`CF_PAGES_TOKEN`** (for `deploy-web.yml`, optional): *Account → Cloudflare Pages → Edit*, plus *Account → D1 → Edit* so the deploy can attach the D1 binding.
2. **Add the repository secrets.** Go to GitHub → your repo → Settings → Secrets and variables → Actions → *New repository secret*:

   | Secret | Value |
   |---|---|
   | `NVIDIA_API_KEY` | NVIDIA key |
   | `OPENROUTER_API_KEY` | OpenRouter key |
   | `ZHIPU_API_KEY` | Zhipu key |
   | `CF_API_TOKEN` | Token from step 1 |
   | `CF_ACCOUNT_ID` | From `npx wrangler whoami` |
   | `D1_DATABASE_ID` | From `wrangler d1 create` |
   | `CF_PAGES_TOKEN` | For `deploy-web.yml`: deploys the site and applies new D1 migrations |

   Optionally, under the *Variables* tab, set `TIME_BUDGET_MINUTES` (default 45).
3. **Merge into `main`.** Scheduled workflows only run from the default branch (`main`), so the workflow files and the `database_id` in `wrangler.toml` must be on `main`.
4. **Do a first manual run.** Go to Actions → *pipeline* → *Run workflow*. Enter a recent workday such as `2026-10-21` in **dates**, or leave it empty for the previous 2 workdays.
   - The run summary shows each provider's state and each date's counts.
   - Finished papers show up on the website right away (within about 5 minutes, because of the edge cache); the date is complete once it shows `ready`.
5. **From then on,** the pipeline runs Tuesday–Saturday at 02:17 UTC (10:17 Beijing), with catch-up runs at 08:17 and 14:17 UTC. Each run covers the previous two workdays, so Friday's papers are processed on Saturday morning.
   - A run that finds nothing to do takes about 1 billed minute.
   - If every provider is down, the run fails and GitHub emails you; the papers stay hidden until a later run succeeds.

> In a **public** repository, GitHub disables scheduled workflows after 60 days without repository activity, and emails you first. Push any commit, or re-enable the workflow under Actions, to keep it running.

---

## 5. Day-to-day operations

**Backfill history** from your machine. This uses no GitHub minutes. Put the remote D1 credentials in `.env`, then:

```bash
.venv/bin/python -m pipeline.backfill --from 2026-09-01 --to 2026-09-30
```

- It processes dates D1 doesn't have and continues unfinished ones. Ctrl+C stops cleanly; run the same command again to resume.
- If a scheduled run starts meanwhile, the backfill pauses and resumes after it.
- Other options:
  - `--status`: report only
  - `--redo`: regenerate the summaries
  - `--refresh-list`: fetch the lists again
  - `--include-weekends`: include Saturday and Sunday
  - `--family F`: use only one model
  - `--dry-run`: write to files, not D1

**Manage models** in D1. Each run uses only rows that are enabled and whose key is set:

```bash
# list
npx wrangler d1 execute hf-papers --remote --command "SELECT id, family, provider, api_model, rpm_limit, max_concurrency, is_enabled FROM llm_models"
# retire a model that expired or stopped being free (its half-done papers are redone by another model)
npx wrangler d1 execute hf-papers --remote --command "UPDATE llm_models SET is_enabled = 0 WHERE id = 2"
# change a pool size, or turn off GLM's thinking for speed
npx wrangler d1 execute hf-papers --remote --command "UPDATE llm_models SET max_concurrency = 25 WHERE id = 1"
npx wrangler d1 execute hf-papers --remote --command "UPDATE llm_models SET request_options = '{\"temperature\":0.3,\"thinking\":{\"type\":\"disabled\"}}' WHERE id = 3"
```

`translate_options` holds request options for the Chinese step only. By default it turns reasoning off for GLM and NVIDIA, since translating doesn't need it.

To add a provider, insert a row (same `family` = same model), add its key as a GitHub secret, and add one line to the `env:` block in `.github/workflows/pipeline.yml`.

**Look at errors:**

```bash
npx wrangler d1 execute hf-papers --remote --command "SELECT created_at, job, target, model_id, http_status, error_code, message FROM job_errors ORDER BY id DESC LIMIT 20"
```

**Categories.** Each paper has a top-level category (`papers.category_major`, e.g. `llm`) and a sub-category (`papers.category_minor`, e.g. `llm.agents`). They come from `lib/taxonomy.json`.

- New papers get theirs from the English-walkthrough request: the model writes a `<category>…</category>` tag before the walkthrough.
- At the end of every run, papers with no category are classified from title + abstract. This covers papers stored before categories existed, walkthroughs without a valid tag, and withdrawn or failed papers. A paper is tried at most 3 times; failures are in `job_errors` with `job = 'classify'`.
- On the website, the list page shows the day's categories as boxes with paper counts. Click a sub-category, or a top-level title, to filter; the filter is kept in the URL (`?cat=llm.agents`).
- To change the categories, edit `lib/taxonomy.json`. Keep the ids stable: renaming an id leaves papers with an unknown id, which the website shows as uncategorized. To re-classify everything after a big change:
  ```bash
  npx wrangler d1 execute hf-papers --remote --command "UPDATE papers SET category_major = NULL, category_minor = NULL, category_attempts = 0"
  ```
  The next run classifies them again from title + abstract.
- Count papers per category:
  ```bash
  npx wrangler d1 execute hf-papers --remote --command "SELECT category_minor, COUNT(*) AS n FROM papers GROUP BY 1 ORDER BY n DESC"
  ```

**Change a prompt:**

1. Edit `pipeline/prompts.py` and increase `PROMPT_VERSION`.
2. Check the result with `--dry-run`.
3. Regenerate the dates you want with `--redo`.

**Measure a provider:** use `scripts/probe_concurrency.py` (currently written for Zhipu).

---

## 6. Troubleshooting

| Symptom | Check |
|---|---|
| Site says "No papers have been published yet" | `--status` for recent dates. A date appears once at least one of its papers is done. |
| A date shows "N more papers are still being analyzed" | Normal for a few hours: a paper whose full text HF hasn't published yet waits up to 10 hours from the first miss (`MD_WAIT_HOURS` in `pipeline/pool.py`), then falls back to the abstract. Later runs finish them. |
| `paper error translate … missing <summary_zh>` | The model didn't follow the output format. The run log and `job_errors.message` show the start and end of what it returned. A paper is retried at most 3 times (30 s apart), then becomes `fatal` so its date can still become ready. |
| OpenRouter shows `quota: … free-models-per-day` | Without credits OpenRouter allows only 50 free-model requests a day (each paper needs at least 2). With 10 USD of credits it is 1000 a day. |
| A date is stuck in `processing` | `job_errors`. Usually a provider is down or out of quota. The next run continues automatically. |
| Run summary says a model looks retired | Disable that row in `llm_models`. |
| `D1 query failed` with HTTP 401/403 | `CF_API_TOKEN` needs the *D1 Edit* permission on the right account. |
| Workflow never runs on schedule | The workflow file must be on `main`. In public repos, re-enable it after 60 days of inactivity. |
