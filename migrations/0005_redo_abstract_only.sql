-- arXiv HTML is now a second full-text source (after Hugging Face's markdown). Give every walkthrough that was
-- written from the abstract only another chance: the next run fetches the full text again (HF, then arXiv HTML)
-- and rewrites it, or writes it from the abstract again if neither exists.
UPDATE summaries SET summary_en = NULL, summary_zh = NULL, title_zh = NULL, family = NULL, en_model_id = NULL,
    zh_model_id = NULL, source = NULL, status = 'pending', attempts = 0, last_error = NULL, updated_at = datetime('now')
WHERE source = 'abstract';

-- Translations that failed only because they had more sections than the English (now accepted): translate again.
UPDATE summaries SET status = 'pending', attempts = 0, last_error = NULL, updated_at = datetime('now')
WHERE status = 'fatal' AND summary_en IS NOT NULL AND summary_zh IS NULL AND last_error LIKE '%level-2 headings, expected%';

-- Their dates are not finished any more.
UPDATE daily_lists SET status = 'processing', ready_at = NULL
WHERE status = 'ready' AND date IN (SELECT p.daily_date FROM papers p JOIN summaries s ON s.paper_id = p.id
                                    WHERE s.status = 'pending' AND p.withdrawn_at IS NULL);
