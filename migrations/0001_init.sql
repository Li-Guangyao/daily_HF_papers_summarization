-- Schema for the HF Daily Papers Digest (see system_design_improved.md §4).

CREATE TABLE daily_lists (
  date        TEXT PRIMARY KEY,            -- 'YYYY-MM-DD', the HF list date (UTC)
  paper_count INTEGER NOT NULL,
  status      TEXT NOT NULL,               -- processing | ready | empty
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  fetched_at  TEXT NOT NULL,
  ready_at    TEXT
);

CREATE TABLE papers (
  id                  TEXT PRIMARY KEY,    -- arXiv id, e.g. '2610.27999'
  daily_date          TEXT NOT NULL,
  title               TEXT NOT NULL,
  authors             TEXT NOT NULL,       -- JSON array of names
  abstract            TEXT,                -- HF's `summary` field, renamed
  published_at        TEXT,
  thumbnail           TEXT,
  project_page        TEXT,
  github_repo         TEXT,
  github_stars        INTEGER,
  upvotes             INTEGER NOT NULL DEFAULT 0,
  num_comments        INTEGER,
  hf_ai_summary       TEXT,
  hf_ai_keywords      TEXT,                -- JSON array
  hf_ai_summary_model TEXT,
  withdrawn_at        TEXT,
  md_misses           INTEGER NOT NULL DEFAULT 0,  -- runs in which .md returned 404
  created_at          TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_papers_daily_date ON papers(daily_date);

-- One row per (provider, model). All usable rows share the work, and each paper is summarized by ONE model.
-- Rows that serve the same underlying model share a `family`; a paper's ZH step must run on the family that wrote its EN.
CREATE TABLE llm_models (
  id                INTEGER PRIMARY KEY,
  family            TEXT NOT NULL,         -- the underlying model, e.g. 'nemotron-3-ultra'
  display_name      TEXT NOT NULL,         -- shown in the UI
  provider          TEXT NOT NULL,         -- 'nvidia' | 'openrouter' | 'zhipu' | ...
  base_url          TEXT NOT NULL,         -- OpenAI-compatible; POST {base_url}/chat/completions
  api_model         TEXT NOT NULL,         -- exact model id for this provider
  api_key_env       TEXT NOT NULL,         -- NAME of the env var holding the key, never the key
  rpm_limit         INTEGER NOT NULL,
  max_concurrency   INTEGER NOT NULL,
  context_tokens    INTEGER NOT NULL,
  max_output_tokens INTEGER NOT NULL,      -- includes reasoning tokens
  request_options   TEXT NOT NULL DEFAULT '{}',  -- JSON merged into the request body
  is_enabled        INTEGER NOT NULL DEFAULT 1,
  UNIQUE (provider, api_model)
);

CREATE TABLE summaries (                   -- exactly one summary per paper
  paper_id        TEXT PRIMARY KEY REFERENCES papers(id),
  family          TEXT,                    -- the model that writes this paper; set by the EN step
  status          TEXT NOT NULL DEFAULT 'pending',  -- pending | ok | error | fatal
  source          TEXT,                    -- full_text | abstract
  prompt_version  INTEGER,
  summary_en      TEXT,
  en_model_id     INTEGER REFERENCES llm_models(id),
  title_zh        TEXT,
  summary_zh      TEXT,
  zh_model_id     INTEGER REFERENCES llm_models(id),
  input_truncated INTEGER NOT NULL DEFAULT 0,
  attempts        INTEGER NOT NULL DEFAULT 0,  -- paper-specific failures only; outages don't count
  last_error      TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE job_errors (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id      TEXT,
  job         TEXT NOT NULL,               -- healthcheck | list | markdown | summarize | translate
  target      TEXT NOT NULL,               -- date, or paper id
  model_id    INTEGER,
  http_status INTEGER,
  error_code  TEXT,
  message     TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Only one pipeline process may use the API keys at a time.
CREATE TABLE run_locks (
  name       TEXT PRIMARY KEY,             -- 'pipeline'
  holder     TEXT NOT NULL,                -- 'actions:<run_id>' or 'local:<hostname>:<pid>'
  waiting    TEXT,                         -- a scheduled run asking the holder to step aside
  expires_at TEXT NOT NULL
);

INSERT INTO llm_models
  (family, display_name, provider, base_url, api_model, api_key_env,
   rpm_limit, max_concurrency, context_tokens, max_output_tokens, request_options)
VALUES
  ('nemotron-3-ultra', 'Nemotron 3 Ultra', 'nvidia', 'https://integrate.api.nvidia.com/v1',
   'nvidia/nemotron-3-ultra-550b-a55b', 'NVIDIA_API_KEY', 40, 35, 1000000, 16000, '{"temperature":0.3}'),
  ('nemotron-3-ultra', 'Nemotron 3 Ultra', 'openrouter', 'https://openrouter.ai/api/v1',
   'nvidia/nemotron-3-ultra-550b-a55b:free', 'OPENROUTER_API_KEY', 20, 15, 262144, 16000, '{"temperature":0.3}'),
  ('glm-4.7-flash', 'GLM-4.7-Flash', 'zhipu', 'https://open.bigmodel.cn/api/paas/v4',
   'glm-4.7-flash', 'ZHIPU_API_KEY', 20, 1, 200000, 12000, '{"temperature":0.3}');
