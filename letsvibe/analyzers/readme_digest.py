"""
README digest for the AI reviewers (no AI involved).

The reviewers get a fixed budget of README text. Taking the first N characters as written wastes it on
banners, badges and tables of contents, so instead the README is cleaned, split into sections by heading,
and the budget is filled by priority: what the project is, its features, how to install and use it, then
anything else. Licence, contributing, support/donation and changelog sections are left out. A one-line
outline of every heading goes first, so reviewers can see how extensive the documentation is even when
only part of it fits.
"""
import re
from typing import List, Tuple

BUDGET_CHARS = 3000
OUTLINE_CHARS = 320
# (category, share of the budget) in priority order; unused budget flows to the next category
BUDGET_SHARES = (("intro", 0.30), ("features", 0.33), ("setup", 0.27), ("other", 0.10))

_DROP = re.compile(r"licen[cs]e|contribut|support|donat|sponsor|ko-?fi|patreon|credit|acknowledg|changelog|"
                   r"release notes|table of contents|^\W*contents\W*$|\btoc\b|authors|code of conduct|star history|"
                   r"show your support|buy me a coffee", re.I)
_FEATURES = re.compile(r"feature|highlight|what (it|this|is)|what does|overview|about|why|capabilit|how it works|"
                       r"at a glance|tour", re.I)
_SETUP = re.compile(r"install|quick ?start|getting started|set ?up|usage|how to (use|run|play)|requirement|"
                    r"prerequisite|download|\brun\b|running|launch|command[- ]line|\bcli\b|build|platform|compatib|"
                    r"configur|controls|commands", re.I)
_DEV = re.compile(r"\btest|develop|architecture|internals|technical reference|repository structure|api reference", re.I)

_URL = r"\((?:[^()\s]|\([^()\s]*\))*\)"  # link target, allowing one level of parentheses inside it
_MD_IMAGE = re.compile(r"!\[[^\]]*\]" + _URL)
_MD_LINK = re.compile(r"\[([^\]]*)\]" + _URL)
_HTML_TAG = re.compile(r"<img\b[^>]*>|</?[a-zA-Z][^>]*>")
_TOC_LINE = re.compile(r"^\s*[*-]\s*\[.*\]\(#[^)]*\)\s*$", re.M)
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_EMPTY_TABLE_ROW = re.compile(r"^[\s|:\-]*$")


def clean(text: str) -> str:
    """Markdown/HTML decoration removed: images, badges, HTML tags, ToC link lists, dividers; links keep their text."""
    text = _TOC_LINE.sub("", text)
    text = _MD_IMAGE.sub("", text)          # removes badge images too: [![..](..)](..) -> [](..)
    text = _MD_LINK.sub(lambda m: m.group(1), text)
    text = _HTML_TAG.sub("", text)
    out: List[str] = []
    in_code, code_lines = False, 0
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            code_lines = 0
            out.append(line)
            continue
        if in_code:
            code_lines += 1
            if code_lines == 7:
                out.append("    ...")
            if code_lines > 6:
                continue
        elif stripped in ("---", "***", "___") or (stripped and _EMPTY_TABLE_ROW.match(stripped) and "|" in stripped):
            continue
        out.append(line.rstrip())
    text = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _sections(text: str) -> List[Tuple[int, str, str]]:
    """(level, heading, body) in order; text before the first heading has level 0 and heading ''."""
    sections: List[Tuple[int, str, List[str]]] = [(0, "", [])]
    in_code = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
        m = None if in_code else _HEADING.match(line)
        if m:
            sections.append((len(m.group(1)), m.group(2).strip(), []))
        else:
            sections[-1][2].append(line)
    return [(lvl, head, "\n".join(body).strip()) for lvl, head, body in sections]


def _category(heading: str) -> str:
    if _DROP.search(heading):
        return "drop"
    if _DEV.search(heading):  # developer-facing detail: useful, but after what users see
        return "other"
    if _SETUP.search(heading):
        return "setup"
    if _FEATURES.search(heading):
        return "features"
    return "other"


def _trim(text: str, limit: int) -> str:
    """At most `limit` characters, cut at a sentence or line end where possible."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for sep in ("\n\n", "\n", ". ", "! ", "? "):
        i = cut.rfind(sep)
        if i > limit * 0.5:
            return cut[: i + (1 if sep.strip() else 0)].rstrip() + " …"
    return cut.rstrip() + " …"


def build_readme_digest(readme: str, budget: int = BUDGET_CHARS) -> str:
    text = clean(readme)
    sections = _sections(text)
    total_lines = readme.count("\n") + 1

    # Categorise; a dropped heading drops its sub-sections too. The first H1 is the title.
    title, kept, omitted, drop_level = "", [], [], None
    for lvl, head, body in sections:
        if drop_level is not None and lvl > drop_level:
            continue
        drop_level = None
        if lvl == 1 and not title and head:
            title = head
            if body:
                kept.append(("intro", "", body))  # the title is printed once, at the top
            continue
        cat = "intro" if lvl == 0 else _category(head)
        if cat == "drop":
            omitted.append(head)
            drop_level = lvl
            continue
        if not body and cat != "intro":
            kept.append((cat, head, ""))      # keeps the heading in the outline even if it only has sub-sections
            continue
        kept.append((cat, head, body))
    # The first descriptive section counts as the introduction when the top of the README is thin
    intro_chars = sum(len(b) for c, _, b in kept if c == "intro")
    if intro_chars < 200:
        for i, (cat, head, body) in enumerate(kept):
            if cat in ("other", "features") and len(body) >= 80:
                kept[i] = ("intro", head, body)
                break

    all_headings = [h for lvl, h, _ in sections if h and h != title]
    top = [h for lvl, h, _ in sections if h and h != title and lvl <= 2 and h not in omitted]
    head = f"README outline ({total_lines} lines, {len(all_headings)} sections). Main sections: "
    tail = f". Not shown: {', '.join(omitted)}" if omitted else ""
    room = OUTLINE_CHARS - len(head) - len(tail)
    listed: List[str] = []
    for h in top:
        if len("; ".join(listed + [h])) > room:
            listed.append("…")
            break
        listed.append(h)
    body_list = "; ".join(listed) if listed else "none"
    outline = (head + body_list + tail).replace("…. Not shown", "… Not shown")

    remaining = budget - len(outline) - len(title) - 8
    parts: List[str] = []
    carry = 0
    for cat, share in BUDGET_SHARES:
        allowance = int(remaining * share) + carry
        used = 0
        for c, head, body in kept:
            if c != cat or not body:
                continue
            block = (f"## {head}\n" if head else "") + body
            room = allowance - used
            if room < 120:
                break
            piece = _trim(block, room)
            parts.append(piece)
            used += len(piece) + 2
        carry = max(0, allowance - used)

    digest = "\n\n".join(p for p in [f"# {title}" if title else "", outline, *parts] if p)
    return digest[:budget].strip()
