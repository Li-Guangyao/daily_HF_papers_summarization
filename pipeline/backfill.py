"""Local backfill: python -m pipeline.backfill --date 2026-10-01 | --from 2026-09-01 --to 2026-09-30

Fetches papers for dates D1 doesn't have yet, generates their summaries, and stores everything in D1.
See system_design_improved.md §5.9, or run with --help.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
from datetime import date, timedelta
from pathlib import Path

from . import store
from .config import expand_dates, load_env_file, migration_sql, open_db, utc_today
from .core import AllProvidersDown, RunOptions, log, run
from .d1 import LocalD1
from .lock import RunLock

OUT_DIR = Path("backfill_out")


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m pipeline.backfill", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--date", action="append", default=[], help="a date to process (repeatable)")
    p.add_argument("--from", dest="start", help="first date of a range (inclusive)")
    p.add_argument("--to", dest="end", help="last date of a range (inclusive); default yesterday")
    p.add_argument("--status", action="store_true", help="only report what D1 holds for these dates")
    p.add_argument("--dry-run", action="store_true", help=f"write results to ./{OUT_DIR}/ instead of D1")
    p.add_argument("--limit", type=int, help="only the top N papers (by upvotes) of each date")
    p.add_argument("--refresh-list", action="store_true", help="fetch lists again even for ready/empty dates")
    p.add_argument("--redo", action="store_true", help="regenerate the summaries of these dates")
    p.add_argument("--family", help="use only this model family, e.g. glm-4.7-flash")
    p.add_argument("--include-weekends", action="store_true", help="also process Saturdays and Sundays")
    p.add_argument("--local-db", help="SQLite file instead of the remote D1 ('auto' = wrangler's local D1)")
    p.add_argument("--env-file", default=".env")
    p.add_argument("--yes", action="store_true", help="don't ask for confirmation (--redo)")
    return p.parse_args(argv)


def resolve_dates(args) -> list[str]:
    yesterday = utc_today() - timedelta(days=1)
    specs = list(args.date)
    if args.start:
        specs.append(f"{args.start}..{args.end or yesterday.isoformat()}")
    if not specs:
        sys.exit("Give --date or --from/--to.")
    dates = expand_dates(",".join(specs), include_weekends=args.include_weekends)
    if late := [d for d in dates if date.fromisoformat(d) > yesterday]:
        sys.exit(f"Dates after yesterday (UTC) aren't complete yet: {', '.join(late)}")
    if not dates:
        sys.exit("No workdays in that range (use --include-weekends to include Saturday/Sunday).")
    return dates


async def print_status(db, dates: list[str]) -> None:
    rows = {r["date"]: r for r in await store.date_report(db, dates[0], dates[-1])}
    print(f"{'date':<11} {'status':<14} {'papers':>6} {'ok':>4} {'pending':>8} {'error':>6} {'fatal':>6}")
    for d in dates:
        r = rows.get(d)
        if not r:
            print(f"{d:<11} {'(not fetched)':<14} {'-':>6} {'-':>4} {'-':>8} {'-':>6} {'-':>6}")
        else:
            print(f"{d:<11} {r['status']:<14} {r['paper_count']:>6} {r['ok'] or 0:>4} {r['pending'] or 0:>8} "
                  f"{r['error'] or 0:>6} {r['fatal'] or 0:>6}")


async def dry_run(args, dates: list[str]) -> int:
    """Run the real pipeline against a throwaway in-memory database, then export the results as files."""
    tmp = LocalD1(":memory:")
    tmp.executescript(migration_sql())
    if args.local_db or os.environ.get("LOCAL_D1_PATH") or os.environ.get("CF_API_TOKEN"):
        real = open_db(args.local_db)
        models = await real.query("SELECT * FROM llm_models")
        await real.aclose()
        await tmp.query("DELETE FROM llm_models")
        for m in models:
            cols = list(m)
            await tmp.query(f"INSERT INTO llm_models ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                            [m[c] for c in cols])
        log(f"dry run: using {len(models)} llm_models rows from your database")
    else:
        log("dry run: no database configured, using the default llm_models rows from the migration")
    try:
        report = await run(tmp, RunOptions(dates=dates, limit=args.limit, family=args.family))
    except AllProvidersDown as e:
        log(f"ERROR: {e}")
        return 1
    print(report.markdown())
    rows = await tmp.query(
        "SELECT p.daily_date, p.id, p.title, s.status, s.title_zh, s.summary_en, s.summary_zh, s.source, "
        "m.display_name, m.provider, p.category_minor FROM summaries s JOIN papers p ON p.id = s.paper_id "
        "LEFT JOIN llm_models m ON m.id = s.en_model_id WHERE s.summary_en IS NOT NULL")
    for r in rows:
        folder = OUT_DIR / r["daily_date"]
        folder.mkdir(parents=True, exist_ok=True)
        header = f"<!-- {r['id']} · {r['display_name']} via {r['provider']} · source={r['source']} · category={r['category_minor']} -->\n"
        (folder / f"{r['id']}.en.md").write_text(f"{header}# {r['title']}\n\n{r['summary_en']}\n", encoding="utf-8")
        if r["summary_zh"]:
            (folder / f"{r['id']}.zh.md").write_text(f"{header}# {r['title_zh']}\n\n{r['summary_zh']}\n",
                                                     encoding="utf-8")
    log(f"dry run: wrote {len(rows)} papers to {OUT_DIR.resolve()}")
    return 0


async def amain(args) -> int:
    dates = resolve_dates(args)
    if args.dry_run:
        return await dry_run(args, dates)
    db = open_db(args.local_db)
    try:
        if args.status:
            await print_status(db, dates)
            return 0
        if args.redo:
            if not args.yes and input(f"Regenerate all summaries of {len(dates)} date(s)? [y/N] ").lower() != "y":
                return 1
            log(f"reset {await store.reset_dates(db, dates)} summaries")
        return await _work(db, args, dates)
    finally:
        await db.aclose()


async def _work(db, args, dates: list[str]) -> int:
    loop = asyncio.get_running_loop()
    interrupted = False
    while True:
        stop = asyncio.Event()
        yielded = False

        def on_sigint():
            nonlocal interrupted
            if interrupted:
                raise KeyboardInterrupt
            interrupted = True
            log("stopping after in-flight requests finish (press Ctrl+C again to quit now)")
            stop.set()

        def on_yield():
            nonlocal yielded
            if not yielded:
                yielded = True
                log("a scheduled run is waiting for the API keys; pausing after in-flight requests")
                stop.set()

        lock = RunLock(db)
        await lock.acquire(max_wait_s=None, poll_s=60, ask_holder_to_yield=False, log=log)
        lock.keep_alive(on_yield)
        loop.add_signal_handler(signal.SIGINT, on_sigint)
        try:
            report = await run(db, RunOptions(dates=dates, sync="always" if args.refresh_list else "missing",
                                              limit=args.limit, family=args.family), stop_event=stop)
        except AllProvidersDown as e:
            log(f"ERROR: {e}")
            return 1
        finally:
            loop.remove_signal_handler(signal.SIGINT)
            await lock.release()
        print(report.markdown())
        if yielded and not interrupted:
            await asyncio.sleep(60)  # let the scheduled run take the lock, then wait for it
            continue
        return 0 if report.remaining == 0 else 1


def main(argv=None) -> int:
    args = parse_args(argv)
    load_env_file(args.env_file)
    try:
        return asyncio.run(amain(args))
    except KeyboardInterrupt:
        print("\ninterrupted; run the same command again to continue")
        return 130


if __name__ == "__main__":
    sys.exit(main())
