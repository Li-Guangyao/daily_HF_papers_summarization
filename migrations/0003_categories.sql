-- Research category of each paper (see lib/taxonomy.json). The LLM picks it while writing the English walkthrough;
-- papers without one (older papers, or a walkthrough without a valid tag) are classified from title + abstract.
ALTER TABLE papers ADD COLUMN category_major TEXT;            -- e.g. 'llm'
ALTER TABLE papers ADD COLUMN category_minor TEXT;            -- e.g. 'llm.agents'
ALTER TABLE papers ADD COLUMN category_attempts INTEGER NOT NULL DEFAULT 0;  -- failed classifications
