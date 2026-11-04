"""D1 access: Cloudflare's REST API in production, a local SQLite file for development and tests.

Both classes expose the same two async methods:
  query(sql, params) -> list of row dicts
  batch([(sql, params), ...]) -> list of row-dict lists, run as one transaction
SQL must use plain `?` placeholders and contain no literal '?' characters.
"""

from __future__ import annotations

import asyncio
import random
import sqlite3
from collections.abc import Sequence

import httpx

Statement = tuple[str, Sequence]


class D1Error(RuntimeError):
    pass


class LocalD1:
    def __init__(self, path: str):
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")  # D1 enforces foreign keys too
        self._lock = asyncio.Lock()

    async def query(self, sql: str, params: Sequence = ()) -> list[dict]:
        async with self._lock:
            return [dict(r) for r in self.conn.execute(sql, list(params)).fetchall()]

    async def batch(self, statements: Sequence[Statement]) -> list[list[dict]]:
        async with self._lock:
            out = []
            self.conn.execute("BEGIN")
            try:
                for sql, params in statements:
                    out.append([dict(r) for r in self.conn.execute(sql, list(params)).fetchall()])
                self.conn.execute("COMMIT")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            return out

    def executescript(self, script: str) -> None:
        self.conn.executescript(script)

    async def aclose(self) -> None:
        self.conn.close()


class RemoteD1:
    """POST /accounts/{account}/d1/database/{db}/query. The API only accepts string params, so numbers are
    sent as strings (SQLite column affinity turns them back into numbers) and NULLs are inlined into the SQL."""

    def __init__(self, account_id: str, database_id: str, token: str, client: httpx.AsyncClient | None = None):
        self.url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/d1/database/{database_id}/query"
        self.headers = {"Authorization": f"Bearer {token}"}
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(30, connect=15))

    async def query(self, sql: str, params: Sequence = ()) -> list[dict]:
        sql, params = encode(sql, params)
        result = await self._post({"sql": sql, "params": params})
        return result[0].get("results") or []

    async def batch(self, statements: Sequence[Statement]) -> list[list[dict]]:
        if not statements:
            return []
        body = {"batch": [dict(zip(("sql", "params"), encode(sql, params))) for sql, params in statements]}
        result = await self._post(body)
        return [r.get("results") or [] for r in result]

    async def _post(self, body: dict) -> list[dict]:
        last: Exception | None = None
        for attempt in range(4):
            try:
                r = await self.client.post(self.url, json=body, headers=self.headers)
            except httpx.HTTPError as e:
                last = e
            else:
                if r.status_code == 429 or r.status_code >= 500:
                    last = D1Error(f"HTTP {r.status_code}: {r.text[:300]}")
                else:
                    try:
                        data = r.json()
                    except ValueError:
                        raise D1Error(f"HTTP {r.status_code}: {r.text[:300]}") from None
                    if not data.get("success"):
                        raise D1Error(f"D1 query failed (HTTP {r.status_code}): {data.get('errors')}")
                    return data["result"]
            if attempt < 3:
                await asyncio.sleep(2**attempt + random.random())
        raise D1Error(f"D1 unavailable after 4 attempts: {last}")

    async def aclose(self) -> None:
        await self.client.aclose()


def _to_str(v) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    return v if isinstance(v, str) else str(v)


def encode(sql: str, params: Sequence) -> tuple[str, list[str]]:
    """Stringify params and replace the placeholders of None params with NULL."""
    params = list(params)
    if all(p is not None for p in params):
        return sql, [_to_str(p) for p in params]
    parts = sql.split("?")
    if len(parts) - 1 != len(params):
        raise ValueError(f"{len(parts) - 1} placeholders but {len(params)} params")
    out, kept = [parts[0]], []
    for p, rest in zip(params, parts[1:]):
        if p is None:
            out.append("NULL")
        else:
            out.append("?")
            kept.append(_to_str(p))
        out.append(rest)
    return "".join(out), kept
