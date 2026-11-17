"""
Grading rubric version and public changelog.

Every report records the rubric version it was graded with, so scores from different versions are
never silently compared. Bump RUBRIC_VERSION and add a changelog entry whenever a change can move
scores (prompts, weights, aggregation, grade curve, checklist).
"""

from typing import Dict, List

RUBRIC_VERSION = "v13"

GRADE_CUTOFFS = (
    (94.0, "A+"),
    (89.0, "A"),
    (85.0, "A-"),
    (81.0, "B+"),
    (77.0, "B"),
    (72.0, "B-"),
    (68.0, "C+"),
    (62.0, "C"),
    (55.0, "C-"),
    (40.0, "D"),
)


def calculate_letter_grade(score: float) -> str:
    """Maps a 0-100 score to a letter grade on the prior curve (a typical vibe-coded project, ~62, earns a C)."""
    for cutoff, grade in GRADE_CUTOFFS:
        if score >= cutoff:
            return grade
    return "F"

# Newest first. Plain language: this is shown publicly on /changelog.
RUBRIC_CHANGELOG: List[Dict[str, object]] = [
    {
        "version": "v13",
        "date": "2026-11-17",
        "title": "Unified Stratified Bucketing, Dynamic Category Weights & Tri-State Probes",
        "changes": [
            "Replaced alphabetical batching with Stratified Role-Based Bucketing (Entrypoints, Core Domain, UI/Templates/Onboarding, Tests/Docs) so large multi-language repos always surface frontend/UI and onboarding code.",
            "Added canonical deep_audit telemetry parity, manifest-first Android/iOS platform priority, and tailscale.com installer allowlisting.",
            "Unified v13/v13a scoring with tri-state atomic probes, dynamic category weights, and exact static LOC for file split plans.",
        ],
    },
    {
        "version": "v10.3",
        "date": "2026-11-16",
        "title": "Runtime Envelopes for Native GUIs, Addons, Widgets, Libraries & Trainers",
        "changes": [
            "Added universal Runtime Envelope detection (host plugins/mods, desktop widgets/tray tools, native GUIs, single-player trainers/save tools, and libraries/SDKs) with guardrails preventing native/overlay apps from being misrouted to CLI rubrics.",
            "Added filename and banner fingerprinting in ingestion to exclude bundled single-header/amalgamated third-party libraries (Dear ImGui, stb_*, sqlite3, nlohmann/json, etc.) from author LOC, complexity, and audit batches even in single-commit repos.",
            "Fixed over-capping of domain-integrated plugins, overlays, widgets, and trainers as generic prompt clones, and tightened batch audit rules against false-positive null-check and wrong-platform complaints.",
        ],
    },
    {
        "version": "v10.2",
        "date": "2026-11-16",
        "title": "Code Health (15%) in Raw Rubric, Host/SDK Awareness & Stricter Standards",
        "changes": [
            "Promoted Engine v10.2 to the default production rubric: adds Code Health (15% weight—cyclomatic complexity, oversized files/functions, 10-line duplicated logic blocks, dead code, and swallowed errors) directly into the Raw Rubric score.",
            "Added deterministic option/function documentation parity checks (comparing GUI, CLI, or dual-mode options against inline help and documentation) so complex tools aren't credited for easy onboarding when options lack explanations.",
            "Added automatic in-repo host/SDK header capability discovery (e.g. Nexus.h) plus optional [+] external repo/header reference context and branch/subpath targeting (/tree/<branch>/<subpath>), so plugins aren't penalized for host-managed windows or options.",
            "Removed artificial repo file-count and size caps during ingestion, added multi-version archive root detection, and added a 'Beauty in Simplicity' callout (Laser-Focused / Balanced / Kitchen-Sink).",
        ],
    },
    {
        "version": "v10",
        "date": "2026-11-15",
        "title": "Unified 30B-A3B MoE engine via CUDA llama.cpp (--cpu-moe)",
        "changes": [
            "Replaced the dual 7B + 14B Ollama pipeline with a single unified Qwen3 30B-A3B (30.5B Mixture-of-Experts, 3B active parameters) model running locally through CUDA-accelerated llama.cpp (--cpu-moe).",
            "Hybrid GPU+CPU execution keeps attention layers and the 32K-token KV cache resident in GPU VRAM while running MoE expert weights from system RAM on CPU—eliminating model swaps and enabling larger 12K-token code audit batches.",
            "Streamlined evaluation into four stages: deterministic dossier & static scan, context/dossier synthesis, large-batch code audit with exact file/line citations, and calibrated 5-factor scoring with actionable next steps.",
            "Projects such as code libraries, compilers, and developer tools are evaluated on what they actually are rather than penalized for lacking web GUI themes or save buttons.",
        ],
    },
    {
        "version": "v9.4",
        "date": "2026-11-14",
        "title": "Submitter context, grounding checks, and 7 domain archetype lenses",
        "changes": [
            "Added developer intake context (project profile, provenance, guest access, and custom hook) to weight and interpret evaluations more accurately.",
            "Added stricter grounding checks between dossier claims and verified source-code quotes.",
        ],
    },
    {
        "version": "v9.3",
        "date": "2026-11-13",
        "title": "Archetype profiles, domain exemptions, and hackathon judge evaluation",
        "changes": [
            "Every project is classified into an archetype profile (Developer Tool / MCP Server, Game / Interactive Simulation, Media / Audio Lounge, Mobile / IoT, Automation Pipeline, Web Productivity, or General Software) with tailored evaluation lenses.",
            "Domain-specific exemptions are honored: developer tools and MCP servers are not penalized for technical CLI/schema terminology or lack of web screen-reader labels; games are evaluated on arcade game loop engagement and feel rather than business forms.",
            "The 14B model's persona is tuned to a discerning Hackathon Judge and power-user looking for working solo vibe-coding leverage, real-world utility, and creative superpowers rather than corporate enterprise bureaucracy.",
            "Semantic README parsing bypasses top-of-page badge walls, donation buttons (like Ko-fi), and hero images to capture the developer's true pitch, while recognizing screenshots and diagrams as positive visual proof of work.",
            "Projects without clear domain signals or with minimal READMEs gracefully fall back to an 'Unknown / General Software' archetype that evaluates on core functionality without assuming specific frameworks or workflows.",
        ],
    },
    {
        "version": "v9.2",
        "date": "2026-11-13",
        "title": "Graders first read what the project says it is",
        "changes": [
            "Before any judging, LetsVibe reads the project's own descriptions: its GitHub description and topics, the "
            "README's title, opening and table of contents (never the whole file, so a long README counts no more than a "
            "short one), recent release notes and commit messages that explain changes.",
            "The AI turns these into one plain sentence about what the project is and who it is for. Its quotes are checked "
            "against the descriptions, and the kind of project is taken from its files when they make it certain (for example "
            "an AI-assistant plugin or a browser extension).",
            "Every checklist sees this first, and the summary opens with it, so a project is described as what it is rather "
            "than as the closest common app type. It is context only: a feature the README promises still has to be shown "
            "by the app and its code.",
            "Projects with no README or description are noted as such, which is a fact about how easy they are to get started "
            "with.",
            "Grading is now repeatable: the AI models use a fixed seed and no randomness when they judge, so grading the same "
            "project twice gives the same answers instead of tipping close calls either way.",
        ],
    },
    {
        "version": "v9.1",
        "date": "2026-11-13",
        "title": "Problems are found as reliably as strengths",
        "changes": [
            "Every checklist question now has a real 'no' answer. Before, questions such as 'does the app tell people when "
            "something goes wrong?' could only be answered 'clearly', 'sometimes' or 'not shown', so the code review could only "
            "ever add praise.",
            "A 'no' is backed by the code that shows the gap, for example an error that is only written to a log. 'Not there at "
            "all' counts only when every part of the code that could show it was read and none did; a scan that simply missed "
            "something is still never held against a project.",
            "For things people see (messages, confirmations, loading feedback and screen-reader labels), praise must quote "
            "something a person actually sees. Lines written to a log, print or console never count.",
            "Each answer from the code review is checked once more: the smaller AI model is shown the quoted lines and asked "
            "whether they really show the claim. Answers whose quotes are real but beside the point are dropped.",
            "Questions about the whole app (does it work from start to finish, are the features wired together, is the look "
            "consistent, do the features work together) need evidence from at least two files. When parts of the code disagree, "
            "the answer is the one halfway between them, so a few polished screens never outvote the rest.",
            "A code-review answer can no longer replace the dossier review's answer without a dispute check.",
            "'Needs work' lists every problem and every answer that falls short (for example 'mostly works' or 'only sometimes "
            "confirms an action'), with the file and line where the code shows it. Each factor's description starts with its "
            "weakest finding, so the words match the score.",
        ],
    },
    {
        "version": "v9",
        "date": "2026-11-13",
        "title": "Two independent reviews of every project, checked against each other",
        "changes": [
            "Every project now gets two independent reviews. The first is the App Dossier from v8: a written account of what "
            "people see and do, read by the larger AI model. The second reads the code itself, cut into small sections.",
            "The code review looks at one thing at a time. A smaller AI model reads each section separately for functionality, "
            "UI/UX, the user journey and creativity, and only reads the sections that can tell it something about that topic. "
            "Real-world value is still judged from the project as a whole.",
            "Every answer from the code review must quote real lines of code, and a program checks each quote against the "
            "file. Answers whose quotes are not in the code are thrown away.",
            "The two reviews are compared item by item. When they agree, the answer stands. When only one found evidence, "
            "that evidence is used, so things a scan could not see are no longer simply given middle points. When they "
            "disagree, the larger model rereads the exact quoted lines from both sides and decides which claim the code "
            "supports; if neither holds up, the item counts as not shown. Answers are never averaged.",
            "Scores still come from fixed points per answer, with the same weights as v8.",
            "Reports now say where findings were seen (file and line) and include a plain-English description of the "
            "code's complexity. Complexity is shown for information only and never changes the score.",
            "A grade that runs longer than 3 hours stops and publishes nothing.",
        ],
    },
    {
        "version": "v8",
        "date": "2026-11-12",
        "title": "Graded on what people experience, not on how the code is written",
        "changes": [
            "Projects are graded on five things, in order of weight: real-world value, creativity, the user journey (from "
            "installing it to closing it), functionality and UI/UX. Code health is shared with each project's owner only, as "
            "notes for the developer, and never counts toward the score.",
            "No AI reads the code any more. A program first writes an App Dossier of what people see and do: what the "
            "project says it is, its screens, the text on them, its own features, how many devices can use it at once, "
            "how much setup it needs before first use, how it launches and saves, and any ads, paywalls or forced sign-in.",
            "The project is compared with common app types people make from a single prompt (to-do lists, stock viewers, "
            "space shooters and so on), and the closest match is given to the reviewer as a fact.",
            "Problems a program can prove (for example back-end features no screen calls, or a config file that cannot be "
            "read) count against functionality, per 1,000 lines of code. Anything a scan could not find is never counted as missing.",
            "The reviewer answers fixed checklists, one topic at a time, and quotes the dossier for every answer. It never "
            "picks a number: each answer is worth fixed points, so every score can be traced to the answers behind it.",
            "The written review only restates the checked answers, and 'Needs work' lists only real problems (such as a "
            "heavy setup, nothing being saved, or a standard copy of a common idea), never invented advice.",
            "Safety is no longer graded: LetsVibe is not a security tool, and a grade never says software is safe to run.",
        ],
    },
    {
        "version": "v7",
        "date": "2026-11-11",
        "title": "Graded on a curve, with a leaner review",
        "changes": [
            "Letter grades now come from a curve (a bell curve). AI reviewers hedge toward the middle, so a raw score "
            "says little on its own. Your letter depends on how far your score sits from the average, in standard deviations; "
            "two published reference projects set the bell at first, and your category's own scores take over as it grows. Everything behind the "
            "curve, every run included, is on the Grading curve page.",
            "Before review, files are sorted by name and folder (no AI): docs, licenses, build/config files and tests "
            "are skipped (they reach the reviewers as measurements), and story, dialogue or level content is sampled. "
            "Each chunk lists only its own files, which cut the reading by about a fifth.",
            "The yes/no checklist went from 33 to 23 questions: duplicates were removed, and code-health questions "
            "that measurements already answer (tests, complexity, dependencies) or that AI-written code almost always "
            "passes (clear names) are no longer asked.",
            "Safety is always reviewed for code that makes network calls, runs other programs, uses threads or files, "
            "loops forever or silently ignores errors, and setup wizards built inside a page are now recognized.",
        ],
    },
    {
        "version": "v6",
        "date": "2026-11-11",
        "title": "Vibe-coded software, graded as vibe-coded software",
        "changes": [
            "Projects are compared with other vibe-coded projects (programs written by AI from someone's prompts), "
            "never with software written by professional developers. Score bands were rewritten accordingly: 62 is a typical working vibe-coded project.",
            "New factor: User Journey, the user's path from install and first-run setup, through finding their way "
            "(including how deep settings are buried), to finishing the main task and being told it worked.",
            "Maintainability and Architecture were merged into Code Health. Engineering factors now carry roughly "
            "10-25% of the weight in most categories and are framed as 'what a pro team would do next'.",
            "Letter grades reflect standing among similar projects once a category has 30 or more; until then the "
            "rubric curve is used (62 = C). Reports show their rank within their category.",
            "The checklist was rephrased for vibe-coded projects, and complexity now counts mainly when it is widespread "
            "rather than for a few large functions.",
        ],
    },
    {
        "version": "v5",
        "date": "2026-11-11",
        "title": "Owner input before publishing, and corrections",
        "changes": [
            "When the review needs more context about specific code, the report is not published or scored down. "
            "Instead the owner is asked to explain those lines; the report is published after review, and requests "
            "left unanswered for 7 days are deleted.",
            "Owners can report an error in their review. Each report is checked against the code; confirmed errors "
            "are corrected and marked as corrected on the report.",
        ],
    },
    {
        "version": "v4",
        "date": "2026-11-11",
        "title": "Device-aware grading and fairer factor scoring",
        "changes": [
            "Each project is classified first (category, target platforms and hardware) and graded against "
            "the device it is built for, so a microcontroller dashboard is not judged like desktop software.",
            "Screen size, chip and memory are read from the project's own build files and source code.",
            "Parts of a codebase that contain no user interface, I/O or user-facing features no longer count "
            "for or against those factors. If no part contains them, the whole-repository review decides.",
            "Factor weights are kept inside the published range for each project category.",
            "Detected secrets are reported by type only; their location is never published.",
        ],
    },
    {
        "version": "v3",
        "date": "2026-11-11",
        "title": "Reliability fixes",
        "changes": [
            "Fixed model prompts that were too long for the local models' context window, which caused some "
            "evaluations to return generic placeholder results. Evaluations that fail now produce no ranking "
            "at all instead of a misleading one.",
            "Vendored third-party libraries are excluded so projects are graded on their own code.",
        ],
    },
    {
        "version": "v2",
        "date": "2026-11-11",
        "title": "Calibration",
        "changes": [
            "Letter-grade curve aligned with the scoring rubric (a typical working project is a C).",
            "Yes/no evidence checklist blended with model scores to anchor results in concrete facts.",
        ],
    },
    {
        "version": "v1",
        "date": "2026-11-08",
        "title": "Beta",
        "changes": ["Initial dual-model (7B + 14B) local grading pipeline."],
    },
]
