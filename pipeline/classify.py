"""Category pass: classify papers that have no category yet from their title + abstract.

New papers usually get their category from the English walkthrough (one request does both). This pass covers
papers stored before categories existed, walkthroughs whose output had no valid tag, and fatal or withdrawn papers.
It runs at the end of every pipeline run, so the existing history is classified on the next run.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable

import httpx

from . import store
from .llm import PaperError, ProviderError, chat
from .markdown import strip_reasoning
from .pool import RouteState, Timing
from .prompts import classify_messages
from .taxonomy import parse_category

PER_RUN_LIMIT = 500   # plenty for a day; the rest of a large backlog continues next run
CONCURRENCY = 4
MAX_TOKENS = 4000     # the answer is one short tag; leave room for models that still reason


async def classify_missing(db, client: httpx.AsyncClient, states: list[RouteState], timing: Timing, *,
                           log: Callable[[str], None], stopping: Callable[[], bool] = lambda: False,
                           limit: int = PER_RUN_LIMIT, concurrency: int = CONCURRENCY) -> Counter:
    """Returns Counter(ok=…, failed=…, skipped=…)."""
    counts: Counter = Counter()
    alive = [s for s in states if s.alive]
    if not alive:
        return counts
    rows = await store.unclassified(db, limit)
    if not rows:
        return counts
    log(f"classify {len(rows)} papers without a category")
    sem = asyncio.Semaphore(concurrency)

    async def one(i: int, row: dict) -> None:
        async with sem:
            if stopping():
                counts["skipped"] += 1
                return
            messages = classify_messages(row["title"], row["abstract"])
            order = alive[i % len(alive):] + alive[:i % len(alive)]  # spread the work, fail over in turn
            for rs in order:
                if not rs.alive:
                    continue
                await rs.limiter.acquire()
                try:
                    comp = await chat(client, rs.route, messages, max_tokens=min(MAX_TOKENS, rs.route.max_output_tokens),
                                      extra=rs.route.translate_options,  # reasoning off where the row says how
                                      first_chunk_timeout=timing.first_chunk_timeout,
                                      total_timeout=timing.call_timeout)
                except ProviderError as e:
                    if e.kind == "rate_limit":
                        await asyncio.sleep(e.retry_after or timing.rate_limit_pause)
                    continue  # try the next provider; the paper isn't to blame
                except PaperError as e:
                    await _failed(row["id"], f"{rs.route.label}: {e}")
                    return
                category = parse_category(strip_reasoning(comp.content))
                if category:
                    await store.set_category(db, row["id"], *category)
                    counts["ok"] += 1
                else:
                    await _failed(row["id"], f"{rs.route.label}: no valid <category> in {comp.content[:120]!r}")
                return
            counts["skipped"] += 1  # every provider failed this time; try again next run

    async def _failed(paper_id: str, message: str) -> None:
        counts["failed"] += 1
        log(f"classify failed {paper_id}: {message[:160]}")
        await store.category_failure(db, paper_id)
        await store.log_error(db, "classify", paper_id, message)

    await asyncio.gather(*(one(i, r) for i, r in enumerate(rows)))
    log(f"classify done: {counts['ok']} ok, {counts['failed']} failed, {counts['skipped']} left for next run")
    return counts
