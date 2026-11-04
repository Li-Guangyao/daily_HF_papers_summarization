export interface ReadyDate {
  date: string;
  paperCount: number;
}

export interface ProcessingDate {
  date: string;
  paperCount: number;
  papersDone: number;
}

export interface PaperListItem {
  id: string;
  title: string;
  titleZh: string | null;
  authors: string[];
  upvotes: number;
  thumbnail: string | null;
  withdrawnAt: string | null;
  summaryStatus: "pending" | "ok" | "error" | "fatal" | null;
  model: string | null;
}

export interface PaperDetail {
  id: string;
  dailyDate: string;
  title: string;
  titleZh: string | null;
  authors: string[];
  abstract: string | null;
  publishedAt: string | null;
  projectPage: string | null;
  githubRepo: string | null;
  githubStars: number | null;
  upvotes: number;
  hfAiSummary: string | null;
  hfAiKeywords: string[];
  withdrawnAt: string | null;
  status: "pending" | "ok" | "error" | "fatal" | null;
  source: "full_text" | "abstract" | null;
  summaryEn: string | null;
  summaryZh: string | null;
  model: string | null;
  enProvider: string | null;
  zhProvider: string | null;
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) throw Object.assign(new Error(`HTTP ${res.status}`), { status: res.status });
  return res.json() as Promise<T>;
}

export const fetchDates = () => get<{ ready: ReadyDate[]; processing: ProcessingDate[] }>("/api/dates");
export const fetchPapers = (date: string) =>
  get<{ date: string; papers: PaperListItem[] }>(`/api/papers?date=${encodeURIComponent(date)}`);
export const fetchPaper = (id: string) => get<PaperDetail>(`/api/papers/${encodeURIComponent(id)}`);
