import { cached, json, type Env } from "../../lib/api";

// GET /api/dates → { ready: [{date, paperCount}], processing: [{date, papersDone, paperCount}] }
export const onRequestGet: PagesFunction<Env> = (ctx) =>
  cached(ctx, 300, async () => {
    const db = ctx.env.DB;
    const [ready, processing] = await db.batch([
      db.prepare(
        "SELECT date, paper_count AS paperCount FROM daily_lists WHERE status = 'ready' AND paper_count > 0 ORDER BY date DESC",
      ),
      db.prepare(
        `SELECT d.date,
                COUNT(p.id) AS paperCount,
                SUM(CASE WHEN s.status IN ('ok', 'fatal') THEN 1 ELSE 0 END) AS papersDone
           FROM daily_lists d
           LEFT JOIN papers p ON p.daily_date = d.date AND p.withdrawn_at IS NULL
           LEFT JOIN summaries s ON s.paper_id = p.id
          WHERE d.status = 'processing'
          GROUP BY d.date ORDER BY d.date DESC`,
      ),
    ]);
    return json({ ready: ready.results, processing: processing.results }, 200, 300);
  });
