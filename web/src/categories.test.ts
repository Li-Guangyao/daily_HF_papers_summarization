import { describe, expect, it } from "vitest";
import { MAJORS, UNCATEGORIZED, filterExists, groupPapers, matchesFilter, minorName } from "./categories";

const paper = (minor: string | null) => ({ categoryMajor: minor?.split(".")[0] ?? null, categoryMinor: minor });

describe("categories", () => {
  it("has unique ids that start with their major id", () => {
    const ids = MAJORS.flatMap((m) => m.minors.map((n) => n.id));
    expect(new Set(ids).size).toBe(ids.length);
    for (const m of MAJORS) for (const n of m.minors) expect(n.id.startsWith(`${m.id}.`)).toBe(true);
  });

  it("groups papers in taxonomy order with counts, uncategorized last", () => {
    const papers = [paper("llm.agents"), paper("cv.generation"), paper("llm.agents"), paper("llm.rag"), paper(null),
      paper("made.up")];
    expect(groupPapers(papers)).toEqual([
      { major: "cv", count: 1, minors: [{ minor: "cv.generation", count: 1 }] },
      { major: "llm", count: 3, minors: [{ minor: "llm.rag", count: 1 }, { minor: "llm.agents", count: 2 }] },
      { major: UNCATEGORIZED, count: 2, minors: [] },
    ]);
  });

  it("filters by minor, major or uncategorized", () => {
    const papers = [paper("llm.agents"), paper("llm.rag"), paper("cv.3d"), paper(null)];
    expect(papers.filter((p) => matchesFilter(p, "llm.agents"))).toHaveLength(1);
    expect(papers.filter((p) => matchesFilter(p, "llm"))).toHaveLength(2);
    expect(papers.filter((p) => matchesFilter(p, UNCATEGORIZED))).toEqual([paper(null)]);
    expect(papers.filter((p) => matchesFilter(p, null))).toHaveLength(4);
    const groups = groupPapers(papers);
    expect(filterExists(groups, "cv.3d")).toBe(true);
    expect(filterExists(groups, "mm.vlm")).toBe(false);
  });

  it("uses short names", () => {
    expect(minorName("llm.agents", "zh")).toBe("智能体");
    expect(minorName("llm.agents", "en")).toBe("Agents");
  });
});
