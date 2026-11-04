import { useEffect, useRef, useState } from "react";
import { fetchDates, fetchPapers, type PaperListItem, type PapersResponse, type ProcessingDate } from "./api";
import {
  UNCATEGORIZED,
  categoryOf,
  filterExists,
  groupPapers,
  majorName,
  matchesFilter,
  minorDesc,
  minorName,
  type CategoryGroup,
} from "./categories";
import { t, type Lang } from "./i18n";

function weekday(date: string, lang: Lang) {
  return new Date(`${date}T00:00:00Z`).toLocaleDateString(lang === "zh" ? "zh-CN" : "en-US", {
    weekday: "short",
    timeZone: "UTC",
  });
}

// A date in the strip: every ready date, plus processing dates that already have finished papers.
interface ShownDate {
  date: string;
  paperCount: number;
  papersDone: number;
}

export default function ListPage({ lang }: { lang: Lang }) {
  const s = t(lang);
  const [dates, setDates] = useState<ShownDate[] | null>(null); // newest first
  const [waiting, setWaiting] = useState<ProcessingDate[]>([]); // processing dates with nothing finished yet
  const [selected, setSelected] = useState<string | null>(() => new URLSearchParams(location.search).get("date"));
  const [list, setList] = useState<PapersResponse | null>(null);
  // Category filter: a sub-category id, a top-level id, UNCATEGORIZED, or null for all papers.
  const [filter, setFilter] = useState<string | null>(() => new URLSearchParams(location.search).get("cat"));
  const [error, setError] = useState<string | null>(null);
  const stripRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetchDates()
      .then((d) => {
        const partial = d.processing.filter((p) => (p.papersDone ?? 0) > 0);
        const shown: ShownDate[] = [
          ...d.ready.map((r) => ({ date: r.date, paperCount: r.paperCount, papersDone: r.paperCount })),
          ...partial.map((p) => ({ date: p.date, paperCount: p.paperCount ?? 0, papersDone: p.papersDone ?? 0 })),
        ].sort((a, b) => b.date.localeCompare(a.date));
        setDates(shown);
        setWaiting(d.processing.filter((p) => !(p.papersDone > 0)));
        setSelected((cur) => cur ?? shown[0]?.date ?? null);
      })
      .catch(() => setError(s.loadError));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!selected || !dates) return;
    const url = new URL(location.href);
    url.searchParams.set("date", selected);
    history.replaceState(null, "", url);
    stripRef.current?.querySelector(`[data-date="${selected}"]`)?.scrollIntoView({ inline: "center", block: "nearest" });
    setList(null);
    if (!dates.some((d) => d.date === selected)) return;
    fetchPapers(selected)
      .then(setList)
      .catch(() => setError(s.loadError));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, dates]);

  useEffect(() => {
    const url = new URL(location.href);
    if (filter) url.searchParams.set("cat", filter);
    else url.searchParams.delete("cat");
    history.replaceState(null, "", url);
  }, [filter]);

  function pickDate(date: string) {
    setSelected(date);
    setFilter(null);
  }

  if (error) return <Notice>{error}</Notice>;
  if (!dates) return <Notice>{s.loading}</Notice>;
  if (dates.length === 0 && waiting.length === 0) return <Notice>{s.noDates}</Notice>;

  const isShown = dates.some((d) => d.date === selected);
  const groups = list ? groupPapers(list.papers) : [];
  const active = filterExists(groups, filter) ? filter : null;
  const shownPapers = list ? list.papers.filter((p) => matchesFilter(p, active)) : [];
  const toggle = (value: string) => setFilter(active === value ? null : value);
  const strip = [...dates].reverse(); // oldest → newest, so the newest sits at the right edge

  return (
    <main className="mx-auto w-full max-w-4xl flex-1 px-4 pb-16">
      <p className="mt-6 text-sm text-stone-500 dark:text-stone-400">{s.tagline}</p>

      {waiting.map((p) => (
        <div
          key={p.date}
          className="mt-4 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-700/60 dark:bg-amber-950/40 dark:text-amber-200"
        >
          {s.processing(p.date, p.papersDone ?? 0, p.paperCount ?? 0)}
        </div>
      ))}

      <div className="mt-5 flex items-end gap-3">
        <div ref={stripRef} className="no-scrollbar flex min-w-0 flex-1 gap-2 overflow-x-auto pb-1">
          {strip.map((d) => {
            const partial = d.papersDone < d.paperCount;
            const active = d.date === selected;
            return (
              <button
                key={d.date}
                data-date={d.date}
                onClick={() => pickDate(d.date)}
                title={partial ? s.partial(d.papersDone, d.paperCount) : undefined}
                className={`flex shrink-0 flex-col items-center rounded-xl border px-3 py-1.5 text-sm transition ${
                  active
                    ? "border-amber-500 bg-amber-500 text-white shadow-sm"
                    : "border-stone-200 bg-white hover:border-stone-400 dark:border-stone-800 dark:bg-stone-900 dark:hover:border-stone-600"
                }`}
              >
                <span className="text-xs opacity-80">{weekday(d.date, lang)}</span>
                <span className="font-medium tabular-nums">{d.date.slice(5)}</span>
                {partial && (
                  <span
                    className={`text-[10px] leading-tight tabular-nums ${
                      active ? "text-white/90" : "text-amber-700 dark:text-amber-400"
                    }`}
                  >
                    {d.papersDone}/{d.paperCount}
                  </span>
                )}
              </button>
            );
          })}
        </div>
        <label className="shrink-0 text-xs text-stone-500 dark:text-stone-400">
          <span className="mb-1 block">{s.jumpTo}</span>
          <input
            type="date"
            value={selected ?? ""}
            min={strip[0]?.date}
            max={strip[strip.length - 1]?.date}
            onChange={(e) => e.target.value && pickDate(e.target.value)}
            className="rounded-lg border border-stone-300 bg-white px-2 py-1.5 text-sm text-stone-900 dark:border-stone-700 dark:bg-stone-900 dark:text-stone-100"
          />
        </label>
      </div>

      {selected && (
        <h1 className="mt-6 text-lg font-semibold">
          {selected}
          {isShown && list && (
            <span className="ml-2 text-sm font-normal text-stone-500 dark:text-stone-400">
              {active ? `${shownPapers.length} / ${list.papers.length}` : list.papers.length} {s.papers}
            </span>
          )}
        </h1>
      )}

      {isShown && list && list.pending > 0 && (
        <p className="mt-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-700/60 dark:bg-amber-950/40 dark:text-amber-200">
          {s.missing(list.pending)}
        </p>
      )}

      {isShown && list && groups.length > 0 && (
        <CategoryPanel groups={groups} total={list.papers.length} active={active} lang={lang} onToggle={toggle}
                       onClear={() => setFilter(null)} />
      )}

      {!isShown ? (
        <p className="mt-4 text-stone-500 dark:text-stone-400">{s.notReady}</p>
      ) : !list ? (
        <p className="mt-4 text-stone-500 dark:text-stone-400">{s.loading}</p>
      ) : (
        <ol className="mt-3 divide-y divide-stone-200 dark:divide-stone-800">
          {shownPapers.map((p, i) => (
            <PaperRow key={p.id} paper={p} rank={i + 1} lang={lang} onFilter={(c) => setFilter(c)} />
          ))}
        </ol>
      )}
    </main>
  );
}

const chipBase = "inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-sm transition";
const chipIdle =
  "border-stone-200 bg-white text-stone-700 hover:border-amber-400 dark:border-stone-700 dark:bg-stone-900 dark:text-stone-300 dark:hover:border-amber-500";
const chipActive = "border-amber-500 bg-amber-500 text-white shadow-sm";

function Count({ n, on }: { n: number; on: boolean }) {
  return (
    <span className={`tabular-nums text-xs ${on ? "text-white/90" : "text-stone-400 dark:text-stone-500"}`}>{n}</span>
  );
}

// The day's categories: one box per top-level category, holding its sub-categories with paper counts.
// Clicking a sub-category (or a top-level title) filters the list; clicking it again shows everything.
function CategoryPanel({ groups, total, active, lang, onToggle, onClear }: {
  groups: CategoryGroup[];
  total: number;
  active: string | null;
  lang: Lang;
  onToggle: (value: string) => void;
  onClear: () => void;
}) {
  const s = t(lang);
  return (
    <section aria-label={s.categories} className="mt-4">
      <div className="mb-2 flex items-center gap-2">
        <h2 className="text-sm font-medium text-stone-600 dark:text-stone-400">{s.categories}</h2>
        <button onClick={onClear} aria-pressed={!active} className={`${chipBase} py-0.5 ${!active ? chipActive : chipIdle}`}>
          {s.all} <Count n={total} on={!active} />
        </button>
      </div>
      <div className="flex flex-wrap gap-2">
        {groups.map((g) => {
          const majorOn = active === g.major;
          return (
            <div
              key={g.major}
              className={`min-w-0 max-w-full rounded-xl border p-2 ${
                majorOn
                  ? "border-amber-500 bg-amber-50 dark:bg-amber-950/30"
                  : "border-stone-200 bg-stone-100/60 dark:border-stone-800 dark:bg-stone-900/50"
              }`}
            >
              <button
                onClick={() => onToggle(g.major)}
                aria-pressed={majorOn}
                className="mb-1.5 flex items-center gap-1.5 px-0.5 text-xs font-semibold text-stone-600 hover:text-amber-700 dark:text-stone-300 dark:hover:text-amber-400"
              >
                {g.major === UNCATEGORIZED ? s.uncategorized : majorName(g.major, lang)}
                <span className="font-normal tabular-nums text-stone-400 dark:text-stone-500">{g.count}</span>
              </button>
              {g.minors.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {g.minors.map((m) => {
                    const on = active === m.minor;
                    return (
                      <button
                        key={m.minor}
                        onClick={() => onToggle(m.minor)}
                        aria-pressed={on}
                        title={minorDesc(m.minor, lang)}
                        className={`${chipBase} ${on ? chipActive : chipIdle}`}
                      >
                        {minorName(m.minor, lang)} <Count n={m.count} on={on} />
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

function PaperRow({ paper: p, rank, lang, onFilter }: {
  paper: PaperListItem;
  rank: number;
  lang: Lang;
  onFilter: (category: string) => void;
}) {
  const s = t(lang);
  const category = categoryOf(p);
  const shown = p.authors.slice(0, 4);
  const badge = p.withdrawnAt
    ? s.withdrawn
    : p.summaryStatus === "fatal"
      ? s.unavailable
      : p.summaryStatus !== "ok"
        ? s.inProgress
        : null;
  return (
    <li className="flex gap-4 py-4">
      <span className="w-6 shrink-0 pt-0.5 text-right text-sm tabular-nums text-stone-400">{rank}</span>
      <div className="min-w-0 flex-1">
        <a
          href={`/paper?id=${encodeURIComponent(p.id)}&lang=${lang}`}
          target="_blank"
          rel="noopener"
          className="font-medium leading-snug text-stone-900 decoration-amber-500 decoration-2 underline-offset-4 hover:underline dark:text-stone-100"
        >
          {p.title}
        </a>
        {lang === "zh" && p.titleZh && (
          <p className="mt-0.5 leading-snug text-stone-700 dark:text-stone-300">{p.titleZh}</p>
        )}
        <p className="mt-1 text-sm text-stone-500 dark:text-stone-400">
          {shown.join(", ")}
          {p.authors.length > shown.length && ` ${s.authorsMore(p.authors.length - shown.length)}`}
        </p>
        <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-stone-500 dark:text-stone-400">
          <span className="tabular-nums">▲ {p.upvotes}</span>
          {category && (
            <button
              onClick={() => onFilter(category.minor)}
              title={minorDesc(category.minor, lang)}
              className="rounded-full border border-amber-300 px-2 py-0.5 text-amber-800 hover:bg-amber-50 dark:border-amber-700/60 dark:text-amber-300 dark:hover:bg-amber-950/40"
            >
              {majorName(category.major, lang)} · {minorName(category.minor, lang)}
            </button>
          )}
          {p.model && p.summaryStatus === "ok" && <span>· {p.model}</span>}
          {badge && (
            <span className="rounded-full bg-stone-200 px-2 py-0.5 text-stone-700 dark:bg-stone-800 dark:text-stone-300">
              {badge}
            </span>
          )}
        </div>
      </div>
    </li>
  );
}

function Notice({ children }: { children: React.ReactNode }) {
  return <main className="mx-auto w-full max-w-4xl px-4 py-16 text-stone-500 dark:text-stone-400">{children}</main>;
}
