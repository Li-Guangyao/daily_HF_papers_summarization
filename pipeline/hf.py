"""Hugging Face daily papers: the list API and the per-paper markdown."""

from __future__ import annotations

import asyncio
import json
import random

import httpx

LIST_URL = "https://huggingface.co/api/daily_papers"
MD_URL = "https://huggingface.co/papers/{id}.md"
PAGE_SIZE = 100
HEADERS = {"User-Agent": "hf-papers-digest/1.0"}


class HFError(RuntimeError):
    pass


async def _get(client: httpx.AsyncClient, url: str, params: dict | None = None) -> httpx.Response:
    last: Exception | None = None
    for attempt in range(4):
        try:
            r = await client.get(url, params=params, headers=HEADERS, timeout=30, follow_redirects=True)
        except httpx.HTTPError as e:
            last = e
        else:
            if r.status_code != 429 and r.status_code < 500:
                return r
            last = HFError(f"HTTP {r.status_code} for {url}")
        if attempt < 3:
            await asyncio.sleep(5 * 2**attempt + random.random())
    raise HFError(f"giving up on {url}: {last}")


async def fetch_daily_list(client: httpx.AsyncClient, day: str) -> list[dict]:
    """All papers listed for `day`. Never send sort=trending: it makes the API ignore `date`."""
    items: list[dict] = []
    page = 0
    while True:
        r = await _get(client, LIST_URL, {"date": day, "limit": PAGE_SIZE, "p": page})
        if r.status_code != 200:
            raise HFError(f"daily_papers {day} page {page}: HTTP {r.status_code}")
        batch = r.json()
        items.extend(batch)
        if len(batch) < PAGE_SIZE:
            return items
        page += 1


async def fetch_markdown(client: httpx.AsyncClient, paper_id: str) -> str | None:
    """The paper as markdown, or None when HF has no markdown for it (yet)."""
    r = await _get(client, MD_URL.format(id=paper_id))
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise HFError(f"{paper_id}.md: HTTP {r.status_code}")
    return r.text


def to_row(item: dict, day: str) -> dict:
    """Map one daily_papers item to `papers` columns."""
    p = item.get("paper") or {}
    keywords = p.get("ai_keywords")
    return {
        "id": p["id"],
        "daily_date": day,
        "title": (p.get("title") or item.get("title") or "").strip(),
        "authors": json.dumps([a["name"] for a in p.get("authors", []) if a.get("name")], ensure_ascii=False),
        "abstract": p.get("summary") or item.get("summary"),
        "published_at": p.get("publishedAt"),
        "thumbnail": item.get("thumbnail"),
        "project_page": p.get("projectPage"),
        "github_repo": p.get("githubRepo"),
        "github_stars": p.get("githubStars"),
        "upvotes": p.get("upvotes") or 0,
        "num_comments": item.get("numComments"),
        "hf_ai_summary": p.get("ai_summary"),
        "hf_ai_keywords": json.dumps(keywords, ensure_ascii=False) if keywords else None,
        "hf_ai_summary_model": p.get("ai_summary_model"),
        "withdrawn_at": p.get("withdrawnAt"),
    }
