"""All SQL the pipeline runs against D1."""

from __future__ import annotations

import os

from .config import Route
from .prompts import PROMPT_VERSION

MAX_ATTEMPTS = 3  # paper errors before a summary becomes 'fatal'
BATCH_SIZE = 40

PAPER_COLUMNS = [
    "id", "daily_date", "title", "authors", "abstract", "published_at", "thumbnail", "project_page", "github_repo",
    "github_stars", "upvotes", "num_comments", "hf_ai_summary", "hf_ai_keywords", "hf_ai_summary_model",
    "withdrawn_at",
]
UPSERT_PAPER = (
    f"INSERT INTO papers ({', '.join(PAPER_COLUMNS)}) VALUES ({', '.join('?' * len(PAPER_COLUMNS))}) "
    "ON CONFLICT(id) DO UPDATE SET upvotes = excluded.upvotes, github_stars = excluded.github_stars, "
    "num_comments = excluded.num_comments, withdrawn_at = excluded.withdrawn_at, "
    "thumbnail = excluded.thumbnail, updated_at = datetime('now')"
)


async def run_batches(db, statements: list) -> None:
    for i in range(0, len(statements), BATCH_SIZE):
        await db.batch(statements[i: i + BATCH_SIZE])


# --- lists -------------------------------------------------------------------------------------

async def list_states(db, dates: list[str]) -> dict[str, str]:
    """{date: status} for dates that have a daily_lists row."""
    if not dates:
        return {}
    rows = await db.query(
        "SELECT date, status FROM daily_lists WHERE date BETWEEN ? AND ?", [min(dates), max(dates)])
    wanted = set(dates)
    return {r["date"]: r["status"] for r in rows if r["date"] in wanted}


async def sync_list(db, day: str, rows: list[dict]) -> int:
    """Upsert a day's papers, create pending summary rows for new ones, and upsert daily_lists.
    Returns the number of new summary rows."""
    statements = [(UPSERT_PAPER, [r[c] for c in PAPER_COLUMNS]) for r in rows]
    statements += [
        ("INSERT INTO summaries (paper_id) VALUES (?) ON CONFLICT(paper_id) DO NOTHING", [r["id"]])
        for r in rows if not r["withdrawn_at"]
    ]
    before = await db.query("SELECT COUNT(*) AS n FROM summaries s JOIN papers p ON p.id = s.paper_id "
                            "WHERE p.daily_date = ?", [day])
    await run_batches(db, statements)
    status = "processing" if rows else "empty"
    await db.query(
        "INSERT INTO daily_lists (date, paper_count, status, fetched_at) VALUES (?, ?, ?, datetime('now')) "
        "ON CONFLICT(date) DO UPDATE SET paper_count = excluded.paper_count, fetched_at = excluded.fetched_at, "
        "status = CASE WHEN daily_lists.status = 'ready' THEN 'ready' ELSE excluded.status END",
        [day, len(rows), status])
    after = await db.query("SELECT COUNT(*) AS n FROM summaries s JOIN papers p ON p.id = s.paper_id "
                           "WHERE p.daily_date = ?", [day])
    return int(after[0]["n"]) - int(before[0]["n"])


async def has_work(db, dates: list[str], since: str) -> bool:
    """False when every date's list is stored and settled, and nothing since `since` is unfinished."""
    states = await list_states(db, dates)
    if any(states.get(d) not in ("ready", "empty") for d in dates):
        return True
    rows = await db.query(
        "SELECT COUNT(*) AS n FROM summaries s JOIN papers p ON p.id = s.paper_id "
        "WHERE p.daily_date >= ? AND p.withdrawn_at IS NULL AND s.status IN ('pending', 'error') "
        "AND s.attempts < ?", [since, MAX_ATTEMPTS])
    if int(rows[0]["n"]) > 0:
        return True
    rows = await db.query(f"SELECT COUNT(*) AS n {UNCLASSIFIED}", [MAX_ATTEMPTS])
    return int(rows[0]["n"]) > 0


# --- planning ----------------------------------------------------------------------------------

async def reset_retired(db, start: str, end: str) -> int:
    """Papers whose EN was written by a family that has no enabled row left start over with another model."""
    rows = await db.query(
        "UPDATE summaries SET summary_en = NULL, family = NULL, en_model_id = NULL, source = NULL, "
        "status = 'pending', updated_at = datetime('now') "
        "WHERE summary_en IS NOT NULL AND summary_zh IS NULL AND status IN ('pending', 'error') "
        "AND family NOT IN (SELECT family FROM llm_models WHERE is_enabled = 1) "
        "AND paper_id IN (SELECT id FROM papers WHERE daily_date BETWEEN ? AND ?) RETURNING paper_id",
        [start, end])
    return len(rows)


async def unfinished(db, start: str, end: str) -> list[dict]:
    return await db.query(
        "SELECT s.paper_id, s.family, s.summary_en IS NOT NULL AS has_en, p.title, p.abstract, p.daily_date, "
        "p.upvotes, p.md_misses FROM summaries s JOIN papers p ON p.id = s.paper_id "
        "WHERE p.daily_date BETWEEN ? AND ? AND p.withdrawn_at IS NULL AND s.status IN ('pending', 'error') "
        "AND s.attempts < ? ORDER BY p.daily_date, p.upvotes DESC",
        [start, end, MAX_ATTEMPTS])


# --- per-job state -----------------------------------------------------------------------------

async def get_summary(db, paper_id: str) -> dict | None:
    rows = await db.query(
        "SELECT status, family, summary_en, summary_zh FROM summaries WHERE paper_id = ?", [paper_id])
    return rows[0] if rows else None


async def save_en(db, paper_id: str, text: str, route: Route, source: str, truncated: bool) -> None:
    await db.query(
        "UPDATE summaries SET summary_en = ?, family = ?, en_model_id = ?, source = ?, prompt_version = ?, "
        "input_truncated = ?, status = 'pending', last_error = NULL, updated_at = datetime('now') "
        "WHERE paper_id = ? AND summary_en IS NULL",
        [text, route.family, route.id, source, PROMPT_VERSION, int(truncated), paper_id])


async def save_zh(db, paper_id: str, title_zh: str, text: str, route: Route) -> None:
    await db.query(
        "UPDATE summaries SET title_zh = ?, summary_zh = ?, zh_model_id = ?, status = 'ok', last_error = NULL, "
        "updated_at = datetime('now') WHERE paper_id = ? AND family = ?",
        [title_zh, text, route.id, paper_id, route.family])


async def set_category(db, paper_id: str, major: str, minor: str) -> None:
    await db.query("UPDATE papers SET category_major = ?, category_minor = ?, updated_at = datetime('now') "
                   "WHERE id = ?", [major, minor, paper_id])


async def category_failure(db, paper_id: str) -> None:
    await db.query("UPDATE papers SET category_attempts = category_attempts + 1 WHERE id = ?", [paper_id])


UNCLASSIFIED = (
    "FROM papers p LEFT JOIN summaries s ON s.paper_id = p.id "
    "WHERE p.category_minor IS NULL AND p.category_attempts < ? "
    "AND (s.status IS NULL OR s.status IN ('ok', 'fatal'))"  # settled papers; pending ones get it from the EN step
)


async def unclassified(db, limit: int) -> list[dict]:
    """Papers (any date) that still need a category, newest first."""
    return await db.query(f"SELECT p.id, p.title, p.abstract {UNCLASSIFIED} ORDER BY p.daily_date DESC, p.upvotes DESC "
                          "LIMIT ?", [MAX_ATTEMPTS, limit])


async def paper_failure(db, paper_id: str, message: str) -> bool:
    """Count a paper error. Returns True when the summary became fatal."""
    rows = await db.query(
        "UPDATE summaries SET attempts = attempts + 1, last_error = ?, updated_at = datetime('now'), "
        "status = CASE WHEN attempts + 1 >= ? THEN 'fatal' ELSE 'error' END "
        "WHERE paper_id = ? RETURNING status", [message[:500], MAX_ATTEMPTS, paper_id])
    return bool(rows) and rows[0]["status"] == "fatal"


async def mark_error(db, paper_id: str, message: str) -> None:
    """Leave a row for the next run (provider trouble; does not count as an attempt)."""
    await db.query(
        "UPDATE summaries SET status = 'error', last_error = ?, updated_at = datetime('now') "
        "WHERE paper_id = ? AND status IN ('pending', 'error')", [message[:500], paper_id])


async def md_miss(db, paper_id: str) -> None:
    await db.query("UPDATE papers SET md_misses = md_misses + 1 WHERE id = ?", [paper_id])


async def log_error(db, job: str, target: str, message: str, model_id: int | None = None,
                    http_status: int | None = None, code: str | None = None) -> None:
    try:
        await db.query(
            "INSERT INTO job_errors (run_id, job, target, model_id, http_status, error_code, message) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [os.environ.get("GITHUB_RUN_ID") or "local", job, target, model_id, http_status, code, message[:1000]])
    except Exception as e:  # logging must never break the run
        print(f"warning: could not write job_errors: {e}")


# --- readiness and reporting -------------------------------------------------------------------

async def check_ready(db, day: str) -> bool:
    """Mark the date ready when every paper's summary is settled. Returns True if it is ready now."""
    rows = await db.query(
        "SELECT COUNT(*) AS n FROM summaries s JOIN papers p ON p.id = s.paper_id "
        "WHERE p.daily_date = ? AND p.withdrawn_at IS NULL AND s.status NOT IN ('ok', 'fatal')", [day])
    if int(rows[0]["n"]) > 0:
        return False
    await db.query("UPDATE daily_lists SET status = 'ready', ready_at = datetime('now') "
                   "WHERE date = ? AND status = 'processing'", [day])
    return True


async def date_report(db, start: str, end: str) -> list[dict]:
    return await db.query(
        "SELECT d.date, d.status, d.paper_count, "
        "SUM(CASE WHEN s.status = 'ok' THEN 1 ELSE 0 END) AS ok, "
        "SUM(CASE WHEN s.status = 'pending' THEN 1 ELSE 0 END) AS pending, "
        "SUM(CASE WHEN s.status = 'error' THEN 1 ELSE 0 END) AS error, "
        "SUM(CASE WHEN s.status = 'fatal' THEN 1 ELSE 0 END) AS fatal "
        "FROM daily_lists d LEFT JOIN papers p ON p.daily_date = d.date AND p.withdrawn_at IS NULL "
        "LEFT JOIN summaries s ON s.paper_id = p.id WHERE d.date BETWEEN ? AND ? "
        "GROUP BY d.date, d.status, d.paper_count ORDER BY d.date", [start, end])


async def reset_dates(db, dates: list[str]) -> int:
    """--redo: regenerate every summary of these dates."""
    total = 0
    for day in dates:
        rows = await db.query(
            "UPDATE summaries SET summary_en = NULL, summary_zh = NULL, title_zh = NULL, family = NULL, "
            "en_model_id = NULL, zh_model_id = NULL, source = NULL, status = 'pending', attempts = 0, "
            "last_error = NULL, updated_at = datetime('now') "
            "WHERE paper_id IN (SELECT id FROM papers WHERE daily_date = ?) RETURNING paper_id", [day])
        await db.query("UPDATE daily_lists SET status = 'processing', ready_at = NULL "
                       "WHERE date = ? AND status = 'ready'", [day])
        total += len(rows)
    return total
