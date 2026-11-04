-- 1. Repair summaries stuck by the old retry bug: on D1 a paper never became 'fatal', so some rows stayed 'error'
--    with attempts >= 3. The pipeline no longer picks those up, and their dates could never become ready.
--    Give them a fresh set of 3 tries; if they still fail they now become 'fatal' and the date is released.
UPDATE summaries SET attempts = 0 WHERE status = 'error' AND attempts >= 3;

-- 2. When the paper's full-text markdown was first found missing. The pipeline waits a number of hours from this
--    moment (not a number of runs) before falling back to the abstract.
ALTER TABLE papers ADD COLUMN md_first_miss_at TEXT;
UPDATE papers SET md_first_miss_at = created_at WHERE md_misses > 0;
