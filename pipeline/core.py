"""One pipeline run, shared by the scheduled job (main.py) and the local backfill (backfill.py)."""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

import httpx

from . import hf, store
from .classify import classify_missing
from .config import Route, load_routes
from .llm import ProviderError, health_check
from .pool import Job, RouteState, Scheduler, Timing


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


@dataclass
class RunOptions:
    dates: list[str]                      # lists to sync and process
    lookback_from: str | None = None      # also finish unfinished rows from this date on (scheduled runs)
    sync: str = "always"                  # always | missing (only dates not ready/empty)
    limit: int | None = None              # only the top N papers per date (testing)
    family: str | None = None             # only this model
    time_budget_s: float | None = None    # stop taking new jobs after this
    timing: Timing = field(default_factory=Timing)
    hf_concurrency: int = 8


class AllProvidersDown(RuntimeError):
    pass


@dataclass
class RunReport:
    states: list[RouteState]
    skipped: list[tuple[Route, str]]
    dates: list[dict]
    remaining: int
    stopped_early: bool
    done: Counter
    categories: Counter = field(default_factory=Counter)  # the classify pass

    @property
    def all_down(self) -> bool:
        return all(not s.alive for s in self.states)

    @property
    def outage(self) -> bool:
        """Work was left because providers were down (the scheduled run then exits with code 1)."""
        return self.remaining > 0 and not self.stopped_early

    def markdown(self) -> str:
        lines = ["## Pipeline run", "", "| Provider | Requests | OK | Provider errors | Paper errors | 429s | State |",
                 "|---|---|---|---|---|---|---|"]
        for s in self.states:
            st = s.stats
            state = f"down ({s.down_reason})" if s.down_reason else s.state
            lines.append(f"| {s.route.label} | {st['requests']} | {st['ok']} | {st['provider_errors']} | "
                         f"{st['paper_errors']} | {st['rate_limited']} | {state} |")
        for route, reason in self.skipped:
            lines.append(f"| {route.label} | 0 | 0 | 0 | 0 | 0 | skipped ({reason}) |")
        lines += ["", "| Date | Status | Papers | OK | Pending | Error | Fatal |", "|---|---|---|---|---|---|---|"]
        for d in self.dates:
            lines.append(f"| {d['date']} | {d['status']} | {d['paper_count']} | {d['ok'] or 0} | "
                         f"{d['pending'] or 0} | {d['error'] or 0} | {d['fatal'] or 0} |")
        lines.append("")
        lines.append(f"Summaries finished this run: {self.done['ok']}, became fatal: {self.done['fatal']}, "
                     f"jobs left: {self.remaining}" + (" (stopped early)" if self.stopped_early else ""))
        c = self.categories
        lines.append(f"Categories from walkthroughs: {self.done['categorized']}; from title + abstract: {c['ok']} "
                     f"(failed {c['failed']}, left for next run {c['skipped']})")
        if any(s.down_reason and s.down_reason.startswith("gone") for s in self.states):
            lines.append("")
            lines.append("**A model looks retired or no longer free. Disable its row in `llm_models`.**")
        return "\n".join(lines)


async def _healthy_routes(client, db, routes: list[Route],
                          retry_delay: float = 10) -> tuple[list[Route], list[tuple[Route, str]]]:
    async def check(route: Route):
        for attempt in range(3):
            try:
                await health_check(client, route)
                return route, None
            except ProviderError as e:
                if e.kind == "rate_limit":  # busy but reachable: the workers back off and retry
                    log(f"busy     {route.label}: {str(e)[:120]}")
                    return route, None
                if e.kind != "transient" or attempt == 2:
                    return route, f"{e.kind}: {str(e)[:200]}"
            except Exception as e:
                return route, f"{type(e).__name__}: {str(e)[:200]}"
            await asyncio.sleep(retry_delay * (attempt + 1))

    healthy, failed = [], []
    for route, err in await asyncio.gather(*(check(r) for r in routes)):
        if err is None:
            healthy.append(route)
            log(f"healthy  {route.label}")
        else:
            failed.append((route, f"health check failed: {err}"))
            log(f"UNHEALTHY {route.label}: {err}")
            await store.log_error(db, "healthcheck", route.label, err, route.id)
    return healthy, failed


async def run(db, opts: RunOptions, *, client: httpx.AsyncClient | None = None,
              stop_event: asyncio.Event | None = None) -> RunReport:
    own_client = client is None
    client = client or httpx.AsyncClient(limits=httpx.Limits(max_connections=200, max_keepalive_connections=50))
    try:
        # 1. Providers: enabled rows with a key, that answer a tiny request.
        routes, no_key = await load_routes(db, opts.family)
        skipped = [(r, f"{r.api_key_env} not set") for r in no_key]
        for r, why in skipped:
            log(f"skip     {r.label}: {why}")
        healthy, failed = await _healthy_routes(client, db, routes, opts.timing.retry_base)
        skipped += failed
        if not healthy:
            raise AllProvidersDown("no LLM provider is available; nothing was changed")

        # 2. Lists.
        states = await store.list_states(db, opts.dates)
        for day in opts.dates:
            if opts.sync == "missing" and states.get(day) in ("ready", "empty"):
                continue
            items = await hf.fetch_daily_list(client, day)
            rows = [hf.to_row(item, day) for item in items]
            new = await store.sync_list(db, day, rows)
            log(f"list     {day}: {len(rows)} papers, {new} new")

        # 3. Plan.
        start = min([*opts.dates, *([opts.lookback_from] if opts.lookback_from else [])])
        end = max(opts.dates)
        if n := await store.reset_retired(db, start, end):
            log(f"reset    {n} half-done papers whose model was disabled")
        rows = await store.unfinished(db, start, end)
        if not opts.lookback_from:
            wanted = set(opts.dates)
            rows = [r for r in rows if r["daily_date"] in wanted]
        if opts.limit:
            per_date: Counter = Counter()
            rows = [r for r in rows if (per_date.update([r["daily_date"]]) or per_date[r["daily_date"]] <= opts.limit)]

        deadline = time.monotonic() + opts.time_budget_s if opts.time_budget_s else None
        sched = Scheduler(db, client, healthy, opts.timing, deadline=deadline, stop_event=stop_event,
                          hf_concurrency=opts.hf_concurrency, log=log)
        for r in rows:
            has_en = bool(r["has_en"])
            wait = r["md_wait_hours"]
            sched.add(Job(r["paper_id"], r["daily_date"], r["title"], r["abstract"],
                          float(wait) if wait is not None else None,
                          "zh" if has_en else "en", r["family"] if has_en else None))
        log(f"plan     {len(rows)} papers to process with {sum(r.max_concurrency for r in healthy)} workers")

        # 4. Work.
        await sched.run()

        # 5. Categories for papers that have none yet (including papers stored before categories existed).
        categories = Counter()
        if not sched.stopping():
            categories = await classify_missing(db, client, sched.states, opts.timing, log=log,
                                                stopping=sched.stopping)

        # 6. Readiness for every date we touched.
        for day in sorted(sched.touched_dates | set(opts.dates)):
            await store.check_ready(db, day)

        return RunReport(sched.states, skipped, await store.date_report(db, start, end), sched.remaining(),
                         sched.stopping(), sched.done, categories)
    finally:
        if own_client:
            await client.aclose()
