"""A lease in D1 so only one process uses the API keys at a time (system_design_improved.md §5.10)."""

from __future__ import annotations

import asyncio
import os
import socket
import time
from collections.abc import Callable

LEASE = "+300 seconds"
RENEW_EVERY = 60


def holder_id() -> str:
    if os.environ.get("GITHUB_ACTIONS"):
        return f"actions:{os.environ.get('GITHUB_RUN_ID', 'unknown')}"
    return f"local:{socket.gethostname()}:{os.getpid()}"


class RunLock:
    def __init__(self, db, holder: str | None = None):
        self.db = db
        self.holder = holder or holder_id()
        self._task: asyncio.Task | None = None

    async def try_acquire(self) -> bool:
        rows = await self.db.query(
            "INSERT INTO run_locks (name, holder, expires_at) VALUES ('pipeline', ?, datetime('now', ?)) "
            "ON CONFLICT(name) DO UPDATE SET holder = excluded.holder, waiting = NULL, expires_at = excluded.expires_at "
            "WHERE run_locks.expires_at < datetime('now') OR run_locks.holder = excluded.holder "
            "RETURNING holder", [self.holder, LEASE])
        return bool(rows) and rows[0]["holder"] == self.holder

    async def current_holder(self) -> str | None:
        rows = await self.db.query("SELECT holder FROM run_locks WHERE name = 'pipeline' "
                                   "AND expires_at >= datetime('now')")
        return rows[0]["holder"] if rows else None

    async def acquire(self, max_wait_s: float | None, poll_s: float, ask_holder_to_yield: bool, log=print) -> bool:
        """Wait for the lock. A scheduled run asks the holder (a local backfill) to step aside."""
        deadline = None if max_wait_s is None else time.monotonic() + max_wait_s
        announced = False
        while True:
            if await self.try_acquire():
                return True
            if ask_holder_to_yield:
                await self.db.query("UPDATE run_locks SET waiting = ? WHERE name = 'pipeline' "
                                    "AND (waiting IS NULL OR waiting = ?)", [self.holder, self.holder])
            if not announced:
                log(f"waiting for the run lock held by {await self.current_holder()}")
                announced = True
            if deadline is not None and time.monotonic() > deadline:
                return False
            await asyncio.sleep(poll_s)

    def keep_alive(self, on_yield_request: Callable[[], None] | None = None) -> None:
        """Renew the lease every minute; call on_yield_request when a scheduled run is waiting."""

        async def loop():
            while True:
                await asyncio.sleep(RENEW_EVERY)
                try:
                    rows = await self.db.query(
                        "UPDATE run_locks SET expires_at = datetime('now', ?) WHERE name = 'pipeline' AND holder = ? "
                        "RETURNING waiting", [LEASE, self.holder])
                except Exception as e:
                    print(f"warning: could not renew the run lock: {e}")
                    continue
                if rows and rows[0]["waiting"] and on_yield_request:
                    on_yield_request()

        self._task = asyncio.create_task(loop())

    async def release(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
        try:
            await self.db.query("DELETE FROM run_locks WHERE name = 'pipeline' AND holder = ?", [self.holder])
        except Exception as e:
            print(f"warning: could not release the run lock (it expires within 5 minutes): {e}")
