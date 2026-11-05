"""End-to-end runs of pipeline.core against fake HF + fake providers and an in-memory D1."""

import asyncio

import pytest

from pipeline.core import AllProvidersDown, RunOptions, run
from pipeline.lock import RunLock
from pipeline.pool import Timing
from pipeline.tests.fakes import FakeWorld, en_output, failing, hf_item, make_db, ok_behavior, sse

DAY = "2026-01-05"  # an old Monday: missing markdown falls back to the abstract right away
FAST = Timing(retry_base=0.01, rate_limit_pause=0.01, cooling=0.05, idle_poll=0.01, paper_retry_delay=0.01,
              arxiv_pause=0)


@pytest.fixture(autouse=True)
def fake_key(monkeypatch):
    monkeypatch.setenv("FAKE_KEY", "k")


def opts(**kw) -> RunOptions:
    return RunOptions(dates=[DAY], timing=FAST, **kw)


def go(coro):
    return asyncio.run(coro)


async def rows(db, sql, params=()):
    return await db.query(sql, params)


def test_full_run_with_failover_and_same_model_per_paper():
    db = make_db([("nemo", "flaky.test", 2), ("nemo", "good.test", 2), ("glm", "glm.test", 1)])
    db.conn.execute("""UPDATE llm_models SET translate_options = '{"thinking":{"type":"disabled"}}'""")
    papers = [hf_item(f"2601.0000{i}", upvotes=10 - i) for i in range(5)] + [hf_item("2601.09999", 1, withdrawn=True)]
    world = FakeWorld({DAY: papers},
                      {"flaky.test": failing(500), "good.test": ok_behavior, "glm.test": ok_behavior},
                      missing_md={"2601.00004"})

    async def scenario():
        async with world.client() as client:
            report = await run(db, opts(), client=client)
        summaries = await rows(db, "SELECT s.*, e.family AS en_fam, z.family AS zh_fam FROM summaries s "
                                   "JOIN llm_models e ON e.id = s.en_model_id JOIN llm_models z ON z.id = s.zh_model_id")
        day = (await rows(db, "SELECT * FROM daily_lists"))[0]
        errors = await rows(db, "SELECT * FROM job_errors")
        return report, summaries, day, errors

    report, summaries, day, errors = go(scenario())
    assert len(summaries) == 5                                          # withdrawn paper gets no summary
    assert all(s["status"] == "ok" for s in summaries)
    assert all(s["en_fam"] == s["zh_fam"] == s["family"] for s in summaries)   # same model for EN and ZH
    assert {s["source"] for s in summaries if s["paper_id"] == "2601.00004"} == {"abstract"}
    assert day["status"] == "ready" and day["paper_count"] == 6
    assert errors and all(e["model_id"] == 1 for e in errors)           # only the flaky provider failed
    assert report.remaining == 0 and not report.outage
    assert world.calls[("flaky.test", "en")] > 0                        # it was tried, then work moved on
    sent = [(kind, "thinking" in body) for host, kind, body in world.bodies if kind in ("en", "zh")]
    assert all(has == (kind == "zh") for kind, has in sent)            # translate_options only on the ZH step


def test_all_providers_down_changes_nothing():
    db = make_db([("nemo", "a.test", 2), ("glm", "b.test", 1)])
    world = FakeWorld({DAY: [hf_item("2601.00001", 5)]},
                      {"a.test": failing(503, kinds=("health", "en", "zh")), "b.test": failing(401, kinds=("health",))})

    async def scenario():
        async with world.client() as client:
            with pytest.raises(AllProvidersDown):
                await run(db, opts(), client=client)
        return (await rows(db, "SELECT COUNT(*) AS n FROM papers"))[0]["n"], \
               (await rows(db, "SELECT COUNT(*) AS n FROM daily_lists"))[0]["n"]

    assert go(scenario()) == (0, 0)


def test_outage_keeps_date_hidden_then_retired_model_is_redone_by_another():
    db = make_db([("nemo", "nemo.test", 1), ("glm", "glm.test", 1)])
    paper = [hf_item("2601.00001", 5)]
    # Run 1: only nemo is enabled; it writes EN, then disappears ("gone") before translating.
    db.conn.execute("UPDATE llm_models SET is_enabled = 0 WHERE family = 'glm'")
    world = FakeWorld({DAY: paper}, {"nemo.test": failing(404, '{"error":{"message":"model not found"}}',
                                                          kinds=("zh",)), "glm.test": ok_behavior})

    async def run1():
        async with world.client() as client:
            report = await run(db, opts(), client=client)
        return report, (await rows(db, "SELECT * FROM summaries"))[0], (await rows(db, "SELECT * FROM daily_lists"))[0]

    report, s, day = go(run1())
    assert s["summary_en"] and not s["summary_zh"] and s["family"] == "nemo"
    assert day["status"] == "processing"          # not shown: the summary isn't finished
    assert report.outage                          # scheduled run would exit 1
    assert "retired or no longer free" in report.markdown()

    # Run 2: you disable nemo in D1 and enable glm. GLM redoes the paper in both languages.
    db.conn.execute("UPDATE llm_models SET is_enabled = CASE family WHEN 'glm' THEN 1 ELSE 0 END")

    async def run2():
        async with world.client() as client:
            await run(db, opts(), client=client)
        return (await rows(db, "SELECT s.*, m.family AS en_fam FROM summaries s "
                               "JOIN llm_models m ON m.id = s.en_model_id"))[0], \
               (await rows(db, "SELECT * FROM daily_lists"))[0]

    s, day = go(run2())
    assert s["status"] == "ok" and s["family"] == "glm" and s["en_fam"] == "glm"
    assert day["status"] == "ready"


def test_paper_error_becomes_fatal_and_date_still_ready():
    db = make_db([("nemo", "a.test", 1)])
    world = FakeWorld({DAY: [hf_item("2601.00001", 5), hf_item("2601.00002", 4)]},
                      {"a.test": lambda body, kind: failing(400, "bad input")(body, kind)
                       if "2601.00002" in str(body) else ok_behavior(body, kind)})

    async def scenario():
        async with world.client() as client:
            await run(db, opts(), client=client)
        return {r["paper_id"]: r for r in await rows(db, "SELECT * FROM summaries")}, \
               (await rows(db, "SELECT * FROM daily_lists"))[0]

    s, day = go(scenario())
    assert s["2601.00001"]["status"] == "ok"
    assert s["2601.00002"]["status"] == "fatal" and s["2601.00002"]["attempts"] == 3
    assert day["status"] == "ready"


def test_run_lock_and_step_aside_request():
    db = make_db([])

    async def scenario():
        backfill, scheduled = RunLock(db, "local:me:1"), RunLock(db, "actions:42")
        assert await backfill.try_acquire()
        assert not await scheduled.acquire(max_wait_s=0, poll_s=0, ask_holder_to_yield=True, log=lambda m: None)
        waiting = (await db.query("SELECT waiting FROM run_locks"))[0]["waiting"]
        await backfill.release()
        assert await scheduled.try_acquire()
        return waiting

    assert go(scenario()) == "actions:42"


def test_busy_provider_still_counts_as_available():
    db = make_db([("glm", "glm.test", 1)])
    busy_once = {"left": 1}

    def behave(body, kind):
        if kind == "health" and busy_once["left"]:
            busy_once["left"] -= 1
            return failing(429, '{"error":{"code":"1305","message":"该模型当前访问量过大"}}', kinds=("health",))(body, kind)
        return ok_behavior(body, kind)

    world = FakeWorld({DAY: [hf_item("2601.00001", 5)]}, {"glm.test": behave})

    async def scenario():
        async with world.client() as client:
            await run(db, opts(), client=client)
        return (await rows(db, "SELECT status FROM summaries"))[0]["status"]

    assert go(scenario()) == "ok"


def test_categories_from_walkthrough_and_from_title_and_abstract():
    db = make_db([("nemo", "a.test", 2)])
    # An older paper stored before categories existed: summarized, but without a category.
    db.conn.execute("INSERT INTO daily_lists (date, paper_count, status, fetched_at) VALUES ('2026-01-02', 1, 'ready', 'x')")
    db.conn.execute("INSERT INTO papers (id, daily_date, title, authors, abstract) "
                    "VALUES ('2601.00900', '2026-01-02', 'Old paper', '[]', 'An old abstract.')")
    db.conn.execute("INSERT INTO summaries (paper_id, status) VALUES ('2601.00900', 'ok')")

    def behave(body, kind):
        text = str(body)
        if kind == "en" and "2601.00002" in text:
            return sse(en_output(category="not.a.category"))   # no valid tag: classified afterwards
        if kind == "classify" and "Paper 2601.00003" in text:
            return sse("I think it is about agents.")          # unusable answer: counted, retried next run
        return ok_behavior(body, kind)

    papers = [hf_item("2601.00001", 9), hf_item("2601.00002", 8), hf_item("2601.00003", 7, withdrawn=True)]
    world = FakeWorld({DAY: papers}, {"a.test": behave})

    async def scenario():
        async with world.client() as client:
            report = await run(db, opts(), client=client)
            again = await run(db, opts(), client=client)
        cats = {r["id"]: r for r in await rows(db, "SELECT id, category_major, category_minor, category_attempts "
                                                   "FROM papers")}
        return report, again, cats

    report, again, cats = go(scenario())
    assert (cats["2601.00001"]["category_major"], cats["2601.00001"]["category_minor"]) == ("cv", "cv.generation")
    assert cats["2601.00002"]["category_minor"] == "llm.agents"                  # from title + abstract
    assert cats["2601.00900"]["category_minor"] == "llm.agents"                  # history gets classified too
    assert cats["2601.00003"]["category_minor"] is None and cats["2601.00003"]["category_attempts"] == 2
    assert report.done["categorized"] == 1 and report.categories["ok"] == 2 and report.categories["failed"] == 1
    assert "Categories from walkthroughs: 1" in report.markdown()
    assert world.calls[("a.test", "classify")] == 3 + 1                          # run 2 only retries the failed one


def test_missing_markdown_waits_hours_not_runs_then_uses_abstract():
    from datetime import timedelta
    from pipeline.config import utc_today
    day = (utc_today() - timedelta(days=1)).isoformat()   # recent: wait for the full text
    db = make_db([("nemo", "a.test", 1)])
    world = FakeWorld({day: [hf_item("2611.00001", 5)]}, {"a.test": ok_behavior}, missing_md={"2611.00001"})

    async def once():
        async with world.client() as client:
            await run(db, RunOptions(dates=[day], timing=FAST), client=client)
        return (await rows(db, "SELECT s.status, s.source, p.md_misses, p.md_first_miss_at FROM summaries s "
                               "JOIN papers p ON p.id = s.paper_id"))[0]

    first = go(once())
    assert first["status"] == "pending" and first["md_first_miss_at"] and first["md_misses"] == 1
    second = go(once())                                    # a run soon after: still waiting
    assert second["status"] == "pending" and second["md_first_miss_at"] == first["md_first_miss_at"]
    db.conn.execute("UPDATE papers SET md_first_miss_at = datetime('now', '-11 hours')")
    third = go(once())                                     # waited long enough: abstract
    assert third["status"] == "ok" and third["source"] == "abstract"


def test_unusable_translation_is_retried_after_a_delay_then_fatal():
    db = make_db([("nemo", "a.test", 1)])

    def behave(body, kind):
        if kind == "zh":
            return sse("这是一段没有任何结构的输出。")
        return ok_behavior(body, kind)

    world = FakeWorld({DAY: [hf_item("2601.00001", 5)]}, {"a.test": behave})

    async def scenario():
        async with world.client() as client:
            await run(db, opts(), client=client)
        return (await rows(db, "SELECT status, attempts FROM summaries"))[0], \
               await rows(db, "SELECT message FROM job_errors WHERE job = 'translate'"), \
               (await rows(db, "SELECT status FROM daily_lists"))[0]["status"]

    summary, errors, day_status = go(scenario())
    assert summary["status"] == "fatal" and summary["attempts"] == 3          # stops after 3, also on D1
    assert world.calls[("a.test", "zh")] == 3
    assert len(errors) == 3 and "output: 这是一段没有任何结构的输出。" in errors[0]["message"]
    assert day_status == "ready"


def test_arxiv_html_is_used_when_hf_has_no_markdown():
    from datetime import timedelta
    from pipeline.config import utc_today
    day = (utc_today() - timedelta(days=1)).isoformat()    # recent: without HTML it would wait for HF
    db = make_db([("nemo", "a.test", 2)])
    world = FakeWorld({day: [hf_item("2611.00001", 5), hf_item("2611.00002", 4), hf_item("2611.00003", 3)]},
                      {"a.test": ok_behavior}, missing_md={"2611.00002", "2611.00003"}, arxiv_html={"2611.00002"})

    async def scenario():
        async with world.client() as client:
            await run(db, RunOptions(dates=[day], timing=FAST), client=client)
        return {r["paper_id"]: r for r in await rows(db, "SELECT paper_id, status, source FROM summaries")}

    s = go(scenario())
    assert (s["2611.00001"]["status"], s["2611.00001"]["source"]) == ("ok", "full_text")    # HF markdown
    assert (s["2611.00002"]["status"], s["2611.00002"]["source"]) == ("ok", "arxiv_html")   # arXiv HTML
    assert s["2611.00003"]["status"] == "pending"                                           # neither: waits
    assert world.calls[("arxiv", "html")] == 2                                              # only when HF has none
    sent = [body for host, kind, body in world.bodies if kind == "en" and "2611.00002" in str(body)]
    paper_text = sent[0]["messages"][1]["content"]
    assert "## 1 Introduction" in paper_text and "$$\nE=mc^{2}\n$$" in paper_text and "Old paper" not in paper_text


def test_paper_without_any_full_text_still_uses_the_abstract_when_old():
    db = make_db([("nemo", "a.test", 1)])
    world = FakeWorld({DAY: [hf_item("2601.00001", 5)]}, {"a.test": ok_behavior}, missing_md={"2601.00001"})

    async def scenario():
        async with world.client() as client:
            await run(db, opts(), client=client)
        return (await rows(db, "SELECT status, source FROM summaries"))[0]

    assert go(scenario()) == {"status": "ok", "source": "abstract"}
    assert world.calls[("arxiv", "html")] == 1
