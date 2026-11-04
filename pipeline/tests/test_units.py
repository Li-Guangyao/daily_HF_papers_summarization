from datetime import date

import pytest

from pipeline.config import expand_dates, previous_workdays
from pipeline.d1 import encode
from pipeline.llm import PaperError, ProviderError, classify
import asyncio
import time

from pipeline.markdown import (ABSTRACT_NOTICE, OutputCheckError, check_en, check_zh, prepare_input, snippet,
                               strip_reasoning)
from pipeline.pool import RpmLimiter
from pipeline.taxonomy import MINORS, TAXONOMY, parse_category
from pipeline.tests.fakes import en_output, zh_output


def test_previous_workdays():
    assert previous_workdays(date(2026, 10, 26)) == ["2026-10-22", "2026-10-23"]  # Monday → Thu, Fri
    assert previous_workdays(date(2026, 10, 27)) == ["2026-10-23", "2026-10-26"]  # Tuesday → Fri, Mon
    assert previous_workdays(date(2026, 10, 22)) == ["2026-10-20", "2026-10-21"]


def test_expand_dates_skips_weekends():
    assert expand_dates("2026-10-16..2026-10-19") == ["2026-10-16", "2026-10-19"]
    assert len(expand_dates("2026-10-16..2026-10-19", include_weekends=True)) == 4
    assert expand_dates("2026-10-21, 2026-10-20") == ["2026-10-20", "2026-10-21"]


def test_encode_inlines_nulls_and_stringifies():
    assert encode("UPDATE t SET a = ?, b = ? WHERE c = ?", [1, None, "x"]) == (
        "UPDATE t SET a = ?, b = NULL WHERE c = ?", ["1", "x"])
    assert encode("SELECT ?", [True]) == ("SELECT ?", ["1"])


def test_prepare_input_cuts_header_references_and_images():
    md = ("Title: X\n\nURL Source: y\n\nMarkdown Content:\nIntro text " + "word " * 500 +
          "\n![fig](https://img)\n\n\n\n## 5 Conclusion\nDone.\n## References\n[1] A. Author\n## Appendix A\nmore")
    text, truncated = prepare_input(md, 100_000)
    assert text.startswith("Intro text") and "References" not in text and "Appendix" not in text
    assert "![fig]" not in text and "\n\n\n" not in text and not truncated
    short, truncated = prepare_input(md, 100)
    assert truncated and short.endswith("[TRUNCATED]")


def test_strip_reasoning():
    assert strip_reasoning("<think>hmm</think>\n## TL;DR") == "## TL;DR"
    assert strip_reasoning("still thinking</think>answer") == "answer"


def test_check_en_accepts_any_structure():
    good = en_output(category=None)
    assert check_en("Sure! Here it is:\n" + good, "full_text") == good.strip()      # preamble dropped
    assert check_en(en_output(), "full_text") == good.strip()                         # category tag dropped
    assert "<category>" not in check_en(good + "\n<category>llm.rag</category>\n", "full_text")
    custom = en_output(["In a nutshell", "Background", "What they found", "Why it matters", "Caveats", "Next"])
    assert check_en(custom, "full_text").startswith("## In a nutshell")


def test_check_en_rejects_thin_or_broken_output():
    with pytest.raises(OutputCheckError):
        check_en(en_output(["In a nutshell", "Everything else"]), "full_text")         # fewer than 3 sections
    with pytest.raises(OutputCheckError):
        check_en("## A\nshort\n## B\nshort\n## C\nshort", "full_text")               # too short
    with pytest.raises(OutputCheckError):
        check_en(en_output() + "\n$$\nx = 1\n", "full_text")                          # unclosed $$ block


def test_check_en_abstract_gets_notice():
    text = "> Based on the abstract only; the full text was unavailable.\n\n## In a nutshell\n" + "word " * 300
    out = check_en(text, "abstract")
    assert out.startswith(ABSTRACT_NOTICE) and out.count(ABSTRACT_NOTICE) == 1


def test_check_zh():
    en = en_output()
    title, body = check_zh(zh_output(), en)
    assert title == "测试论文" and body.startswith("## 一分钟速览")
    with pytest.raises(OutputCheckError):
        check_zh("<title_zh>x</title_zh>", en)                                         # no body
    with pytest.raises(OutputCheckError):
        check_zh(zh_output(n_sections=4), en)                                           # sections dropped
    with pytest.raises(OutputCheckError):
        check_zh(zh_output().replace("$$", ""), en)                                     # formula not preserved


def test_check_zh_reads_tags_leniently():
    en = en_output()
    good_title, good_body = check_zh(zh_output(), en)
    no_close = zh_output().replace("</summary_zh>", "")
    assert check_zh(no_close, en) == (good_title, good_body)                            # missing closing tag
    fenced = f"```markdown\n{zh_output()}\n```"
    assert check_zh(fenced, en) == (good_title, good_body)                              # fenced output
    bare = "# 测试论文\n\n" + good_body                                               # no tags at all
    assert check_zh(bare, en) == ("测试论文", good_body)
    assert check_zh("标题：测试论文\n" + good_body, en)[0] == "测试论文"
    assert check_zh(good_body, en) == (None, good_body)                                 # no title: allowed
    with pytest.raises(OutputCheckError) as err:
        check_zh("抱歉，我无法完成。", en)
    assert err.value.raw == "抱歉，我无法完成。"                                           # kept for the log


def test_snippet_keeps_head_and_tail_on_one_line():
    assert snippet("a\nb") == "a\\nb"
    long = "x" * 300 + "MIDDLE" + "y" * 300
    s = snippet(long)
    assert s.startswith("x" * 250) and s.endswith("y" * 150) and "MIDDLE" not in s


def test_rpm_limiter_spaces_starts_evenly():
    async def scenario():
        limiter = RpmLimiter(1200)          # 90% → 1080 per minute → one start every ~56 ms
        t0 = time.monotonic()
        for _ in range(4):
            await limiter.acquire()
        return time.monotonic() - t0

    elapsed = asyncio.run(scenario())
    assert elapsed >= 3 * 60 / 1080 * 0.95  # no burst: the 2nd–4th starts each waited


@pytest.mark.parametrize("status, body, kind", [
    (429, '{"error":{"code":"1302","message":"您的账户已达到速率限制，请您控制请求频率"}}', "rate_limit"),
    (429, '{"error":{"code":429,"message":"Rate limit exceeded: free-models-per-day"}}', "quota"),
    (401, "unauthorized", "auth"),
    (404, '{"error":{"message":"Model not found"}}', "gone"),
    (402, "payment required", "gone"),
    (503, "overloaded", "transient"),
])
def test_classify_provider_errors(status, body, kind):
    err = classify(status, body)
    assert isinstance(err, ProviderError) and err.kind == kind


def test_classify_paper_error():
    assert isinstance(classify(400, '{"error":{"code":"1301","message":"sensitive content"}}'), PaperError)


def test_parse_category():
    assert parse_category("<category>llm.agents</category>\n## In a nutshell") == ("llm", "llm.agents")
    assert parse_category("<think>maybe</think><category> CV.Generation </category>") == ("cv", "cv.generation")
    assert parse_category("<category>llm.unknown</category><category>mm.vlm</category>") == ("mm", "mm.vlm")
    assert parse_category("agents") is None


def test_taxonomy_is_consistent():
    ids = [m["id"] for major in TAXONOMY["majors"] for m in major["minors"]]
    assert len(ids) == len(set(ids)) == len(MINORS)
    for major in TAXONOMY["majors"]:
        assert major["zh"] and major["en"]
        for m in major["minors"]:
            assert m["id"].startswith(major["id"] + ".") and m["zh"] and m["en"] and m["desc"]
