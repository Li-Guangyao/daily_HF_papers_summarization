import DOMPurify from "dompurify";
import { Marked } from "marked";
import markedKatex from "marked-katex-extension";

// Markdown + LaTeX → sanitized HTML. `nonStandard` lets $…$ sit directly next to text, which Chinese needs
// ("满足$x>0$的"); throwOnError=false shows a broken formula in red instead of failing the whole page.
const marked = new Marked({ gfm: true }).use(markedKatex({ throwOnError: false, nonStandard: true, output: "html" }));

// Models sometimes write \( … \) and \[ … \] despite the prompt; convert them to $ … $ and $$ … $$.
// A one-line "$$ … $$" right under a sentence also gets blank lines so it renders as a centered display formula.
export function normalizeMath(md: string): string {
  return md
    .replace(/^[ \t]*\$\$(.+?)\$\$[ \t]*$/gm, (_, m: string) => `\n$$\n${m.trim()}\n$$\n`)
    .replace(/\\\[([\s\S]+?)\\\]/g, (_, m: string) => `\n\n$$\n${m.trim()}\n$$\n\n`)
    .replace(/\\\(([\s\S]+?)\\\)/g, (_, m: string) => `$${m.trim()}$`);
}

export interface Rendered {
  html: string;
  toc: { id: string; text: string }[];
}

// LLM output comes from untrusted paper text: always sanitize before rendering.
export function renderMarkdown(md: string): Rendered {
  const raw = marked.parse(normalizeMath(md), { async: false }) as string;
  const doc = new DOMParser().parseFromString(DOMPurify.sanitize(raw), "text/html");
  const toc = [...doc.querySelectorAll("h2")].map((h, i) => {
    h.id = `section-${i + 1}`;
    return { id: h.id, text: h.textContent ?? "" };
  });
  doc.querySelectorAll("a").forEach((a) => {
    a.target = "_blank";
    a.rel = "noopener noreferrer";
  });
  // Wide tables and long display formulas scroll sideways instead of breaking the layout on phones.
  doc.querySelectorAll("table").forEach((table) => {
    const wrap = doc.createElement("div");
    wrap.className = "table-scroll";
    table.replaceWith(wrap);
    wrap.appendChild(table);
  });
  return { html: doc.body.innerHTML, toc };
}
