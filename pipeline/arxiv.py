"""arXiv's HTML version of a paper (https://arxiv.org/html/<id>), converted to markdown.

Used when Hugging Face has no markdown for a paper. arXiv builds these pages from the LaTeX source with LaTeXML, so
they keep the structure (sections, tables) and every formula's LaTeX (`<math alttext="…">`). Papers submitted
without LaTeX source, or whose conversion failed, have no HTML version.

Only the standard library is used: html.parser walks the page and writes markdown.
"""

from __future__ import annotations

import asyncio
import re
from html.parser import HTMLParser

import httpx

from .hf import HFError, _get

HTML_URL = "https://arxiv.org/html/{id}"
CONCURRENCY = 2          # be gentle with arXiv: few requests, one or two at a time
PAUSE_S = 1.0            # pause after each request
MIN_CHARS = 2_000        # less than this is not a usable full text (error page, failed conversion)

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
SKIP_TAGS = {"script", "style", "noscript", "nav", "header", "footer", "button", "svg", "head", "form", "select"}
SKIP_CLASSES = ("ltx_bibliography", "ltx_appendix", "ltx_authors", "ltx_dates", "ltx_TOC", "ltx_page_footer",
                "ltx_page_header", "ltx_tag_equation", "ltx_role_affiliation", "ltx_keywords", "ltx_acknowledgements",
                "ltx_classification")
HEADING_LEVEL = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 4, "h6": 4}
BLOCK_TAGS = {"p", "div", "section", "article", "figure", "figcaption", "blockquote", "ul", "ol", "dl", "dd", "dt"}


class _Converter(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[str] = []          # one action per open non-void element
        self.skip = 0                       # > 0 while inside an element we drop
        self.in_article = 0
        self.found_article = False
        self.heading: list[str] | None = None
        self.tables: list[list[list[str]]] = []   # rows of cells, for each open data table
        self.cell: list[str] | None = None

    # --- output helpers ----------------------------------------------------------------------

    def _emit(self, text: str) -> None:
        if self.cell is not None:
            self.cell.append(text)
        elif self.heading is not None:
            self.heading.append(text)
        else:
            self.out.append(text)

    def _block(self) -> None:
        if self.cell is None and self.heading is None:
            self.out.append("\n\n")

    # --- parser callbacks --------------------------------------------------------------------

    def handle_starttag(self, tag, attrs):
        if tag in VOID:
            if tag == "br" and not self.skip and self.in_article:
                self._emit(" " if self.cell is not None else "\n")
            return
        a = dict(attrs)
        classes = a.get("class") or ""
        if tag == "article" and "ltx_document" in classes:
            self.in_article += 1
            self.found_article = True
            self.stack.append("article")
            return
        if not self.in_article:          # page chrome around the paper; its text is ignored
            self.stack.append("outside")
            return
        if self.skip or tag in SKIP_TAGS or any(c in classes for c in SKIP_CLASSES):
            self.skip += 1
            self.stack.append("skip")
            return
        if tag == "math":
            tex = (a.get("alttext") or "").strip()
            if tex:
                if a.get("display") == "block" and self.cell is None:
                    self._emit(f"\n\n$$\n{tex}\n$$\n\n")
                else:
                    self._emit(f"${tex}$")
            self.skip += 1                  # the MathML inside is replaced by its LaTeX
            self.stack.append("skip")
            return
        if tag in HEADING_LEVEL and self.cell is None:
            self._block()
            self.heading = []
            self.stack.append(tag)
            return
        if tag == "table":
            if "ltx_equation" in classes or "ltx_eqn_table" in classes:  # layout for display equations
                self.stack.append("eqn")
            else:
                self.tables.append([])
                self.stack.append("table")
            return
        if tag == "tr" and self.tables:
            self.tables[-1].append([])
            self.stack.append("tr")
            return
        if tag in ("td", "th") and self.tables:
            self.cell = []
            self.stack.append("cell")
            return
        if tag == "li":
            self._block()
            self._emit("- ")
            self.stack.append("li")
            return
        if tag in BLOCK_TAGS:
            self._block()
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in VOID or not self.stack:
            return
        action = self.stack.pop()
        if action == "article":
            self.in_article -= 1
        elif action == "skip":
            self.skip -= 1
        elif action in HEADING_LEVEL:
            text = _squash("".join(self.heading or []))
            self.heading = None
            if text:
                self.out.append(f"\n\n{'#' * HEADING_LEVEL[action]} {text}\n\n")
        elif action == "cell":
            if self.tables and self.tables[-1]:
                self.tables[-1][-1].append(_squash("".join(self.cell or [])).replace("|", "\\|"))
            self.cell = None
        elif action == "table":
            rows = [r for r in self.tables.pop() if any(c for c in r)]
            if rows:
                self.out.append("\n\n" + _markdown_table(rows) + "\n\n")
        elif action in BLOCK_TAGS or action == "li":
            self._block()

    def handle_data(self, data):
        if self.skip or not self.in_article:
            return
        self._emit(data)


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _markdown_table(rows: list[list[str]]) -> str:
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + " --- |" * width]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


def html_to_markdown(html: str) -> str | None:
    """Markdown for the paper body of an arXiv (LaTeXML) page, or None when the page holds no paper."""
    conv = _Converter()
    conv.feed(html)
    conv.close()
    if not conv.found_article:
        return None
    text = "".join(conv.out)
    lines = []
    for line in text.splitlines():
        # Inside $$ blocks keep lines as they are; elsewhere collapse runs of spaces.
        lines.append(line.strip() if line.strip().startswith("$") else re.sub(r"[ \t]+", " ", line).strip())
    md = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return md if len(md) >= MIN_CHARS and "## " in md else None


class ArxivHtml:
    """Fetches arXiv HTML politely: at most CONCURRENCY requests at once, with a pause after each."""

    def __init__(self, client: httpx.AsyncClient, concurrency: int = CONCURRENCY, pause_s: float = PAUSE_S):
        self.client = client
        self.sem = asyncio.Semaphore(concurrency)
        self.pause_s = pause_s

    async def fetch(self, paper_id: str) -> str | None:
        """The paper as markdown, or None when arXiv has no usable HTML version. Raises HFError on network trouble."""
        async with self.sem:
            try:
                r = await _get(self.client, HTML_URL.format(id=paper_id))
            finally:
                await asyncio.sleep(self.pause_s)
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise HFError(f"arXiv HTML {paper_id}: HTTP {r.status_code}")
        return html_to_markdown(r.text)

