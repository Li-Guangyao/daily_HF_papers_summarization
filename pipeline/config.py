"""Environment settings, date helpers and the provider rows (`llm_models`)."""

from __future__ import annotations

import glob
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .d1 import LocalD1, RemoteD1

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = ROOT / "migrations"


def load_env_file(path: str | os.PathLike = ".env") -> None:
    """Load KEY=VALUE lines into os.environ. Variables that are already set win."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        os.environ.setdefault(key, value.strip().strip('"').strip("'"))


def open_db(local_path: str | None = None) -> LocalD1 | RemoteD1:
    """LOCAL_D1_PATH (or --local-db) selects a SQLite file; otherwise the remote D1 database is used.

    LOCAL_D1_PATH=auto finds the database that `wrangler d1 migrations apply --local` created.
    """
    path = local_path or os.environ.get("LOCAL_D1_PATH")
    if path == "auto":
        found = sorted(p for p in glob.glob(str(ROOT / ".wrangler/state/v3/d1/miniflare-D1DatabaseObject/*.sqlite"))
                       if not p.endswith("metadata.sqlite"))
        if not found:
            sys.exit("LOCAL_D1_PATH=auto: no local D1 found. Run `npm run db:migrate:local` first.")
        path = found[0]
    if path:
        return LocalD1(path)
    missing = [k for k in ("CF_API_TOKEN", "CF_ACCOUNT_ID", "D1_DATABASE_ID") if not os.environ.get(k)]
    if missing:
        sys.exit(f"Missing {', '.join(missing)}. Set them in .env, or use LOCAL_D1_PATH for a local database.")
    return RemoteD1(os.environ["CF_ACCOUNT_ID"], os.environ["D1_DATABASE_ID"], os.environ["CF_API_TOKEN"])


def migration_sql() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(MIGRATIONS_DIR.glob("*.sql")))


# --- dates -------------------------------------------------------------------------------------

def utc_today() -> date:
    return datetime.now(timezone.utc).date()


def previous_workdays(today: date, n: int = 2) -> list[str]:
    """The n workdays before `today`, oldest first. Monday → [Thursday, Friday]."""
    out: list[str] = []
    d = today
    while len(out) < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            out.append(d.isoformat())
    return sorted(out)


def expand_dates(spec: str, include_weekends: bool = False) -> list[str]:
    """'2026-10-20', '2026-10-01..2026-10-21' or a comma-separated mix of both."""
    out: set[str] = set()
    for part in filter(None, (p.strip() for p in spec.split(","))):
        if ".." in part:
            start, end = (date.fromisoformat(x.strip()) for x in part.split("..", 1))
            d = start
            while d <= end:
                if include_weekends or d.weekday() < 5:
                    out.add(d.isoformat())
                d += timedelta(days=1)
        else:
            out.add(date.fromisoformat(part).isoformat())
    return sorted(out)


# --- provider rows -----------------------------------------------------------------------------

@dataclass
class Route:
    """One enabled `llm_models` row: a model served by one provider."""

    id: int
    family: str
    display_name: str
    provider: str
    base_url: str
    api_model: str
    api_key_env: str
    rpm_limit: int
    max_concurrency: int
    context_tokens: int
    max_output_tokens: int
    request_options: dict
    translate_options: dict = field(default_factory=dict)  # extra options for the ZH step only
    api_key: str | None = None

    @property
    def label(self) -> str:
        return f"#{self.id} {self.provider}/{self.api_model}"


ROUTE_COLUMNS = (
    "id, family, display_name, provider, base_url, api_model, api_key_env, rpm_limit, "
    "max_concurrency, context_tokens, max_output_tokens, request_options, translate_options"
)


def route_from_row(r: dict) -> Route:
    return Route(
        id=int(r["id"]),
        family=r["family"],
        display_name=r["display_name"],
        provider=r["provider"],
        base_url=r["base_url"].rstrip("/"),
        api_model=r["api_model"],
        api_key_env=r["api_key_env"],
        rpm_limit=int(r["rpm_limit"]),
        max_concurrency=max(1, int(r["max_concurrency"])),
        context_tokens=int(r["context_tokens"]),
        max_output_tokens=int(r["max_output_tokens"]),
        request_options=json.loads(r["request_options"] or "{}"),
        translate_options=json.loads(r.get("translate_options") or "{}"),
    )


async def load_routes(db, family: str | None = None) -> tuple[list[Route], list[Route]]:
    """Enabled rows split into (has an API key in the environment, key missing)."""
    rows = await db.query(f"SELECT {ROUTE_COLUMNS} FROM llm_models WHERE is_enabled = 1 ORDER BY id")
    usable, no_key = [], []
    for r in rows:
        route = route_from_row(r)
        if family and route.family != family:
            continue
        route.api_key = os.environ.get(route.api_key_env) or None
        (usable if route.api_key else no_key).append(route)
    return usable, no_key
