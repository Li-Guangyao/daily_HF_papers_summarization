// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { normalizeMath, renderMarkdown } from "./markdown";

describe("renderMarkdown", () => {
  it("renders inline and display LaTeX with KaTeX", () => {
    const { html } = renderMarkdown("Loss $L = L_{rec} + \\lambda L_{KL}$ here.\n\n$$\n\\frac{a}{b} = \\sum_i x_i^2\n$$\n");
    expect(html).toContain('class="katex"');
    expect(html).toContain("katex-display");
    expect(html).not.toContain("$$");
    expect(html).not.toContain("\\frac");
  });

  it("renders a one-line $$ formula placed right under a sentence as display math", () => {
    const { html } = renderMarkdown("The update rule looks like this:\n$$ z_{t+1} = z_t - \\sigma \\cdot v $$\n\nHere $z_t$ is the image.");
    expect(html).toContain("katex-display");
    expect(html.match(/class="katex"/g)?.length).toBe(2);
    expect(html).not.toContain("$$");
  });

  it("renders math directly next to Chinese text", () => {
    const { html } = renderMarkdown("当$x>0$时，损失$L$会下降。");
    expect(html.match(/class="katex"/g)?.length).toBe(2);
  });

  it("accepts \\( \\) and \\[ \\] delimiters", () => {
    expect(normalizeMath("a \\(x^2\\) b")).toBe("a $x^2$ b");
    const { html } = renderMarkdown("Equation:\n\n\\[ E = mc^2 \\]\n");
    expect(html).toContain("katex-display");
  });

  it("shows a broken formula instead of throwing", () => {
    expect(() => renderMarkdown("bad $\\frac{1}{$ math")).not.toThrow();
  });

  it("builds the table of contents from level-2 headings and wraps tables", () => {
    const { html, toc } = renderMarkdown("## In a nutshell\nx\n\n## Results\n\n| a | b |\n|---|---|\n| 1 | 2 |\n");
    expect(toc).toEqual([
      { id: "section-1", text: "In a nutshell" },
      { id: "section-2", text: "Results" },
    ]);
    expect(html).toContain('<div class="table-scroll"><table>');
  });

  it("removes scripts and event handlers from model output", () => {
    const { html } = renderMarkdown('Hi <script>alert(1)</script><img src=x onerror="alert(1)"> $x$');
    expect(html).not.toContain("<script");
    expect(html).not.toContain("onerror");
    expect(html).toContain('class="katex"');
  });
});
