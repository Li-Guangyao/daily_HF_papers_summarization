import { lazy, Suspense, useEffect, useState } from "react";
import { initialLang, t, type Lang } from "./i18n";
import ListPage from "./ListPage";
// The detail page carries KaTeX (math rendering); load it only when a paper is opened.
const DetailPage = lazy(() => import("./DetailPage"));

export default function App() {
  const [lang, setLang] = useState<Lang>(initialLang);
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));
  const s = t(lang);
  const onDetail = location.pathname.startsWith("/paper");

  useEffect(() => {
    document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
    try {
      localStorage.setItem("lang", lang);
    } catch {}
    const url = new URL(location.href);
    url.searchParams.set("lang", lang);
    history.replaceState(null, "", url);
  }, [lang]);

  function toggleTheme() {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
    try {
      localStorage.setItem("theme", next ? "dark" : "light");
    } catch {}
  }

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-20 border-b border-stone-200 bg-stone-50/90 backdrop-blur dark:border-stone-800 dark:bg-stone-950/90">
        <div className="mx-auto flex h-14 max-w-screen-2xl items-center gap-3 px-4">
          <a href={`/?lang=${lang}`} className="flex min-w-0 items-center gap-2">
            <img src="/favicon.svg" alt="" className="h-7 w-7 shrink-0" />
            <span className="truncate font-semibold tracking-tight">{s.siteName}</span>
          </a>
          <div className="ml-auto flex items-center gap-2">
            <div className="flex rounded-lg border border-stone-300 p-0.5 text-sm dark:border-stone-700">
              {(["en", "zh"] as const).map((l) => (
                <button
                  key={l}
                  onClick={() => setLang(l)}
                  className={`rounded-md px-2.5 py-1 ${
                    lang === l
                      ? "bg-stone-900 text-white dark:bg-stone-100 dark:text-stone-900"
                      : "text-stone-600 hover:text-stone-900 dark:text-stone-400 dark:hover:text-stone-100"
                  }`}
                >
                  {l === "en" ? "EN" : "中文"}
                </button>
              ))}
            </div>
            <button
              onClick={toggleTheme}
              aria-label={s.theme}
              title={s.theme}
              className="rounded-lg border border-stone-300 p-1.5 text-stone-600 hover:text-stone-900 dark:border-stone-700 dark:text-stone-400 dark:hover:text-stone-100"
            >
              {dark ? (
                <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2">
                  <circle cx="12" cy="12" r="4" />
                  <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
                </svg>
              ) : (
                <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
                </svg>
              )}
            </button>
          </div>
        </div>
      </header>
      {onDetail ? (
        <Suspense fallback={<p className="mx-auto max-w-3xl px-4 py-16 text-stone-500">{s.loading}</p>}>
          <DetailPage lang={lang} />
        </Suspense>
      ) : (
        <ListPage lang={lang} />
      )}
    </div>
  );
}
