// Paper categories (lib/taxonomy.json, shared with the pipeline) and the per-date grouping shown on the list page.
import taxonomy from "../../lib/taxonomy.json";
import type { Lang } from "./i18n";

export interface MinorCategory {
  id: string;
  zh: string; // short names, shown in the UI
  en: string;
  zhDesc: string; // longer descriptions, shown as tooltips
  desc: string;
}

export interface MajorCategory {
  id: string;
  zh: string;
  en: string;
  minors: MinorCategory[];
}

export const MAJORS: MajorCategory[] = taxonomy.majors;
export const UNCATEGORIZED = "none"; // filter value for papers without a category

const majorById = new Map(MAJORS.map((m) => [m.id, m]));
const minorById = new Map(MAJORS.flatMap((m) => m.minors.map((n) => [n.id, n] as const)));

export const majorName = (id: string, lang: Lang) => majorById.get(id)?.[lang] ?? id;
export const minorName = (id: string, lang: Lang) => minorById.get(id)?.[lang] ?? id;
export const minorDesc = (id: string, lang: Lang) => {
  const m = minorById.get(id);
  return m ? (lang === "zh" ? m.zhDesc : m.desc) : "";
};

interface Categorized {
  categoryMajor: string | null;
  categoryMinor: string | null;
}

// A paper's category, or null when it has none (or one the website doesn't know, e.g. from a newer taxonomy).
export function categoryOf(p: Categorized): { major: string; minor: string } | null {
  if (!p.categoryMinor || !minorById.has(p.categoryMinor)) return null;
  const major = p.categoryMinor.split(".")[0];
  return majorById.has(major) ? { major, minor: p.categoryMinor } : null;
}

export interface CategoryGroup {
  major: string; // a major id, or UNCATEGORIZED
  count: number;
  minors: { minor: string; count: number }[];
}

// The categories present in `papers`, in taxonomy order, with paper counts. Uncategorized papers come last.
export function groupPapers(papers: Categorized[]): CategoryGroup[] {
  const counts = new Map<string, number>();
  let none = 0;
  for (const p of papers) {
    const c = categoryOf(p);
    if (c) counts.set(c.minor, (counts.get(c.minor) ?? 0) + 1);
    else none++;
  }
  const groups: CategoryGroup[] = [];
  for (const major of MAJORS) {
    const minors = major.minors
      .filter((m) => counts.has(m.id))
      .map((m) => ({ minor: m.id, count: counts.get(m.id)! }));
    if (minors.length) groups.push({ major: major.id, count: minors.reduce((n, m) => n + m.count, 0), minors });
  }
  if (none) groups.push({ major: UNCATEGORIZED, count: none, minors: [] });
  return groups;
}

// filter: null (all), a major id, a minor id, or UNCATEGORIZED.
export function matchesFilter(p: Categorized, filter: string | null): boolean {
  if (!filter) return true;
  const c = categoryOf(p);
  if (filter === UNCATEGORIZED) return !c;
  return !!c && (c.minor === filter || c.major === filter);
}

// True when `filter` selects something in these groups (a stale ?cat= from another date selects nothing).
export function filterExists(groups: CategoryGroup[], filter: string | null): boolean {
  return !!filter && groups.some((g) => g.major === filter || g.minors.some((m) => m.minor === filter));
}
