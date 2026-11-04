"""Scheduled entry point (GitHub Actions): python -m pipeline.main

Processes the previous two workdays (or DATES), finishes unfinished work from the last 7 days,
and writes a run summary to $GITHUB_STEP_SUMMARY.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import timedelta

from . import store
from .config import expand_dates, load_env_file, open_db, previous_workdays, utc_today
from .core import AllProvidersDown, RunOptions, log, run
from .lock import RunLock


async def amain() -> int:
    load_env_file()
    db = open_db()
    today = utc_today()
    explicit = os.environ.get("DATES", "").strip()
    dates = expand_dates(explicit) if explicit else previous_workdays(today, 2)
    lookback_from = (today - timedelta(days=7)).isoformat()
    budget = float(os.environ.get("TIME_BUDGET_MINUTES") or 45) * 60
    log(f"dates {', '.join(dates)}; budget {budget / 60:.0f} min")

    lock = RunLock(db)
    if not await lock.acquire(max_wait_s=15 * 60, poll_s=30, ask_holder_to_yield=True, log=log):
        log("could not get the run lock within 15 minutes; the next scheduled run will try again")
        await db.aclose()
        return 0
    try:
        if not explicit and not await store.has_work(db, dates, lookback_from):
            log("nothing to do")
            return 0
        lock.keep_alive()
        try:
            report = await run(db, RunOptions(dates=dates, lookback_from=None if explicit else lookback_from,
                                              time_budget_s=budget))
        except AllProvidersDown as e:
            log(f"ERROR: {e}")
            _write_summary(f"## Pipeline run\n\n**{e}.** Today's papers stay hidden until a later run succeeds.")
            return 1
        print(report.markdown())
        _write_summary(report.markdown())
        return 1 if report.outage else 0
    finally:
        await lock.release()
        await db.aclose()


def _write_summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text + "\n")


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
