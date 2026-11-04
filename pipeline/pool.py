"""Worker pools: one pool per provider row, a shared EN queue, one ZH queue per model family.

See system_design_improved.md §5.3–§5.5.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import date, timedelta

import httpx

from . import hf, store
from .config import Route, utc_today
from .llm import PaperError, ProviderError, chat
from .markdown import OutputCheckError, budget_chars, check_en, check_zh, prepare_input, snippet, strip_reasoning
from .prompts import en_messages, zh_messages
from .taxonomy import parse_category


@dataclass
class Timing:
    retry_base: float = 10.0          # first retry delay after a provider error; doubles each time
    rate_limit_pause: float = 20.0    # pause after a 429 without Retry-After
    cooling: float = 120.0            # pause after repeated failures
    idle_poll: float = 2.0
    first_chunk_timeout: float = 120.0
    call_timeout: float = 600.0
    failures_before_cooling: int = 3
    max_cooling_cycles: int = 3
    max_provider_failures: int = 6    # per job per run, then the row waits for the next run
    max_rate_limits: int = 20
    paper_retry_delay: float = 30.0   # wait before retrying a paper whose output was unusable


MD_WAIT_HOURS = 10   # wait this long after the markdown was first missing, then use the abstract
                     # (scheduled runs are 6 hours apart, so the third run of the day falls back)
MD_OLD_DAYS = 3      # papers this old use the abstract right away (backfills)


class RpmLimiter:
    """At most `rpm` request starts in any 60-second window (90% of the provider's limit), spaced evenly:
    providers reject a burst (all workers starting at once) even when the per-minute total is fine."""

    def __init__(self, rpm: int):
        self.base = max(1, int(rpm * 0.9))
        self.reduced_until = 0.0
        self.starts: deque[float] = deque()
        self.next_start = 0.0
        self.lock = asyncio.Lock()

    @property
    def limit(self) -> int:
        return max(1, int(self.base * 0.75)) if time.monotonic() < self.reduced_until else self.base

    def reduce(self, seconds: float = 300) -> None:
        self.reduced_until = time.monotonic() + seconds

    async def acquire(self) -> None:
        async with self.lock:
            while True:
                now = time.monotonic()
                if now < self.next_start:
                    await asyncio.sleep(self.next_start - now)
                    continue
                while self.starts and now - self.starts[0] >= 60:
                    self.starts.popleft()
                if len(self.starts) < self.limit:
                    self.starts.append(now)
                    self.next_start = now + 60 / self.limit
                    return
                await asyncio.sleep(60 - (now - self.starts[0]) + 0.05)


class RouteState:
    """Health of one provider row: healthy → cooling → probing → healthy, or down for the rest of the run."""

    def __init__(self, route: Route, timing: Timing):
        self.route, self.timing = route, timing
        self.limiter = RpmLimiter(route.rpm_limit)
        self.state = "healthy"
        self.until = 0.0
        self.paused_until = 0.0
        self.consecutive = 0
        self.cycles = 0
        self.probe_busy = False
        self.down_reason: str | None = None
        self.stats: Counter = Counter()

    @property
    def alive(self) -> bool:
        return self.state != "down"

    async def gate(self) -> bool:
        """Wait until this row may send a request. False once the row is down."""
        while True:
            now = time.monotonic()
            if self.state == "down":
                return False
            if self.state == "cooling":
                if now < self.until:
                    await asyncio.sleep(min(self.until - now, 5))
                    continue
                self.state = "probing"
            if self.state == "probing":
                if self.probe_busy:  # only one test request while recovering
                    await asyncio.sleep(1)
                    continue
                self.probe_busy = True
                return True
            if now < self.paused_until:
                await asyncio.sleep(min(self.paused_until - now, 5))
                continue
            return True

    def release_probe(self) -> None:
        self.probe_busy = False

    def set_down(self, reason: str) -> None:
        self.state, self.down_reason, self.probe_busy = "down", reason, False

    def success(self) -> None:
        self.consecutive = 0
        if self.state == "probing":
            self.state, self.cycles = "healthy", 0
        self.probe_busy = False

    def failure(self, err: ProviderError) -> None:
        self.probe_busy = False
        if err.kind in ("quota", "auth", "gone"):
            self.set_down(f"{err.kind}: {err}")
            return
        if err.kind == "rate_limit":
            self.stats["rate_limited"] += 1
            self.paused_until = time.monotonic() + (err.retry_after or self.timing.rate_limit_pause)
            self.limiter.reduce()
            return
        self.consecutive += 1
        if self.state == "probing" or self.consecutive >= self.timing.failures_before_cooling:
            self.cycles += 1
            self.consecutive = 0
            if self.cycles > self.timing.max_cooling_cycles:
                self.set_down("no response after repeated cool-downs")
            else:
                self.state, self.until = "cooling", time.monotonic() + self.timing.cooling


@dataclass
class Job:
    paper_id: str
    daily_date: str
    title: str
    abstract: str | None
    md_wait_hours: float | None        # hours since the markdown was first missing; None if never
    step: str                          # en | zh
    family: str | None = None          # required for zh
    provider_failures: int = 0
    rate_limits: int = 0
    not_before: float = 0.0
    avoid_route: int | None = None


class MarkdownCache:
    """Fetch each paper's markdown at most once per run; kept in memory only until its EN step is done."""

    def __init__(self, client: httpx.AsyncClient, concurrency: int):
        self.client = client
        self.sem = asyncio.Semaphore(concurrency)
        self.futures: dict[str, asyncio.Future] = {}

    async def get(self, paper_id: str) -> str | None:
        if paper_id not in self.futures:
            self.futures[paper_id] = asyncio.ensure_future(self._fetch(paper_id))
        try:
            return await asyncio.shield(self.futures[paper_id])
        except Exception:
            self.futures.pop(paper_id, None)  # let a later attempt try again
            raise

    async def _fetch(self, paper_id: str) -> str | None:
        async with self.sem:
            return await hf.fetch_markdown(self.client, paper_id)

    def release(self, paper_id: str) -> None:
        self.futures.pop(paper_id, None)


class SkipForNow(Exception):
    """The job can't run in this run (for example the markdown isn't published yet)."""


class Scheduler:
    def __init__(self, db, client: httpx.AsyncClient, routes: list[Route], timing: Timing, *,
                 deadline: float | None, stop_event: asyncio.Event | None, hf_concurrency: int, log):
        self.db, self.client, self.timing, self.log = db, client, timing, log
        self.deadline, self.stop_event = deadline, stop_event
        self.states = [RouteState(r, timing) for r in routes]
        self.en: list[Job] = []
        self.zh: dict[str, list[Job]] = defaultdict(list)
        self.inflight_en = 0
        self.inflight_zh: Counter = Counter()
        self.md = MarkdownCache(client, hf_concurrency)
        self.md_missed: set[str] = set()
        self.touched_dates: set[str] = set()
        self.done = Counter()

    # --- queue -------------------------------------------------------------------------------

    def add(self, job: Job) -> None:
        self.touched_dates.add(job.daily_date)
        (self.zh[job.family] if job.step == "zh" else self.en).append(job)

    def _requeue(self, job: Job, delay: float, avoid: int | None) -> None:
        job.not_before = time.monotonic() + delay
        job.avoid_route = avoid
        (self.zh[job.family] if job.step == "zh" else self.en).append(job)

    def _others_alive(self, job: Job, rs: RouteState) -> bool:
        return any(o is not rs and o.alive and (job.step == "en" or o.route.family == job.family)
                   for o in self.states)

    def _take(self, rs: RouteState) -> Job | None:
        now = time.monotonic()
        for queue in (self.zh[rs.route.family], self.en):  # finish half-done papers first
            for i, job in enumerate(queue):
                if job.not_before > now:
                    continue
                if job.avoid_route == rs.route.id and self._others_alive(job, rs):
                    continue
                return queue.pop(i)
        return None

    def _work_left_for(self, rs: RouteState) -> bool:
        return bool(self.en or self.zh[rs.route.family] or self.inflight_en or self.inflight_zh[rs.route.family])

    def remaining(self) -> int:
        return len(self.en) + sum(len(q) for q in self.zh.values())

    def stopping(self) -> bool:
        if self.stop_event is not None and self.stop_event.is_set():
            return True
        return self.deadline is not None and time.monotonic() > self.deadline

    # --- workers -----------------------------------------------------------------------------

    async def run(self) -> None:
        tasks = [asyncio.create_task(self._worker(rs)) for rs in self.states for _ in range(rs.route.max_concurrency)]
        await asyncio.gather(*tasks)

    async def _worker(self, rs: RouteState) -> None:
        while not self.stopping():
            if not await rs.gate():
                return
            job = self._take(rs)
            if job is None:
                rs.release_probe()
                if not self._work_left_for(rs):
                    return
                await asyncio.sleep(self.timing.idle_poll)
                continue
            await self._run_job(rs, job)

    async def _run_job(self, rs: RouteState, job: Job) -> None:
        if job.step == "en":
            self.inflight_en += 1
        else:
            self.inflight_zh[job.family] += 1
        try:
            if job.step == "en":
                await self._en(rs, job)
            else:
                await self._zh(rs, job)
            rs.success()
            rs.stats["ok"] += 1
        except SkipForNow:
            rs.release_probe()
        except ProviderError as e:
            await self._provider_error(rs, job, e)
        except (PaperError, OutputCheckError) as e:
            await self._paper_error(rs, job, e)
        except hf.HFError as e:  # HF itself failed: retry later without blaming the provider
            rs.release_probe()
            await store.log_error(self.db, "markdown", job.paper_id, str(e))
            await store.mark_error(self.db, job.paper_id, str(e))
            self.md.release(job.paper_id)
        finally:
            if job.step == "en":
                self.inflight_en -= 1
            else:
                self.inflight_zh[job.family] -= 1

    async def _provider_error(self, rs: RouteState, job: Job, e: ProviderError) -> None:
        rs.failure(e)
        rs.stats["provider_errors"] += 1
        step = "summarize" if job.step == "en" else "translate"
        self.log(f"retry {step} {job.paper_id} on {rs.route.label}: {e.kind}: {str(e)[:160]}")
        await store.log_error(self.db, step, job.paper_id, f"{e.kind}: {e}", rs.route.id, e.status, e.code)
        if e.kind == "rate_limit":
            job.rate_limits += 1
            give_up = job.rate_limits >= self.timing.max_rate_limits
            delay = 0.0
        else:
            job.provider_failures += 1
            give_up = job.provider_failures >= self.timing.max_provider_failures
            delay = self.timing.retry_base * 2 ** (job.provider_failures - 1) * random.uniform(0.8, 1.2)
        if give_up:
            await store.mark_error(self.db, job.paper_id, f"provider errors: {e}")
            self.md.release(job.paper_id)
        else:
            self._requeue(job, delay, rs.route.id)

    async def _paper_error(self, rs: RouteState, job: Job, e: Exception) -> None:
        rs.success()  # the provider answered; the problem is this paper or this output
        rs.stats["paper_errors"] += 1
        step = "summarize" if job.step == "en" else "translate"
        raw = getattr(e, "raw", None)
        shown = f"{e} | output: {snippet(raw)}" if raw else str(e)
        self.log(f"paper error {step} {job.paper_id} on {rs.route.label}: {shown[:600]}")
        await store.log_error(self.db, step, job.paper_id, shown, rs.route.id,
                              getattr(e, "status", None), getattr(e, "code", None))
        if await store.paper_failure(self.db, job.paper_id, f"{step}: {e}"):
            self.md.release(job.paper_id)
            self.done["fatal"] += 1
            await store.check_ready(self.db, job.daily_date)
        else:
            self._requeue(job, self.timing.paper_retry_delay, rs.route.id)

    async def _acquire(self, rs: RouteState) -> None:
        await rs.limiter.acquire()
        rs.stats["requests"] += 1

    async def _en(self, rs: RouteState, job: Job) -> None:
        row = await store.get_summary(self.db, job.paper_id)
        if not row or row["status"] in ("ok", "fatal"):
            raise SkipForNow
        if row["summary_en"]:  # done by an earlier attempt: continue with ZH on the same model
            self.add(Job(**{**job.__dict__, "step": "zh", "family": row["family"], "not_before": 0.0,
                            "avoid_route": None}))
            raise SkipForNow

        route = rs.route
        md = await self.md.get(job.paper_id)
        if md is not None:
            text, truncated = prepare_input(md, budget_chars(route.context_tokens, route.max_output_tokens))
            source, messages = "full_text", en_messages(job.paper_id, job.title, text)
        else:
            old = date.fromisoformat(job.daily_date) < utc_today() - timedelta(days=MD_OLD_DAYS)
            waited = job.md_wait_hours
            if not old and (waited is None or waited < MD_WAIT_HOURS):  # usually published within hours
                if job.paper_id not in self.md_missed:
                    self.md_missed.add(job.paper_id)
                    await store.md_miss(self.db, job.paper_id)
                waiting = f" (waited {waited:.1f} of {MD_WAIT_HOURS} h)" if waited is not None else ""
                self.log(f"skip {job.paper_id}: markdown not published yet{waiting}")
                self.md.release(job.paper_id)
                raise SkipForNow
            if not job.abstract:
                raise PaperError("no markdown and no abstract")
            truncated = False
            source, messages = "abstract", en_messages(job.paper_id, job.title, job.abstract, abstract_only=True)

        await self._acquire(rs)
        comp = await chat(self.client, route, messages, first_chunk_timeout=self.timing.first_chunk_timeout,
                          total_timeout=self.timing.call_timeout)
        summary_en = check_en(comp.content, source)
        await store.save_en(self.db, job.paper_id, summary_en, route, source, truncated)
        if category := parse_category(strip_reasoning(comp.content)):  # missing → the classify pass fills it in
            await store.set_category(self.db, job.paper_id, *category)
            self.done["categorized"] += 1
        self.md.release(job.paper_id)
        self.log(f"ok   EN {job.paper_id} by {route.label} in {comp.seconds:.0f}s ({source})")
        self.zh[route.family].insert(0, Job(job.paper_id, job.daily_date, job.title, job.abstract,
                                            job.md_wait_hours, "zh", route.family))

    async def _zh(self, rs: RouteState, job: Job) -> None:
        row = await store.get_summary(self.db, job.paper_id)
        if (not row or row["status"] in ("ok", "fatal") or row["summary_zh"] or not row["summary_en"]
                or row["family"] != rs.route.family):
            raise SkipForNow
        await self._acquire(rs)
        comp = await chat(self.client, rs.route, zh_messages(job.title, row["summary_en"]),
                          extra=rs.route.translate_options,  # e.g. reasoning off: translating needs none
                          first_chunk_timeout=self.timing.first_chunk_timeout, total_timeout=self.timing.call_timeout)
        title_zh, summary_zh = check_zh(comp.content, row["summary_en"])
        await store.save_zh(self.db, job.paper_id, title_zh, summary_zh, rs.route)
        self.done["ok"] += 1
        self.log(f"ok   ZH {job.paper_id} by {rs.route.label} in {comp.seconds:.0f}s")
        if await store.check_ready(self.db, job.daily_date):
            self.log(f"date {job.daily_date} is ready")
