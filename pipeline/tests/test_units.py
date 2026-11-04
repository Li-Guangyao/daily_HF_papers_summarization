from datetime import date

import pytest

from pipeline.config import expand_dates, previous_workdays
from pipeline.d1 import encode
from pipeline.llm import PaperError, ProviderError, classify
from pipeline.markdown import (ABSTRACT_NOTICE, OutputCheckError, check_en, check_zh, prepare_input,
                               strip_reasoning)
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
    good = en_output()
    assert check_en("Sure! Here it is:\n" + good, "full_text") == good.strip()      # preamble dropped
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
