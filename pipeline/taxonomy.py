"""Paper categories. The list lives in lib/taxonomy.json, shared with the website."""

from __future__ import annotations

import json
import re
from pathlib import Path

TAXONOMY_PATH = Path(__file__).resolve().parent.parent / "lib" / "taxonomy.json"
TAXONOMY = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
MINORS: dict[str, str] = {m["id"]: major["id"] for major in TAXONOMY["majors"] for m in major["minors"]}

CATEGORY_RE = re.compile(r"<category>\s*([A-Za-z0-9_.-]+)\s*</category>", re.I)


def prompt_list() -> str:
    """The category list as the LLM sees it: one line per sub-category, grouped by top-level category."""
    lines = []
    for major in TAXONOMY["majors"]:
        lines.append(f"{major['en']}")
        lines += [f"- {m['id']}: {m['en']} — {m['desc']}" for m in major["minors"]]
    return "\n".join(lines)


CATEGORY_RULES = """Pick the ONE sub-category that best describes the paper's main contribution. When several fit:
- A new benchmark or evaluation study goes to llm.evaluation, unless it clearly belongs to another field's benchmark (for example a robotics or code benchmark goes to that field).
- RL used to train an LLM (RLHF, GRPO, RLVR) goes to llm.posttraining; RL for robots goes to robot.learning.
- Text-to-image and text-to-video go to cv.generation; text-to-3D goes to cv.3d.
- Vision-language-action models go to mm.embodied.
- Prefer the application category (apps.*) only when the domain itself is the point of the paper."""


def parse_category(text: str) -> tuple[str, str] | None:
    """(major, minor) from the first valid <category>…</category> tag, or None."""
    for found in CATEGORY_RE.findall(text or ""):
        minor = found.strip().lower()
        if minor in MINORS:
            return MINORS[minor], minor
    return None


def strip_category(text: str) -> str:
    return CATEGORY_RE.sub("", text)
