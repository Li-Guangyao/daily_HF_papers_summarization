"""Preparing paper markdown for the LLM, and checking what the LLM returns."""

from __future__ import annotations

import re

from .taxonomy import strip_category

REFERENCES_RE = re.compile(r"^#{1,3}\s*(?:[\dIVX]+(?:\.\d+)*\.?\s*)?(?:References|Bibliography)\b", re.I | re.M)
IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
THINK_RE = re.compile(r"<think>.*?</think>", re.S)
H2_RE = re.compile(r"^##\s+(.+?)\s*#*\s*$", re.M)

ABSTRACT_NOTICE = "> Based on the abstract only; the full text was unavailable."
MIN_H2 = 3              # the structure is free, but it must have some
MIN_EN_WORDS = 900      # full text: a beginner-level explanation is long
MIN_ABSTRACT_WORDS = 250
MIN_ZH_RATIO = 0.25     # Chinese needs far fewer characters than English for the same content


FENCE_RE = re.compile(r"^```[\w-]*[ \t]*\n(.*?)\n?```\s*$", re.S)
TITLE_PREFIX_RE = re.compile(r"^(?:#+\s*|\*\*|中文标题[:：]\s*|标题[:：]\s*|title_zh[:：]\s*)+", re.I)


class OutputCheckError(ValueError):
    """The output is unusable. `raw` keeps what the model returned, for the error log."""

    def __init__(self, message: str, raw: str | None = None):
        super().__init__(message)
        self.raw = raw


def snippet(raw: str, head: int = 250, tail: int = 150) -> str:
    """The start and end of a model output on one line, for logs."""
    text = raw.strip().replace("\n", "\\n")
    return text if len(text) <= head + tail + 5 else f"{text[:head]} … {text[-tail:]}"


def unfence(text: str) -> str:
    """Drop a ``` fence that wraps the whole text."""
    m = FENCE_RE.match(text.strip())
    return m.group(1) if m else text


def budget_chars(context_tokens: int, max_output_tokens: int) -> int:
    """How many characters of paper fit, at a cautious 3 characters per token."""
    return max(20_000, (context_tokens - max_output_tokens - 3_000) * 3)


def prepare_input(md: str, budget: int) -> tuple[str, bool]:
    """Strip HF's header, references, appendix and images; truncate to `budget` characters."""
    marker = md.find("Markdown Content:")
    if 0 <= marker < 3000:
        md = md[marker + len("Markdown Content:"):]
    refs = REFERENCES_RE.search(md)
    if refs and refs.start() > len(md) * 0.2:  # ignore a "References" entry in an early table of contents
        md = md[: refs.start()]
    md = IMAGE_RE.sub("", md)
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    if len(md) > budget:
        return md[:budget].rstrip() + "\n\n[TRUNCATED]", True
    return md, False


def strip_reasoning(text: str) -> str:
    text = THINK_RE.sub("", text)
    if "</think>" in text:  # an unclosed opening tag was cut off by the provider
        text = text.split("</think>", 1)[1]
    return text.strip()


def headings(text: str) -> list[str]:
    return [h.strip() for h in H2_RE.findall(text)]


def display_math_count(text: str) -> int:
    return text.count("$$")


def check_en(raw: str, source: str) -> str:
    """Return the cleaned English explanation, or raise OutputCheckError.

    The structure is up to the model; we only require a few sections, enough length and balanced $$ blocks."""
    text = strip_category(strip_reasoning(raw))
    start = text.find("## ")
    if start == -1:
        raise OutputCheckError("no level-2 headings", raw)
    body = text[start:].strip()
    n_h2 = len(headings(body))
    words = len(re.findall(r"\w+", body))
    min_h2, min_words = (MIN_H2, MIN_EN_WORDS) if source == "full_text" else (1, MIN_ABSTRACT_WORDS)
    if n_h2 < min_h2:
        raise OutputCheckError(f"only {n_h2} level-2 headings", raw)
    if words < min_words:
        raise OutputCheckError(f"too short: {words} words", raw)
    if display_math_count(body) % 2:
        raise OutputCheckError("unbalanced $$ math block", raw)
    return body if source == "full_text" else f"{ABSTRACT_NOTICE}\n\n{body}"


def _title_from_lines(text: str) -> str | None:
    """A Chinese title written as a plain line (e.g. "# 标题" or "标题：…") instead of inside <title_zh>."""
    for line in reversed([l.strip() for l in text.splitlines() if l.strip()]):
        title = TITLE_PREFIX_RE.sub("", line).strip().strip("*").strip()
        if title and len(title) <= 200 and not title.startswith("<"):
            return title
    return None


def check_zh(raw: str, summary_en: str) -> tuple[str | None, str]:
    """Return (title_zh, summary_zh), or raise OutputCheckError.

    The tags are read leniently: a missing </summary_zh>, a ``` fence around everything, or no tags at all (the
    translation then starts at its first level-2 heading) are accepted. A missing title is allowed (None); the
    content checks stay strict."""
    text = unfence(strip_reasoning(raw))
    title_m = re.search(r"<title_zh>(.*?)</title_zh>", text, re.S)
    title_zh = title_m.group(1).strip() if title_m else None
    body_m = re.search(r"<summary_zh>(.*?)(?:</summary_zh>|$)", text, re.S)
    if body_m:
        summary_zh = body_m.group(1)
    else:
        rest = text[title_m.end():] if title_m else text
        start = rest.find("## ")
        if start == -1:
            raise OutputCheckError("missing <summary_zh> and no level-2 headings", raw)
        summary_zh = rest[start:]
        title_zh = title_zh or _title_from_lines(rest[:start])
    summary_zh = unfence(summary_zh.strip()).strip()
    n_en, n_zh = len(headings(summary_en)), len(headings(summary_zh))
    if n_zh != n_en:
        raise OutputCheckError(f"{n_zh} level-2 headings, expected {n_en}", raw)
    if display_math_count(summary_zh) != display_math_count(summary_en):
        raise OutputCheckError("display formulas were not carried over unchanged", raw)
    if len(summary_zh) < MIN_ZH_RATIO * len(summary_en):
        raise OutputCheckError(f"too short: {len(summary_zh)} chars for {len(summary_en)} English chars", raw)
    return title_zh or None, summary_zh
