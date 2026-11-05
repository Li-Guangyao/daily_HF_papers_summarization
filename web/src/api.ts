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
  categoryMajor: string | null;
  categoryMinor: string | null;
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
  categoryMajor: string | null;
  categoryMinor: string | null;
  status: "pending" | "ok" | "error" | "fatal" | null;
  source: "full_text" | "arxiv_html" | "abstract" | null; // HF markdown, arXiv HTML, or abstract only
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
export interface PapersResponse {
  date: string;
  status: "ready" | "processing" | "empty";
  pending: number; // papers of this date still being analyzed (not included in `papers`)
  papers: PaperListItem[];
}

export const fetchPapers = (date: string) => get<PapersResponse>(`/api/papers?date=${encodeURIComponent(date)}`);
export const fetchPaper = (id: string) => get<PaperDetail>(`/api/papers/${encodeURIComponent(id)}`);
