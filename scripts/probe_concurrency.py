#!/usr/bin/env python3
"""Probe a Zhipu GLM model's concurrency limit, failure rate, latency and speed.

For each concurrency level, the script keeps that many streaming requests in
flight until --requests calls have finished, then reports success rate, error
codes, latency and tokens/s. It stops raising the level once the failure rate
passes --stop-failure-rate.

Usage:
  export ZHIPU_API_KEY=...            # never pass the key on the command line or commit it
  python scripts/probe_concurrency.py                                # glm-4.7-flash, levels 1,2,3,5,8
  python scripts/probe_concurrency.py --levels 1,2,4 --requests 12
  python scripts/probe_concurrency.py --paper 2610.27999 --input-chars 60000 --max-tokens 4000
  python scripts/probe_concurrency.py --thinking disabled             # compare with thinking off

Raw results go to probe_results/<model>-<timestamp>.jsonl, plus a summary JSON.
"""

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
DEFAULT_PROMPT = "Explain in about 300 words how the attention mechanism in transformers works."


@dataclass
class Result:
    level: int
    index: int
    status: int | None = None  # HTTP status; None means the request never got a response
    ok: bool = False
    error_code: str | None = None
    error_message: str | None = None
    ttft: float | None = None  # seconds until the first token (reasoning or content)
    latency: float | None = None  # seconds until the stream ended
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    reasoning_chars: int = 0
    content_chars: int = 0
    finish_reason: str | None = None


def parse_error(raw: str) -> tuple[str, str]:
    try:
        err = json.loads(raw).get("error", {})
        return str(err.get("code", "")), str(err.get("message", ""))[:300]
    except (json.JSONDecodeError, AttributeError):
        return "", raw[:300]


async def one_request(client: httpx.AsyncClient, args, messages: list[dict], level: int, index: int) -> Result:
    res = Result(level, index)
    body = {
        "model": args.model,
        "messages": messages,
        "max_tokens": args.max_tokens,
        "temperature": args.temperature,
        "stream": True,
    }
    if args.thinking:
        body["thinking"] = {"type": args.thinking}

    t0 = time.perf_counter()
    try:
        async with client.stream("POST", URL, json=body) as resp:
            res.status = resp.status_code
            if resp.status_code != 200:
                res.error_code, res.error_message = parse_error((await resp.aread()).decode(errors="replace"))
                return res
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                if "error" in chunk:  # error reported mid-stream
                    res.error_code, res.error_message = parse_error(data)
                    return res
                choice = (chunk.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                reasoning, content = delta.get("reasoning_content") or "", delta.get("content") or ""
                if (reasoning or content) and res.ttft is None:
                    res.ttft = time.perf_counter() - t0
                res.reasoning_chars += len(reasoning)
                res.content_chars += len(content)
                if choice.get("finish_reason"):
                    res.finish_reason = choice["finish_reason"]
                if usage := chunk.get("usage"):
                    res.prompt_tokens = usage.get("prompt_tokens")
                    res.completion_tokens = usage.get("completion_tokens")
        res.ok = res.finish_reason in ("stop", "length")
        if not res.ok:
            res.error_code = f"finish:{res.finish_reason}"  # e.g. 'sensitive' (content filter) or a cut stream
    except httpx.TimeoutException:
        res.error_code = "timeout"
    except (httpx.HTTPError, json.JSONDecodeError) as e:
        res.error_code, res.error_message = type(e).__name__, str(e)[:300]
    finally:
        res.latency = time.perf_counter() - t0
    return res


def describe(res: Result) -> str:
    if res.ok:
        toks = res.completion_tokens if res.completion_tokens is not None else "?"
        return (f"ok   {res.latency:6.1f}s  ttft {res.ttft or 0:5.1f}s  {toks} tokens  "
                f"reasoning/content chars {res.reasoning_chars}/{res.content_chars}  finish={res.finish_reason}")
    return f"FAIL {res.latency:6.1f}s  http={res.status} code={res.error_code} {res.error_message or ''}"


async def run_level(client, args, messages, level: int, out) -> tuple[list[Result], float]:
    n = args.requests or max(5, 3 * level)
    pending = iter(range(n))
    results: list[Result] = []

    async def worker():
        for i in pending:  # the shared iterator hands each index to exactly one worker
            res = await one_request(client, args, messages, level, i)
            results.append(res)
            out.write(json.dumps(asdict(res)) + "\n")
            out.flush()
            print(f"  [{level}x] #{i + 1:>3}/{n}  {describe(res)}")

    t0 = time.perf_counter()
    await asyncio.gather(*(worker() for _ in range(level)))
    return results, time.perf_counter() - t0


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    return values[min(len(values) - 1, round(p * (len(values) - 1)))]


def summarize(level: int, results: list[Result], wall: float) -> dict:
    ok = [r for r in results if r.ok]
    latencies = [r.latency for r in ok]
    ttfts = [r.ttft for r in ok if r.ttft is not None]
    # Generation speed per request: output tokens over the time after the first token.
    speeds = [
        r.completion_tokens / (r.latency - r.ttft)
        for r in ok
        if r.completion_tokens and r.ttft is not None and r.latency > r.ttft
    ]
    total_tokens = sum(r.completion_tokens or 0 for r in ok)
    return {
        "level": level,
        "requests": len(results),
        "ok": len(ok),
        "failure_rate": round(1 - len(ok) / len(results), 3) if results else None,
        "http_429": sum(r.status == 429 for r in results),
        # ok responses where thinking used the whole token budget and no answer text came back
        "empty_content": sum(r.content_chars == 0 for r in ok),
        "errors": dict(Counter(f"{r.status}/{r.error_code}" for r in results if not r.ok)),
        "error_examples": {r.error_code: r.error_message for r in results if not r.ok and r.error_message},
        "latency_p50": percentile(latencies, 0.5),
        "latency_p95": percentile(latencies, 0.95),
        "ttft_mean": statistics.mean(ttfts) if ttfts else None,
        "tokens_per_s_per_request": statistics.mean(speeds) if speeds else None,
        "tokens_per_s_total": total_tokens / wall if wall else None,
        "requests_per_min": len(ok) / wall * 60 if wall else None,
        "wall_s": wall,
    }


def fmt(v, spec=".1f") -> str:
    return "-" if v is None else format(v, spec)


def print_table(summaries: list[dict]) -> None:
    print(f"\n{'level':>5} {'ok/n':>7} {'fail%':>6} {'429':>4} {'p50 s':>6} {'p95 s':>6} "
          f"{'ttft s':>6} {'tok/s/req':>9} {'tok/s all':>9} {'req/min':>7}  errors")
    for s in summaries:
        print(f"{s['level']:>5} {s['ok']:>3}/{s['requests']:<3} {fmt(s['failure_rate'] * 100, '.0f'):>6} "
              f"{s['http_429']:>4} {fmt(s['latency_p50']):>6} {fmt(s['latency_p95']):>6} "
              f"{fmt(s['ttft_mean']):>6} {fmt(s['tokens_per_s_per_request']):>9} "
              f"{fmt(s['tokens_per_s_total']):>9} {fmt(s['requests_per_min']):>7}  {s['errors'] or ''}")
    for s in summaries:
        for code, msg in s["error_examples"].items():
            print(f"  level {s['level']} {code}: {msg}")
        if s["empty_content"]:
            print(f"  level {s['level']}: {s['empty_content']} ok responses had no answer text "
                  "(thinking used all max_tokens; raise --max-tokens or use --thinking disabled)")


def build_messages(args) -> list[dict]:
    if args.paper or args.input_file:
        if args.input_file:
            text = Path(args.input_file).read_text(encoding="utf-8")
        else:
            r = httpx.get(f"https://huggingface.co/papers/{args.paper}.md", timeout=30, follow_redirects=True)
            r.raise_for_status()
            text = r.text
        text = text[: args.input_chars]
        user = f"Summarize this paper in detail (about 1,500 words).\n\n<paper>\n{text}\n</paper>"
    else:
        user = args.prompt
    return [{"role": "system", "content": "You are a helpful assistant."}, {"role": "user", "content": user}]


async def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", default="glm-4.7-flash")
    p.add_argument("--levels", default="1,2,3,5,8", help="comma-separated concurrency levels to try in order")
    p.add_argument("--requests", type=int, default=0, help="requests per level (default: max(5, 3 x level))")
    p.add_argument("--max-tokens", type=int, default=2000, help="includes thinking tokens")
    p.add_argument("--temperature", type=float, default=0.3)
    p.add_argument("--thinking", choices=["enabled", "disabled"], help="send GLM's thinking switch (omitted by default)")
    p.add_argument("--prompt", default=DEFAULT_PROMPT)
    p.add_argument("--paper", help="use a real paper as input: HF paper id, e.g. 2610.27999")
    p.add_argument("--input-file", help="use a local text/markdown file as input")
    p.add_argument("--input-chars", type=int, default=60000, help="truncate --paper/--input-file to this many chars")
    p.add_argument("--timeout", type=float, default=600, help="seconds per request")
    p.add_argument("--cooldown", type=float, default=20, help="seconds to wait between levels")
    p.add_argument("--stop-failure-rate", type=float, default=0.5, help="stop raising the level above this")
    p.add_argument("--out-dir", default="probe_results")
    args = p.parse_args()

    key = os.environ.get("ZHIPU_API_KEY")
    if not key:
        sys.exit("Set ZHIPU_API_KEY in the environment first.")

    messages = build_messages(args)
    levels = [int(x) for x in args.levels.split(",")]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / f"{args.model}-{stamp}.jsonl"

    print(f"model={args.model} levels={levels} max_tokens={args.max_tokens} "
          f"input_chars={sum(len(m['content']) for m in messages)} thinking={args.thinking or 'default'}")

    summaries = []
    limits = httpx.Limits(max_connections=max(levels) + 2, max_keepalive_connections=max(levels) + 2)
    timeout = httpx.Timeout(args.timeout, connect=15)
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(headers=headers, timeout=timeout, limits=limits) as client:
        with raw_path.open("w", encoding="utf-8") as out:
            for n, level in enumerate(levels):
                if n:
                    print(f"cooling down {args.cooldown:.0f}s ...")
                    await asyncio.sleep(args.cooldown)
                print(f"\n== concurrency {level} ==")
                results, wall = await run_level(client, args, messages, level, out)
                summaries.append(summarize(level, results, wall))
                if summaries[-1]["failure_rate"] > args.stop_failure_rate:
                    print(f"failure rate above {args.stop_failure_rate:.0%}; not trying higher levels")
                    break

    print_table(summaries)
    clean = [s["level"] for s in summaries if s["failure_rate"] == 0]
    if clean:
        print(f"\nHighest level with no failures: {max(clean)}")
    summary_path = raw_path.with_suffix(".summary.json")
    summary_path.write_text(json.dumps({"args": {k: v for k, v in vars(args).items()}, "levels": summaries}, indent=2))
    print(f"Raw results: {raw_path}\nSummary:     {summary_path}")


if __name__ == "__main__":
    asyncio.run(main())
