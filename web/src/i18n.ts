export type Lang = "en" | "zh";

const strings = {
  en: {
    siteName: "HF Papers Digest",
    tagline: "Detailed walkthroughs of Hugging Face daily papers",
    jumpTo: "Jump to date",
    papers: "papers",
    processing: (date: string, done: number, total: number) =>
      `Papers for ${date} are being analyzed (${done} / ${total}).`,
    notReady: "This date has no papers yet, or they're still being analyzed.",
    missing: (n: number) => `${n} more ${n === 1 ? "paper is" : "papers are"} still being analyzed and will appear when done.`,
    partial: (done: number, total: number) => `${done} of ${total} analyzed`,
    loading: "Loading…",
    loadError: "Couldn't load data. Please try again later.",
    noDates: "No papers have been published yet.",
    inProgress: "AI summary in progress",
    unavailable: "AI summary unavailable",
    withdrawn: "Withdrawn",
    by: "by",
    via: "via",
    authorsMore: (n: number) => `+${n} more`,
    published: "Published",
    listed: "Listed on HF",
    openPdf: "Open PDF",
    onThisPage: "On this page",
    hfSummary: "Hugging Face's one-line summary",
    abstract: "Abstract",
    noSummaryNote: "Our detailed AI walkthrough isn't available for this paper yet. Here is what we have:",
    notFound: "Paper not found.",
    back: "All papers",
    theme: "Toggle dark mode",
  },
  zh: {
    siteName: "HF 每日论文精读",
    tagline: "Hugging Face 每日论文的详细解读",
    jumpTo: "跳转到日期",
    papers: "篇论文",
    processing: (date: string, done: number, total: number) => `${date} 的论文正在解读中（${done} / ${total}）。`,
    notReady: "该日期暂无论文，或仍在解读中。",
    missing: (n: number) => `还有 ${n} 篇论文正在解读，完成后会自动补上。`,
    partial: (done: number, total: number) => `已解读 ${done} / ${total}`,
    loading: "加载中…",
    loadError: "数据加载失败，请稍后重试。",
    noDates: "暂时还没有发布的论文。",
    inProgress: "AI 解读生成中",
    unavailable: "暂无 AI 解读",
    withdrawn: "已撤回",
    by: "由",
    via: "经",
    authorsMore: (n: number) => `等 ${n} 人`,
    published: "发表于",
    listed: "收录于 HF",
    openPdf: "打开 PDF",
    onThisPage: "本页目录",
    hfSummary: "Hugging Face 一句话摘要",
    abstract: "摘要",
    noSummaryNote: "这篇论文的详细 AI 解读暂不可用，以下是现有信息：",
    notFound: "未找到该论文。",
    back: "全部论文",
    theme: "切换深色模式",
  },
};

export type Strings = (typeof strings)["en"];
export const t = (lang: Lang): Strings => strings[lang];

export function initialLang(): Lang {
  const fromUrl = new URLSearchParams(location.search).get("lang");
  if (fromUrl === "en" || fromUrl === "zh") return fromUrl;
  try {
    const saved = localStorage.getItem("lang");
    if (saved === "en" || saved === "zh") return saved;
  } catch {}
  return navigator.language.toLowerCase().startsWith("zh") ? "zh" : "en";
}
