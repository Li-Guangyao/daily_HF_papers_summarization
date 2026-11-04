"""OpenAI-compatible streaming chat client (NVIDIA, OpenRouter, Zhipu) and error classification."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass

import httpx

from .config import Route

QUOTA_WORDS = ("per day", "per-day", "daily", "quota", "free-models-per-day", "余额", "额度", "欠费", "每日", "今日")
GONE_WORDS = ("model not found", "does not exist", "no endpoints found", "not a valid model", "deprecated", "retired", "模型不存在")


class ProviderError(Exception):
    """The provider failed; says nothing about the paper. kind: rate_limit | quota | auth | gone | transient"""

    def __init__(self, kind: str, message: str, status: int | None = None, code: str | None = None,
                 retry_after: float | None = None):
        super().__init__(message)
        self.kind, self.status, self.code, self.retry_after = kind, status, code, retry_after


class PaperError(Exception):
    """The provider worked, but this request or its output is unusable (bad input, filtered, malformed)."""

    def __init__(self, message: str, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status, self.code = status, code


@dataclass
class Completion:
    content: str
    finish_reason: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    seconds: float


def _error_code(body: str) -> str | None:
    try:
        err = json.loads(body).get("error")
    except (ValueError, AttributeError):
        return None
    if isinstance(err, dict) and err.get("code") is not None:
        return str(err["code"])
    return None


def classify(status: int, body: str, retry_after: str | None = None) -> Exception:
    """Map an HTTP error response to ProviderError or PaperError."""
    text = body.lower()
    code = _error_code(body)
    msg = f"HTTP {status}: {body[:300]}"
    if status == 429:
        if any(w in text for w in QUOTA_WORDS):
            return ProviderError("quota", msg, status, code)
        try:
            wait = float(retry_after) if retry_after else None
        except ValueError:
            wait = None
        return ProviderError("rate_limit", msg, status, code, retry_after=wait)
    if status in (401, 403):
        return ProviderError("auth", msg, status, code)
    if status in (402, 404, 410) or any(w in text for w in GONE_WORDS):
        return ProviderError("gone", msg, status, code)
    if status in (408, 409) or status >= 500:
        return ProviderError("transient", msg, status, code)
    if status in (400, 413, 422):
        return PaperError(msg, status, code)
    return ProviderError("transient", msg, status, code)


def _request(route: Route, messages: list[dict], max_tokens: int, extra: dict | None = None) -> tuple[dict, dict]:
    headers = {"Authorization": f"Bearer {route.api_key}", "Content-Type": "application/json"}
    if route.provider == "openrouter":
        headers["X-Title"] = "HF Papers Digest"
    body = {"model": route.api_model, "messages": messages, "max_tokens": max_tokens, "stream": True,
            **route.request_options, **(extra or {})}
    return headers, body


async def chat(client: httpx.AsyncClient, route: Route, messages: list[dict], *, max_tokens: int | None = None,
               extra: dict | None = None, first_chunk_timeout: float = 120, total_timeout: float = 600) -> Completion:
    """Stream one completion. Reasoning deltas keep the connection alive but are not returned.
    `extra` is merged into the request body after the row's request_options."""
    headers, body = _request(route, messages, max_tokens or route.max_output_tokens, extra)
    timeout = httpx.Timeout(connect=15, read=first_chunk_timeout, write=60, pool=60)
    url = f"{route.base_url}/chat/completions"
    parts: list[str] = []
    finish = usage = None
    t0 = time.monotonic()
    try:
        async with asyncio.timeout(total_timeout):
            async with client.stream("POST", url, json=body, headers=headers, timeout=timeout) as resp:
                if resp.status_code != 200:
                    raw = (await resp.aread()).decode(errors="replace")
                    raise classify(resp.status_code, raw, resp.headers.get("retry-after"))
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue  # blank lines and SSE comments such as ": OPENROUTER PROCESSING"
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    chunk = json.loads(data)
                    if "error" in chunk:  # error reported mid-stream
                        err = chunk["error"]
                        status = err.get("code") if isinstance(err, dict) and isinstance(err.get("code"), int) else 500
                        raise classify(status, data)
                    for choice in chunk.get("choices") or []:
                        content = (choice.get("delta") or {}).get("content")
                        if content:
                            parts.append(content)
                        if choice.get("finish_reason"):
                            finish = choice["finish_reason"]
                    if chunk.get("usage"):
                        usage = chunk["usage"]
    except (ProviderError, PaperError):
        raise
    except TimeoutError:
        raise ProviderError("transient", f"no complete response within {total_timeout:.0f}s") from None
    except httpx.TimeoutException as e:
        raise ProviderError("transient", f"timeout: {type(e).__name__}") from None
    except (httpx.HTTPError, json.JSONDecodeError) as e:
        raise ProviderError("transient", f"{type(e).__name__}: {e}") from None

    seconds = time.monotonic() - t0
    if finish in ("sensitive", "content_filter"):
        raise PaperError(f"blocked by the provider's content filter (finish_reason={finish})")
    if finish == "length":
        raise PaperError("output hit max_tokens (finish_reason=length)")
    if finish is None:
        raise ProviderError("transient", "stream ended without a finish_reason")
    usage = usage or {}
    return Completion("".join(parts), finish, usage.get("prompt_tokens"), usage.get("completion_tokens"), seconds)


async def health_check(client: httpx.AsyncClient, route: Route, timeout: float = 60) -> None:
    """Raise unless the provider returns HTTP 200 and a first chunk within `timeout` seconds."""
    headers, body = _request(route, [{"role": "user", "content": "Reply with OK"}], 16)
    try:
        async with asyncio.timeout(timeout):
            async with client.stream("POST", f"{route.base_url}/chat/completions", json=body, headers=headers,
                                     timeout=httpx.Timeout(timeout, connect=15)) as resp:
                if resp.status_code != 200:
                    raw = (await resp.aread()).decode(errors="replace")
                    raise classify(resp.status_code, raw, resp.headers.get("retry-after"))
                async for line in resp.aiter_lines():
                    if line.startswith("data:"):
                        return
        raise ProviderError("transient", "empty response")
    except (ProviderError, PaperError):
        raise
    except TimeoutError:
        raise ProviderError("transient", f"no response within {timeout:.0f}s") from None
    except httpx.HTTPError as e:
        raise ProviderError("transient", f"{type(e).__name__}: {e}") from None
