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


class OutputCheckError(ValueError):
    pass


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
        raise OutputCheckError("no level-2 headings")
    body = text[start:].strip()
    n_h2 = len(headings(body))
    words = len(re.findall(r"\w+", body))
    min_h2, min_words = (MIN_H2, MIN_EN_WORDS) if source == "full_text" else (1, MIN_ABSTRACT_WORDS)
    if n_h2 < min_h2:
        raise OutputCheckError(f"only {n_h2} level-2 headings")
    if words < min_words:
        raise OutputCheckError(f"too short: {words} words")
    if display_math_count(body) % 2:
        raise OutputCheckError("unbalanced $$ math block")
    return body if source == "full_text" else f"{ABSTRACT_NOTICE}\n\n{body}"


def check_zh(raw: str, summary_en: str) -> tuple[str, str]:
    """Return (title_zh, summary_zh), or raise OutputCheckError."""
    text = strip_reasoning(raw)
    title = re.search(r"<title_zh>(.*?)</title_zh>", text, re.S)
    body = re.search(r"<summary_zh>(.*?)</summary_zh>", text, re.S)
    if not title or not body:
        raise OutputCheckError("missing <title_zh> or <summary_zh>")
    title_zh, summary_zh = title.group(1).strip(), body.group(1).strip()
    if not title_zh:
        raise OutputCheckError("empty title_zh")
    n_en, n_zh = len(headings(summary_en)), len(headings(summary_zh))
    if n_zh != n_en:
        raise OutputCheckError(f"{n_zh} level-2 headings, expected {n_en}")
    if display_math_count(summary_zh) != display_math_count(summary_en):
        raise OutputCheckError("display formulas were not carried over unchanged")
    if len(summary_zh) < MIN_ZH_RATIO * len(summary_en):
        raise OutputCheckError(f"too short: {len(summary_zh)} chars for {len(summary_en)} English chars")
    return title_zh, summary_zh
