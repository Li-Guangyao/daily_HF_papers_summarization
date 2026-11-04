import { cached, json, parseJsonArray, type Env } from "../../../lib/api";

// GET /api/papers/:id → paper details plus its summary (EN + ZH) and the model that wrote it.
export const onRequestGet: PagesFunction<Env, "id"> = async (ctx) => {
  const id = String(ctx.params.id);
  if (!/^[\w.-]{1,40}$/.test(id)) return json({ error: "bad paper id" }, 400);

  return cached(ctx, 3600, async () => {
    const paper = await ctx.env.DB.prepare(
      `SELECT p.id, p.daily_date AS dailyDate, p.title, p.authors, p.abstract, p.published_at AS publishedAt,
              p.thumbnail, p.project_page AS projectPage, p.github_repo AS githubRepo, p.github_stars AS githubStars,
              p.upvotes, p.num_comments AS numComments, p.hf_ai_summary AS hfAiSummary,
              p.hf_ai_keywords AS hfAiKeywords, p.withdrawn_at AS withdrawnAt,
              p.category_major AS categoryMajor, p.category_minor AS categoryMinor,
              s.status, s.source, s.summary_en AS summaryEn, s.title_zh AS titleZh, s.summary_zh AS summaryZh,
              em.display_name AS model, em.provider AS enProvider, zm.provider AS zhProvider
         FROM papers p
         LEFT JOIN summaries s ON s.paper_id = p.id
         LEFT JOIN llm_models em ON em.id = s.en_model_id
         LEFT JOIN llm_models zm ON zm.id = s.zh_model_id
        WHERE p.id = ?`,
    )
      .bind(id)
      .first<Record<string, unknown>>();
    if (!paper) return json({ error: "paper not found" }, 404);

    const done = paper.status === "ok";
    return json(
      {
        ...paper,
        authors: parseJsonArray(paper.authors),
        hfAiKeywords: parseJsonArray(paper.hfAiKeywords),
        // Only finished summaries are shown; half-done ones look like "in progress".
        summaryEn: done ? paper.summaryEn : null,
        summaryZh: done ? paper.summaryZh : null,
        titleZh: done ? paper.titleZh : null,
      },
      200,
      done ? 3600 : 300,
    );
  });
};
