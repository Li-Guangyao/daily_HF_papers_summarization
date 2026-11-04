import { cached, isDate, json, parseJsonArray, ttlForDate, type Env } from "../../../lib/api";

// GET /api/papers?date=YYYY-MM-DD → the papers of a ready date, most upvoted first.
export const onRequestGet: PagesFunction<Env> = async (ctx) => {
  const day = new URL(ctx.request.url).searchParams.get("date");
  if (!isDate(day)) return json({ error: "date must be YYYY-MM-DD" }, 400);

  return cached(ctx, ttlForDate(day), async () => {
    const db = ctx.env.DB;
    const list = await db.prepare("SELECT status FROM daily_lists WHERE date = ?").bind(day).first<{ status: string }>();
    if (!list) return json({ error: "no papers for this date" }, 404);
    if (list.status === "processing") return json({ status: "processing" }, 409);

    const { results } = await db
      .prepare(
        `SELECT p.id, p.title, p.authors, p.upvotes, p.thumbnail, p.withdrawn_at AS withdrawnAt,
                s.title_zh AS titleZh, s.status AS summaryStatus, m.display_name AS model
           FROM papers p
           LEFT JOIN summaries s ON s.paper_id = p.id
           LEFT JOIN llm_models m ON m.id = s.en_model_id
          WHERE p.daily_date = ?
          ORDER BY p.upvotes DESC`,
      )
      .bind(day)
      .all<Record<string, unknown>>();

    const papers = results.map((r) => ({ ...r, authors: parseJsonArray(r.authors) }));
    return json({ date: day, papers }, 200, ttlForDate(day));
  });
};
