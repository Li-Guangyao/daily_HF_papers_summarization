import { useEffect, useMemo, useState } from "react";
import "katex/dist/katex.min.css";
import { fetchPaper, type PaperDetail } from "./api";
import { t, type Lang } from "./i18n";
import { renderMarkdown } from "./markdown";

export default function DetailPage({ lang }: { lang: Lang }) {
  const s = t(lang);
  const id = new URLSearchParams(location.search).get("id") ?? "";
  const [paper, setPaper] = useState<PaperDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return setError(s.notFound);
    fetchPaper(id)
      .then((p) => {
        setPaper(p);
        document.title = `${p.title} · ${s.siteName}`;
      })
      .catch((e) => setError(e.status === 404 ? s.notFound : s.loadError));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  const summary = paper ? (lang === "zh" ? paper.summaryZh : paper.summaryEn) : null;
  const rendered = useMemo(() => (summary ? renderMarkdown(summary) : null), [summary]);

  if (error) return <p className="mx-auto max-w-3xl px-4 py-16 text-stone-500">{error}</p>;
  if (!paper) return <p className="mx-auto max-w-3xl px-4 py-16 text-stone-500">{s.loading}</p>;

  const pdfUrl = `https://arxiv.org/pdf/${paper.id}`;
  const links = [
    { label: "Hugging Face", href: `https://huggingface.co/papers/${paper.id}` },
    { label: "arXiv", href: `https://arxiv.org/abs/${paper.id}` },
    paper.projectPage && { label: "Project", href: paper.projectPage },
    paper.githubRepo && {
      label: `GitHub${paper.githubStars ? ` ★${paper.githubStars}` : ""}`,
      href: paper.githubRepo,
    },
  ].filter(Boolean) as { label: string; href: string }[];
  const provider =
    paper.enProvider && (lang === "zh" && paper.zhProvider ? paper.zhProvider : paper.enProvider);

  return (
    <div className="mx-auto grid w-full max-w-screen-2xl flex-1 lg:h-[calc(100vh-3.5rem)] lg:grid-cols-2">
      <div className="hidden border-r border-stone-200 lg:block dark:border-stone-800">
        <iframe src={pdfUrl} title={paper.title} className="h-full w-full bg-white" />
      </div>

      <article className="min-w-0 px-4 pb-16 lg:overflow-y-auto lg:px-8">
        <a href={`/?date=${paper.dailyDate}&lang=${lang}`} className="mt-5 inline-block text-sm text-amber-600 hover:underline">
          ← {s.back}
        </a>
        <h1 className="mt-3 text-2xl font-semibold leading-tight tracking-tight">{paper.title}</h1>
        {lang === "zh" && paper.titleZh && (
          <p className="mt-1 text-lg text-stone-700 dark:text-stone-300">{paper.titleZh}</p>
        )}
        <p className="mt-3 text-sm leading-relaxed text-stone-600 dark:text-stone-400">{paper.authors.join(", ")}</p>
        <p className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-stone-500 dark:text-stone-400">
          {paper.publishedAt && (
            <span>
              {s.published} {paper.publishedAt.slice(0, 10)}
            </span>
          )}
          <span>
            {s.listed} {paper.dailyDate}
          </span>
          <span className="tabular-nums">▲ {paper.upvotes}</span>
        </p>

        <div className="mt-4 flex flex-wrap gap-2">
          <a
            href={pdfUrl}
            target="_blank"
            rel="noopener"
            className="rounded-lg bg-amber-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-amber-600"
          >
            {s.openPdf} ↗
          </a>
          {links.map((l) => (
            <a
              key={l.href}
              href={l.href}
              target="_blank"
              rel="noopener noreferrer"
              className="rounded-lg border border-stone-300 px-3 py-1.5 text-sm hover:border-stone-500 dark:border-stone-700 dark:hover:border-stone-500"
            >
              {l.label}
            </a>
          ))}
        </div>

        {rendered ? (
          <>
            <p className="mt-6 text-xs text-stone-500 dark:text-stone-400">
              {lang === "zh"
                ? `AI 解读${s.by} ${paper.model}${provider ? `（${s.via} ${provider}）` : ""}生成`
                : `AI walkthrough ${s.by} ${paper.model}${provider ? ` ${s.via} ${provider}` : ""}`}
            </p>
            {rendered.toc.length > 1 && (
              <nav className="sticky top-0 z-10 -mx-4 mt-3 border-b border-stone-200 bg-stone-50/95 px-4 py-2 backdrop-blur lg:-mx-8 lg:px-8 dark:border-stone-800 dark:bg-stone-950/95">
                <span className="sr-only">{s.onThisPage}</span>
                <div className="no-scrollbar flex gap-1.5 overflow-x-auto">
                  {rendered.toc.map((h) => (
                    <a
                      key={h.id}
                      href={`#${h.id}`}
                      className="shrink-0 rounded-full border border-stone-300 px-2.5 py-0.5 text-xs text-stone-600 hover:border-amber-500 hover:text-amber-700 dark:border-stone-700 dark:text-stone-400 dark:hover:text-amber-400"
                    >
                      {h.text}
                    </a>
                  ))}
                </div>
              </nav>
            )}
            <div
              className="prose prose-stone mt-4 max-w-none dark:prose-invert prose-headings:scroll-mt-16 prose-h2:mt-10 prose-table:text-sm"
              dangerouslySetInnerHTML={{ __html: rendered.html }}
            />
          </>
        ) : (
          <section className="mt-8 rounded-xl border border-stone-200 p-4 dark:border-stone-800">
            <p className="text-sm font-medium">
              {paper.status === "fatal" || paper.withdrawnAt ? s.unavailable : s.inProgress}
            </p>
            <p className="mt-1 text-sm text-stone-500 dark:text-stone-400">{s.noSummaryNote}</p>
            {paper.hfAiSummary && (
              <>
                <h2 className="mt-4 text-sm font-semibold">{s.hfSummary}</h2>
                <p className="mt-1 leading-relaxed">{paper.hfAiSummary}</p>
              </>
            )}
            {paper.abstract && (
              <>
                <h2 className="mt-4 text-sm font-semibold">{s.abstract}</h2>
                <p className="mt-1 leading-relaxed">{paper.abstract}</p>
              </>
            )}
          </section>
        )}
      </article>
    </div>
  );
}
