"""
Grading rubric version and public changelog.

Every report records the rubric version it was graded with, so scores from different versions are
never silently compared. Bump RUBRIC_VERSION and add a changelog entry whenever a change can move
scores (prompts, weights, aggregation, grade curve, checklist).
"""

from typing import Dict, List

RUBRIC_VERSION = "v9"

# Newest first. Plain language: this is shown publicly on /changelog.
RUBRIC_CHANGELOG: List[Dict[str, object]] = [
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
