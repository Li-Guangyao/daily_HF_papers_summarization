"""Fake Hugging Face and LLM providers for end-to-end tests (httpx.MockTransport)."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable

import httpx

from pipeline.config import migration_sql
from pipeline.d1 import LocalD1, encode


class StringParamsD1(LocalD1):
    """LocalD1 that sends parameters the way RemoteD1 does: numbers as strings, NULLs inlined into the SQL.
    SQLite compares a number with a string differently from two numbers, so the tests must see what D1 sees."""

    async def query(self, sql, params=()):
        return await super().query(*encode(sql, params))

    async def batch(self, statements):
        return await super().batch([encode(sql, params) for sql, params in statements])
EN_HEADINGS = ["In a nutshell", "Why teaching robots to plan is hard", "The key idea: think in sketches",
               "How it works, step by step", "What the experiments show", "Limits and open questions"]
ZH_HEADINGS = ["一分钟速览", "为什么教机器人规划这么难", "核心思路：先画草图", "一步步看它如何工作", "实验结果说明了什么",
               "局限与开放问题"]
FORMULA = "\n\n$$\nL = L_{rec} + \\lambda L_{KL}\n$$\n\nHere $L$ is the total loss.\n\n"


def en_output(headings=EN_HEADINGS, category: str | None = "cv.generation") -> str:
    tag = f"<category>{category}</category>\n\n" if category else ""
    return tag + "\n\n".join(f"## {h}\n" + " ".join(["detail"] * 160) + (FORMULA if i == 3 else "")
                         for i, h in enumerate(headings)) + "\n"


def zh_output(n_sections: int = len(ZH_HEADINGS)) -> str:
    body = "\n\n".join(f"## {h}\n" + "这是一段通俗易懂的中文解读内容。" * 20 + (FORMULA if i == 3 else "")
                         for i, h in enumerate(ZH_HEADINGS[:n_sections]))
    return f"<title_zh>测试论文</title_zh>\n<summary_zh>\n{body}\n</summary_zh>"


def sse(content: str, finish: str = "stop") -> httpx.Response:
    chunks = [{"choices": [{"delta": {"reasoning_content": "thinking about it"}}]}]
    step = max(1, len(content) // 3)
    chunks += [{"choices": [{"delta": {"content": content[i:i + step]}}]} for i in range(0, len(content), step)]
    chunks.append({"choices": [{"delta": {}, "finish_reason": finish}], "usage": {"prompt_tokens": 1}})
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})


def hf_item(paper_id: str, upvotes: int, withdrawn: bool = False) -> dict:
    return {"paper": {"id": paper_id, "title": f"Paper {paper_id}", "authors": [{"name": "Ada"}],
                      "summary": f"Abstract of {paper_id}.", "upvotes": upvotes, "publishedAt": "2026-01-01",
                      "ai_keywords": ["k"], "withdrawnAt": "2026-01-02" if withdrawn else None},
            "thumbnail": "https://img", "numComments": 1}


def arxiv_page(paper_id: str, body_words: int = 150) -> str:
    """A page shaped like arXiv's LaTeXML HTML (https://arxiv.org/html/<id>)."""
    words = " ".join(["sentence"] * body_words)
    return f"""<!DOCTYPE html><html><head><title>{paper_id}</title><style>.x{{}}</style></head><body>
<nav class="ltx_page_navbar"><a href="/">Back to arXiv</a></nav>
<header class="desktop_header">This is experimental HTML. Report issue</header>
<div class="ltx_page_main"><div class="ltx_page_content">
<article class="ltx_document ltx_authors_1line">
<h1 class="ltx_title ltx_title_document">A Paper {paper_id}</h1>
<div class="ltx_authors"><span class="ltx_creator ltx_role_author">Ada Lovelace</span></div>
<div class="ltx_abstract"><h6 class="ltx_title ltx_title_abstract">Abstract</h6>
<p class="ltx_p">We study things. {words}</p></div>
<section class="ltx_section" id="S1"><h2 class="ltx_title ltx_title_section"><span class="ltx_tag ltx_tag_section">1 </span>Introduction</h2>
<div class="ltx_para"><p class="ltx_p">The loss <math alttext="L=L_{{rec}}+\\lambda L_{{KL}}" display="inline"><semantics><mi>L</mi>
<annotation encoding="application/x-tex">L</annotation></semantics></math> is minimized. {words}</p></div>
<table class="ltx_equation ltx_eqn_table" id="S1.E1"><tbody><tr class="ltx_equation ltx_eqn_row">
<td class="ltx_eqn_cell"><math alttext="E=mc^{{2}}" display="block"><mi>E</mi></math></td>
<td class="ltx_eqn_cell ltx_eqn_eqno"><span class="ltx_tag ltx_tag_equation">(1)</span></td></tr></tbody></table>
<ul class="ltx_itemize"><li class="ltx_item">first point</li><li class="ltx_item">second point</li></ul>
</section>
<section class="ltx_section" id="S2"><h2 class="ltx_title ltx_title_section"><span class="ltx_tag ltx_tag_section">2 </span>Results</h2>
<figure class="ltx_table" id="T1"><figcaption class="ltx_caption">Table 1: Accuracy.</figcaption>
<table class="ltx_tabular"><tr><th class="ltx_th">Model</th><th class="ltx_th">Acc</th></tr>
<tr><td class="ltx_td">Ours</td><td class="ltx_td">91.2</td></tr><tr><td class="ltx_td">Base</td><td class="ltx_td">88.0</td></tr>
</table></figure>
<figure class="ltx_figure"><img src="x.png" alt="a plot"/><figcaption class="ltx_caption">Figure 1: A plot.</figcaption></figure>
<div class="ltx_para"><p class="ltx_p">Results line.<br/>{words}</p></div>
<h3 class="ltx_title ltx_title_subsection">2.1 Ablations</h3><p class="ltx_p">More. {words}</p>
</section>
<section class="ltx_bibliography" id="bib"><h2 class="ltx_title ltx_title_bibliography">References</h2>
<ul class="ltx_biblist"><li class="ltx_bibitem">[1] A. Author. Old paper.</li></ul></section>
<section class="ltx_appendix" id="A1"><h2 class="ltx_title ltx_title_appendix">Appendix A Proofs</h2><p>Long proofs.</p></section>
</article></div></div>
<footer class="ltx_page_footer">Generated by LaTeXML</footer></body></html>"""


Behavior = Callable[[dict, str], httpx.Response]  # (request body, "en" | "zh" | "classify" | "health") -> response


def ok_behavior(body: dict, kind: str) -> httpx.Response:
    if kind == "health":
        return sse("OK")
    if kind == "classify":
        return sse("<category>llm.agents</category>")
    return sse(en_output() if kind == "en" else zh_output())


def failing(status: int, text: str = "boom", kinds=("en", "zh")) -> Behavior:
    def behave(body: dict, kind: str) -> httpx.Response:
        if kind in kinds:
            return httpx.Response(status, text=text)
        return ok_behavior(body, kind)
    return behave


class FakeWorld:
    """Routes requests by host: huggingface.co is HF; every other host is an LLM provider."""

    def __init__(self, lists: dict[str, list[dict]], providers: dict[str, Behavior], missing_md=(), arxiv_html=()):
        """missing_md: ids with no HF markdown. arxiv_html: ids that have an arXiv HTML page."""
        self.lists, self.providers, self.missing_md = lists, providers, set(missing_md)
        self.arxiv_html = set(arxiv_html)
        self.calls: Counter = Counter()
        self.bodies: list[tuple[str, str, dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if host == "arxiv.org":
            paper_id = path.removeprefix("/html/")
            self.calls[("arxiv", "html")] += 1
            if paper_id in self.arxiv_html:
                return httpx.Response(200, text=arxiv_page(paper_id))
            return httpx.Response(404, text="No HTML for this paper")
        if host == "huggingface.co":
            if path == "/api/daily_papers":
                day, page = request.url.params["date"], int(request.url.params["p"])
                return httpx.Response(200, json=self.lists.get(day, []) if page == 0 else [])
            paper_id = path.removeprefix("/papers/").removesuffix(".md")
            if paper_id in self.missing_md:
                return httpx.Response(404, text="not found")
            return httpx.Response(200, text=f"Markdown Content:\nFull text of {paper_id}. " + "body " * 300 +
                                             "\n## References\n[1] x")
        body = json.loads(request.content)
        system = body["messages"][0]["content"]
        kind = ("health" if body["max_tokens"] == 16 else "zh" if system.startswith("你是")
                else "classify" if system.startswith("You classify") else "en")
        self.calls[(host, kind)] += 1
        self.bodies.append((host, kind, body))
        return self.providers[host](body, kind)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))


def make_db(routes: list[tuple[str, str, int]]) -> LocalD1:
    """In-memory D1 with the real schema; routes = [(family, host, max_concurrency), ...]."""
    db = StringParamsD1(":memory:")
    db.executescript(migration_sql())
    db.conn.execute("DELETE FROM llm_models")
    for i, (family, host, conc) in enumerate(routes, start=1):
        db.conn.execute(
            "INSERT INTO llm_models (id, family, display_name, provider, base_url, api_model, api_key_env, rpm_limit, "
            "max_concurrency, context_tokens, max_output_tokens) VALUES (?, ?, ?, ?, ?, ?, 'FAKE_KEY', 60000, ?, 100000, "
            "8000)", [i, family, family.upper(), host.split(".")[0], f"https://{host}/v1", f"model-{i}", conc])
    return db
