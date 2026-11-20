<p align="center">
  <a href="https://ko-fi.com/todd0042">
    <img src="https://raw.githubusercontent.com/Todd0042/creator-assets/main/banners/todd_creator_banner_v1.0.png" alt="Support Todd on Ko-fi" width="100%" />
  </a>
</p>

---

# ✨ LetsVibe — The Leaderboard for Vibe-Coded Software

[![Version](https://img.shields.io/badge/Version-1.0.0-ff2a85.svg)](https://github.com/Todd0042/letsvibe/releases)
[![License: PolyForm Shield 1.0.0](https://img.shields.io/badge/License-PolyForm%20Shield%201.0.0-00f0ff.svg)](LICENSE)
[![Rubric](https://img.shields.io/badge/Rubric-v9.3%20(bell%20curve)-blueviolet.svg)](https://letsvibe.todd0042.dev/changelog)
[![Python: 3.11+](https://img.shields.io/badge/Python-3.11%2B-39ff14.svg)]()
[![AI: 100% local](https://img.shields.io/badge/AI-100%25%20Local%20(Ollama)-black.svg)](https://ollama.com)
[![Models](https://img.shields.io/badge/Models-Qwen2.5--Coder%207B%20%2B%2014B%20%2B%20mxbai--embed-teal.svg)]()
[![Support on Ko-fi](https://img.shields.io/badge/Ko--fi-Support%20Creator-F16061?style=flat&logo=ko-fi&logoColor=white)](https://ko-fi.com/todd0042)

> 🤖 *LetsVibe is itself vibe-coded: directed by [Todd0042](https://github.com/Todd0042), written by Antigravity (Gemini) and then Claude Code. [Who did what](#-3-how-letsvibe-itself-was-made).*

> **An independent, privacy-first leaderboard that grades vibe-coded software — programs written by AI from someone's prompts — against other vibe-coded software, never against professional developers. Every grade is produced by open AI models running on one local graphics card, anchored by plain code measurements, and placed on a fully published bell curve.**

🌐 **Live site:** [letsvibe.todd0042.dev](https://letsvibe.todd0042.dev)

<p align="center">
  <img src="screenshots/podium.png" alt="LetsVibe leaderboard podium" width="100%">
</p>

---

## 📖 Table of Contents

* **Part 1 — Overview**
  * [🎨 What Is LetsVibe?](#-what-is-letsvibe)
  * [🚀 How It Works in 3 Steps](#-how-it-works-in-3-steps)
  * [📸 Visual Tour](#-visual-tour)
  * [🧭 The 5 Factors](#-the-5-factors)
  * [🔔 Graded on a Published Bell Curve](#-graded-on-a-published-bell-curve)
  * [🔒 Privacy Promises](#-privacy-promises)
  * [🛠️ Run It Yourself](#️-run-it-yourself)
* **Part 2 — Technical Reference**
  * [1. Architecture & the Grading Pipeline ([evaluator/pipeline.py](letsvibe/evaluator/pipeline.py))](#1-architecture--the-grading-pipeline)
  * [2. Ingestion & the Name-Based File Filter ([core/ingest.py](letsvibe/core/ingest.py), [evaluator/triage.py](letsvibe/evaluator/triage.py))](#2-ingestion--the-name-based-file-filter)
  * [3. Plain Measurements & User-Journey Evidence ([analyzers/](letsvibe/analyzers/))](#3-plain-measurements--user-journey-evidence)
  * [4. Classification & Category Weights ([evaluator/classifier.py](letsvibe/evaluator/classifier.py), [evaluator/weights.py](letsvibe/evaluator/weights.py))](#4-classification--category-weights)
  * [5. Experience Grading: Dossier, Checklists & Points ([evaluator/experience/](letsvibe/evaluator/experience/))](#5-experience-grading-dossier-checklists--points)
  * [6. Scores, Letter Grades & the Bell Curve ([storage/peer_grades.py](letsvibe/storage/peer_grades.py))](#6-scores-letter-grades--the-bell-curve)
  * [7. The Website: Queue, Accounts, Privacy & Admin ([web/](letsvibe/web/), [storage/](letsvibe/storage/))](#7-the-website-queue-accounts-privacy--admin)
  * [8. CLI & Configuration](#8-cli--configuration)
  * [9. Repository Structure & Tests](#9-repository-structure--tests)
  * [10. Ethics, Privacy & Licensing](#10-ethics-privacy--licensing)

---

# Part 1 — Overview

## 🎨 What Is LetsVibe?

More and more software is **vibe-coded**: someone describes what they want, and an AI writes the program — often for a person who has never written code. That is a different thing from a professional developer using AI assistance at work, and it deserves a different yardstick.

**LetsVibe** grades vibe-coded projects as what they are:

1. **🏆 A public leaderboard** of graded GitHub projects, with a podium, filters by category and platform, factor-by-factor sorting and a Hall of Fame.
2. **📋 A detailed report for every project**: five factor scores, what works, what needs work (only problems the checks actually found), and private code-health notes that only the project's owner can see.
3. **🔍 A fully disclosed method**: how grading works, every rubric change, and every number behind the grading curve are published on the site.

> **100% open source (site & grading engine):** Both the LetsVibe web platform ([Todd0042/letsvibe](https://github.com/Todd0042/letsvibe)) and the versioned evaluation engine ([Todd0042/letsvibe-engine](https://github.com/Todd0042/letsvibe-engine)) are open source under the [PolyForm Shield License 1.0.0](LICENSE), alongside the live methodology, checklist points, and curve formulas on [/how-it-works](https://letsvibe.todd0042.dev/how-it-works) and [/grading-curve](https://letsvibe.todd0042.dev/grading-curve).

---

## 🚀 How It Works in 3 Steps

1. **Sign in & submit**: Sign in with GitHub (public username, ID and avatar only — never your email) and submit a public repository **you own**. Nobody, including the site operator, can submit someone else's work.
2. **Graded on what people experience**: A program writes an App Dossier of what people see and do in the project (screens, on-screen text, setup, launch, saving, devices). An open 14B model on LetsVibe's own graphics card answers fixed checklists about it, one topic at a time, quoting the dossier for every answer, and each answer is worth fixed points. No AI reads the code and no AI picks a number. The copy is deleted afterwards. Your code never goes to an AI company.
3. **Graded on a curve**: Your project gets a raw score and a letter grade from a published bell curve, plus its rank among similar projects. Delete it — or your whole account — yourself, at any time, from the live site and every backup.

---

## 📸 Visual Tour

### 🏆 The Leaderboard
A podium for the top three, then every graded project with its score, letter grade, rank in its category, platforms, a factor balance strip and merit badges. Filter by grade, category and system, or sort by any single factor:

<p align="center">
  <img src="screenshots/leaderboard.png" alt="Leaderboard with filters, grades and factor balance" width="100%">
</p>

### 📋 A Project Report
Every report shows the letter grade from the bell curve with the exact reason for it, the target device the project was judged for, and its merit badges:

<p align="center">
  <img src="screenshots/report-header.png" alt="Report header with bell-curve grade" width="100%">
</p>

The radar is scaled to the project (0 at the centre, its best factor at the edge) with a dashed outline marking a typical vibe-coded project (62), next to the verdict, the reasoning behind the category's weights and the plain code measurements:

<p align="center">
  <img src="screenshots/report-radar.png" alt="Multi-factor radar, verdict and measurements" width="100%">
</p>

| 🧮 5-Factor Scorecard | 🛠️ What Works, What Needs Work & Next Steps |
| :---: | :---: |
| <img src="screenshots/report-scorecard.png" width="430"> | <img src="screenshots/report-findings.png" width="430"> |
| *Weights, raw scores, contributions and the reviewers' observations* | *Strengths, gaps, and what a professional team would do next* |

---

## 🧭 The 5 Factors

Graded on what people experience, never on how the code is written. "Good enough" is the standard.

| Factor | What it asks | Weight |
| :--- | :--- | :---: |
| **Real-World Value** | More than a single short prompt would make? A common idea adapted for a niche, a real problem free tools overcomplicate, or a loop that keeps people coming back? | 25% |
| **Creativity** | On-screen text with personality, an unusual way of using it, a richly themed world, features that combine into one experience. | 22% |
| **User Journey** | From install to close: how it launches, whether you land on the main thing, how much setup is needed before first use, whether your data is kept, no ads, paywalls or forced sign-in. | 20% |
| **Functionality** | Can the main task be done end to end, are the features wired together, does it say when something worked or failed? Proven problems (back-end features no screen calls, config files that can't be read) count against it, per 1,000 lines of code. | 18% |
| **UI / UX** | Clear or cluttered, one consistent look or a patchwork, feedback while people wait, and a fit for its device (phone, TV, desktop, terminal). | 15% |

**Code health** is shared with each project's owner only, as maintainer notes, and never counts toward the score. **Safety is not graded**: LetsVibe is not a security tool. **Size is never a quality signal**, and anything a scan could not find is never counted as missing.

---

## 🔔 Graded on a Published Bell Curve

AI reviewers are good at *comparing* projects but poor at absolute scores — they hedge toward the middle. So letter grades come from a single site-wide bell curve computed dynamically from raw rubric scores (never stored in the database), while **Raw Rubric** mode always preserves the direct uncurved score:

* **Default anchors**: Raw **20** maps to **0**, raw **90** maps to **100**, and the prior midpoint **75** maps to **75 (C)**.
* **Automatic scaling & adaptive spread**: If **5** or more projects score above 90 (or below 20), that endpoint shifts automatically; as more public projects are graded, the curve blends smoothly from the prior toward the real site-wide average and standard deviation.
* **Community Sparks (✨ upvotes)**: Signed-in users can Spark public projects (unlimited for others' apps, max **1** of their own). Each project receives a bounded diminishing-returns bonus of `min(3.0, 0.5 * log2(1 + sparks))` (`+0.50` for the 1st Spark, capped at `+3.00`) on the **Bell Curve** score only. **Raw Rubric** scores are never modified by Sparks, and deleting an account deletes its Sparks.

Every number and constant used by the curve is published live on the [Grading curve](https://letsvibe.todd0042.dev/grading-curve) page:

| 🔔 Why a Curve & How It Works | ✨ Community Sparks & Curved Grade Bands |
| :---: | :---: |
| <img src="screenshots/curve-method.png" width="400"> | <img src="screenshots/curve-reference.png" width="400"> |
| *Why AI scores need a curve, default anchors, and adaptive scaling* | *Community Spark upvotes and the curved letter-grade ranges* |

---

## 🔒 Privacy Promises

* **🧾 Minimal data**: GitHub's `read:user` permission only — public username, numeric ID and avatar. No email address, ever. GitHub access tokens are used once and discarded.
* **🖥️ Local AI**: Models run on LetsVibe's own hardware via [Ollama](https://ollama.com). No AI provider receives code or data.
* **📵 Cookieless aggregate analytics only**: Cloudflare Web Analytics counts aggregate pageviews without tracking cookies or personal profiles. No ads, no tracking cookies, and no cross-site trackers; GitHub avatars are proxied so visitors' browsers never contact GitHub. Essential session storage is gated behind cookie consent (browse-only mode if declined).
* **🗑️ Self-service deletion**: Export or delete one project, all projects or your whole account from **My Data**, instantly — removed from the live site and every backup at the same moment, with nobody notified.
* **🔞 Adults only**: Signing in creates an account, so the Terms of Use and Privacy Policy (18+, or the local age of majority if higher) are accepted first; the accepted version is stored with the account and deleted with it.

---

## 🛠️ Run It Yourself

> **Note:** The standalone versioned grading engine (`v1.0`–`v9.4`) is also open source at [Todd0042/letsvibe-engine](https://github.com/Todd0042/letsvibe-engine).

### Requirements
* **Python 3.11+** and **git**
* **[Ollama](https://ollama.com)** with `qwen2.5-coder:7b` and `qwen2.5-coder:14b`
* A GPU with **8 GB of VRAM** or more (developed on an RTX 5060 Ti 8 GB; the 14B model runs partly on the CPU at that size)

### Grade a repository from the command line
```bash
git clone https://github.com/Todd0042/letsvibe.git
cd letsvibe
python -m venv .venv && .venv/bin/pip install -e .
ollama pull qwen2.5-coder:7b && ollama pull qwen2.5-coder:14b

# Grade a local folder or a GitHub URL (writes a terminal report; add --html / --markdown for files)
python letsvibe.py analyze https://github.com/owner/repo
```

### Run the website locally
```bash
cp .env.example .env        # then follow the instructions inside: admin ID, SESSION_SECRET, GitHub OAuth app
python letsvibe.py web      # http://127.0.0.1:8000 (leaderboard) and /admin (admin console)
```

---
---

# Part 2 — Technical Reference

> *The sections below describe LetsVibe `v1.0.0` (grading rubric `v7`) as implemented, updated for grading rubric `v9` (hybrid grading). Earlier rubric versions are listed on the site's [rubric changelog](https://letsvibe.todd0042.dev/changelog).*

---

## 1. Architecture & the Grading Pipeline

LetsVibe is a Python package ([letsvibe/](letsvibe/)) with a **FastAPI** web server, a single background **queue worker**, **SQLite** storage (WAL mode, `secure_delete`), and a framework-free HTML/JavaScript front end. One grading recipe ([evaluator/pipeline.py](letsvibe/evaluator/pipeline.py)) is shared by the website worker, the command line and the curve calibration script, so every grade on the site — the reference projects included — is produced the same way.

```mermaid
flowchart TB
    Submit["Owner submits a public repo<br/>(web/server.py)"] --> Queue["First-come, first-served queue<br/>(web/worker.py)"]
    Queue --> Vibe["Vibe-coded check<br/>(core/vibe_detector.py)"]
    Vibe --> Ingest["Temporary clone & packing<br/>(core/ingest.py)"]
    Ingest --> Measure["Plain measurements + user-journey evidence<br/>(analyzers/)"]
    Measure --> Classify["Category, platforms, device<br/>(evaluator/classifier.py)"]
    Classify --> Dossier["App Dossier + proven-problem scan, no AI<br/>(evaluator/experience/dossier.py, analyzers/code_scan.py)"]
    Dossier --> Cliche["Closest common app type<br/>(evaluator/experience/cliche.py)"]
    Cliche --> Code["7B reads the code in 5,200-token chunks,<br/>one factor per session, every quote checked<br/>(evaluator/experience/code_evidence.py)"]
    Code --> Rubric["14B checklists on the dossier, one topic at a time,<br/>every answer quoted (evaluator/experience/rubric.py)"]
    Rubric --> Compare["Item-by-item comparison; 14B rereads<br/>the quoted lines when they disagree<br/>(evaluator/experience/reconcile.py)"]
    Compare --> Score["Fixed points per answer, fixed weights<br/>(evaluator/experience/scoring.py)"]
    Score --> Describe["14B rewrites the checked answers; public, unscored<br/>complexity description (describe.py, complexity.py)"]
    Describe --> Curve["Letter grade on the bell curve<br/>(storage/peer_grades.py)"]
    Curve --> Board["Leaderboard & report<br/>(storage/db.py, web/templates/)"]
    Calib["scripts/calibrate_curve.py<br/>reference projects x3 runs"] --> Curve
```

### GPU discipline ([evaluator/ollama.py](letsvibe/evaluator/ollama.py))
* **One grading at a time**: the worker processes the queue sequentially; the admin "Run next" button moves a task to the front instead of starting a second grading in parallel.
* **Fixed context window per model** (7B: 12,288 tokens; 14B: 6,144): changing `num_ctx` between calls forces Ollama to reload the model, so it never changes. Prompts that don't fit are trimmed from the end by LetsVibe itself, so the system prompt is never silently dropped.
* **Structured output**: every model call is constrained to a JSON schema; answers missing required scores are rejected and the evaluation fails closed rather than publishing a partial grade.

---

## 2. Ingestion & the Name-Based File Filter

* **Git-aware packing ([core/ingest.py](letsvibe/core/ingest.py))**: clones into a temporary folder (deleted afterwards), reads tracked files, skips bundled third-party libraries, binaries and lock files, and caps repositories at about 50 MB / 2,000 files.
* **Media by name only**: images, audio, video, fonts and 3D models are listed in the file tree by name so reviewers know they exist; their contents are never read.
* **Name-based file filter ([`select_for_review`](letsvibe/evaluator/triage.py))**: before any model sees the code, files are sorted by name and folder — no AI involved:
  * docs, licences, build/config files and tests are **not sent to the models** (they reach them as measurements, the README and the file tree);
  * story, dialogue, level and other content files are **sampled** (the largest few, first part only), so content-heavy projects are judged on what they contain, not their length;
  * everything else — program logic, screens, setup, input/output — is read in full.
* **Two independent reviews** (rubric v9): the App Dossier and the proven-problem scan read the code with plain Python and pass on only what people experience and what a program can prove; separately, the 7B reads the filtered code in 5,200-token chunks, **one factor per session** (functionality, UI/UX, user journey, creativity), only on chunks whose files can inform that factor. Every 7B answer must quote lines that a program then finds in the real file, or it is dropped. The two reviews are compared item by item: agreement stands, one-sided checked evidence fills the gap, and disagreements go to the 14B with both sides' quoted lines and the code around them; it must quote the line that decides it. Answers are never averaged and no model picks a number. A grade that runs past 3 hours (`LETSVIBE_TIME_LIMIT_MIN`) stops and publishes nothing. `scripts/benchmark_v9.py` grades the reference projects (and any others) several times and compares the results with v8.

---

## 3. Plain Measurements & User-Journey Evidence

Collected without AI in [`gather_metrics`](letsvibe/evaluator/pipeline.py):

| Measurement | Source | Used for |
| :--- | :--- | :--- |
| Proven problems and code-health findings | [analyzers/code_scan.py](letsvibe/analyzers/code_scan.py) | Functionality (endpoints nothing calls, unreadable configs, unfinished features); everything else becomes the owner's maintainer notes |
| Cyclomatic complexity (average, max, worst functions) | [analyzers/complexity.py](letsvibe/analyzers/complexity.py) (Lizard) | Not graded; a public plain-English complexity description on every report ([evaluator/experience/complexity.py](letsvibe/evaluator/experience/complexity.py)) |
| Packaging, licence, README | [analyzers/functionality.py](letsvibe/analyzers/functionality.py) | Neutral project facts shown on the report |
| Platforms, hardware & device profile | [core/platforms.py](letsvibe/core/platforms.py) | Device-relative grading |
| User-journey evidence | [analyzers/journey_signals.py](letsvibe/analyzers/journey_signals.py) | Installers & launch scripts, README quick start, setup wizards (including in-page wizards), permission requests, screens/commands, settings depth, confirmations, error and empty states |

---

## 4. Classification & Category Weights

* **[evaluator/classifier.py](letsvibe/evaluator/classifier.py)** reads the README, file layout and build files to pick a general category (game, application, web app, tool, embedded, …) and target platforms.
* **Fixed weights** ([evaluator/experience/scoring.py](letsvibe/evaluator/experience/scoring.py)): the same for every project (value 25, creativity 22, journey 20, functionality 18, UI/UX 15). The category is used for rankings, filters and the bell curve.

---

## 5. Experience Grading: Dossier, Checklists & Points

* **App Dossier ([experience/dossier.py](letsvibe/evaluator/experience/dossier.py))**: plain Python, about 3,000 tokens: what the project says it is (README and window titles, quoted as the owner's words), libraries, data and endpoints, screens, shared multi-device use (views other devices open at once, live sync, QR joining), the program's own features, launch and packaging, setup burden (input fields, technical wording, setup forced before first use), saving, screen and feedback facts, proven problems, hostile patterns and the on-screen text.
* **Common app types ([experience/cliche.py](letsvibe/evaluator/experience/cliche.py))**: `mxbai-embed-large` compares the project with [data/archetypes.json](letsvibe/data/archetypes.json); the closest match is given to the checklist as a plain fact.
* **Checklists ([experience/rubric.py](letsvibe/evaluator/experience/rubric.py))**: `qwen2.5-coder:14b` answers five separate checklists (value, creativity, journey, functionality, UI/UX) with fixed choices and 1-3 exact quotes from the dossier; a program checks every quote. When the dossier has no evidence either way, the answer is "not shown", which earns middle points.
* **Points ([experience/scoring.py](letsvibe/evaluator/experience/scoring.py))**: every answer is worth fixed points (published on the How it works page); a factor scores the share of its points earned. The model never picks a number.
* **Descriptions ([experience/describe.py](letsvibe/evaluator/experience/describe.py))**: the 14B rewrites its own checked answers in plain English; sentences that contradict a negative answer are removed by a program, and "needs work" lists only genuine problems.
* **Maintainer notes**: code-health findings are saved in the project's own report folder and shown only to its owner or the admin.

---

## 6. Scores, Letter Grades & the Bell Curve

* **Score**: the five factor scores are combined with fixed weights ([evaluator/experience/scoring.py](letsvibe/evaluator/experience/scoring.py)).
* **Bell curve ([storage/peer_grades.py](letsvibe/storage/peer_grades.py))**: `z = (score − average) / standard deviation`. The reference projects set the starting bell (average = their midpoint, one standard deviation = a quarter of the gap); each category's own scores take over as it grows, fully at 30 projects.

| Grade | Standard deviations from the average | Share of a full bell |
| :---: | :--- | :---: |
| A+ / A / A- | +2.0 and above / +1.75 / +1.5 | ~6.7% |
| B+ / B / B- | +1.17 / +0.83 / +0.5 | ~24.2% |
| C+ / C / C- | +0.17 / −0.17 / −0.5 | ~38.3% |
| D | −1.5 to −0.5 | ~24.2% |
| F | below −1.5 | ~6.7% |

* **Calibration ([scripts/calibrate_curve.py](scripts/calibrate_curve.py))**: grades both reference projects several times through the shared pipeline and writes everything — every run, average, spread and the reference reports — to [letsvibe/data/curve_calibration.json](letsvibe/data/curve_calibration.json). The public Grading curve page is generated from that file, so the disclosure can't drift from what the grader uses. It is re-run whenever the models, rubric or pipeline change.

---

## 7. The Website: Queue, Accounts, Privacy & Admin

* **Accounts ([core/auth.py](letsvibe/core/auth.py), [core/legal.py](letsvibe/core/legal.py))**: GitHub OAuth (`read:user`), signed HttpOnly session cookies, and a signed anti-forgery state that also carries the accepted Terms version. Sessions only count while the account exists and has accepted the current Terms.
* **Ownership**: only a repository's owner can submit, re-grade, appeal or delete it — the admin included.
* **Queue ([web/worker.py](letsvibe/web/worker.py))**: first come, first served, shown in run order with each task's place in line.
* **Owner input & corrections ([storage/integrity.py](letsvibe/storage/integrity.py))**: when the review needs context about specific lines, the report is held privately until the owner explains them (deleted after 7 days without an answer). Owners can report factual errors in a review; each is checked against the code and corrected or reviewed by hand.
* **Privacy & deletion ([storage/backups.py](letsvibe/storage/backups.py), [web/privacy_jobs.py](letsvibe/web/privacy_jobs.py))**: weekly backups, only the two newest kept; every deletion is applied to the live database and every backup together, and a backup that cannot be cleaned is destroyed.
* **Admin console**: queue control, held reports, error reports, appeals, a developer tree with per-project deletion and a **"Delete all user data"** action for removal requests that arrive by email.

<p align="center">
  <img src="screenshots/admin-console.png" alt="Admin queue and engine console" width="90%">
</p>
* **Content pages ([web/templates/pages/](letsvibe/web/templates/pages/))**: How grading works, Grading curve, FAQ, rubric changelog, Privacy, Cookies, Terms, Remove my project, My Data and My Projects.

---

## 8. CLI & Configuration

### Command line ([letsvibe/cli.py](letsvibe/cli.py))
```bash
python letsvibe.py analyze <path-or-github-url> [OPTIONS]   # grade one repository
python letsvibe.py web [--host 127.0.0.1] [--port 8000]     # leaderboard + admin console
python letsvibe.py models                                   # list local Ollama models
```
| `analyze` option | Description |
| :--- | :--- |
| `--mode`, `--micro-model`, `--macro-model`, `--context-tokens`, `--distill` | Accepted for compatibility; ignored since rubric v8 (one recipe for everything) |
| `--html PATH`, `--markdown PATH`, `--json PATH` | Export the report |

### Environment variables ([.env.example](.env.example))
| Variable | Purpose |
| :--- | :--- |
| `SESSION_SECRET` | 32+ random characters used to sign sessions (without one, a random per-process secret is used and every restart signs everyone out) |
| `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `GITHUB_REDIRECT_URI` | Your GitHub OAuth app |
| `ADMIN_GITHUB_ID` (or `ADMIN_GITHUB_USER`) | The admin account, matched by numeric GitHub ID. There is no built-in admin: leave both empty and nobody is admin |
| `ADMIN_EMAIL` | Where appeal notifications go (optional; needs SMTP) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | Optional outgoing email for appeal notices |
| `MAX_REGISTERED_USERS` | Sign-up cap for non-admin accounts (default 5; `0` = private preview) |
| `SUBMISSIONS_PER_WINDOW`, `WINDOW_HOURS` | Fair-use limit per account (default 2 per 168 hours) |
| `LETSVIBE_DB_PATH` | Database location (default `data/letsvibe.db`) |
| `LETSVIBE_BACKUP_DIR` | Rolling weekly database backup directory (default `<db_dir>/backups`) |
| `LETSVIBE_MAX_CTX`, `LETSVIBE_MAX_CTX_14B` | Context windows for the models (defaults 12,288 / 6,144) |
| `HOST`, `PORT` | Web server address |

---

## 9. Repository Structure & Tests

```
letsvibe/
├── letsvibe.py                  # Entry point (auto-uses .venv)
├── scripts/calibrate_curve.py   # Builds the grading curve from the reference projects
├── letsvibe/
│   ├── cli.py                   # analyze / web / models
│   ├── analyzers/               # Proven-problem scan, complexity, packaging, user-journey evidence
│   ├── core/                    # Ingestion, auth, terms, limits, platforms, vibe-coded check, data models
│   ├── evaluator/               # Shared pipeline, experience grading (dossier, checklists, points), classifier, Ollama client
│   ├── storage/                 # SQLite, bell curve, backups & deletion everywhere, held reports & corrections
│   ├── reporters/               # Terminal, Markdown and HTML reports
│   ├── data/curve_calibration.json  # Published curve calibration
│   └── web/                     # FastAPI server, queue worker, deletion jobs, image proxy, templates & static files
└── tests/                       # Unit, API and storage tests; tests/ui = browser tests (Playwright)
```

### Contributing & security
Bug reports and ideas are welcome as issues; code pull requests are not being accepted for now. See [CONTRIBUTING.md](CONTRIBUTING.md). Please report security problems privately as described in [SECURITY.md](SECURITY.md).

### Running the tests
```bash
.venv/bin/pip install -e .[dev]
.venv/bin/python -m pytest -q --ignore=tests/ui     # unit, API, storage and privacy tests
.venv/bin/python -m playwright install chromium
.venv/bin/python -m pytest -q tests/ui              # browser tests at phone, tablet and desktop sizes
```
Both suites run automatically on every push ([.github/workflows/tests.yml](.github/workflows/tests.yml)).
A permanent guard ([tests/test_no_secrets.py](tests/test_no_secrets.py)) fails the test run if any tracked file is a secret-type file or contains a secret-shaped value.

---

## 10. Ethics, Privacy & Licensing

LetsVibe is built around three commitments: **honest grading, user privacy, and full disclosure.**

### 🎯 1. Honest grading
* Vibe-coded software is compared with vibe-coded software only. Accuracy matters more than flattering scores; grades are automated opinions, not security audits or endorsements.
* Every rubric change is published, and the grading curve's reference projects, prompts and runs are public.

### 🔒 2. Privacy by design
* Cookieless aggregate pageviews only (Cloudflare Web Analytics); no tracking cookies, ads, personal profiles, or cross-site trackers. Code is graded locally and the copy deleted; users delete their own data (including Spark upvotes) from the live site and every backup, with nobody notified.

### 🤖 3. How LetsVibe itself was made
LetsVibe is vibe-coded too: by this site's own definition, it is exactly the kind of project it grades. Its creator directed every decision and wrote none of the code.

| Contributor | Role |
| :--- | :--- |
| **[Todd0042](https://github.com/Todd0042)** | Creator: the idea, product direction, the privacy rules, the grading philosophy ("vibe-coded software graded as vibe-coded software", the bell curve), testing against real projects, and every review and decision |
| **Antigravity** (Google, Gemini models) | Wrote the original version, 2026-11-08 to 2026-11-11: the dual-model local analyzer and CLI, git-aware ingestion and chunking, archetype detection, the first web leaderboard with GitHub sign-in, quotas and 7-factor scoring, fail-closed scoring and the yes/no evidence checklist |
| **Claude Code** (Anthropic, Claude Opus 5.5) | Took over from 2026-11-11 to v1.0.0: the fix for silently truncated prompts, rubrics v6 and v7 (the vibe-coded reference class, user journey, the name-based file filter, the bell curve and its calibration), privacy and self-service deletion everywhere, terms acceptance, the admin tools, the model bake-off, the README, and the two reference projects ([letsvibe-good](https://github.com/Todd0042/letsvibe-good), [letsvibe-bad](https://github.com/Todd0042/letsvibe-bad)), as documented in their `PROVENANCE.md` files |

This public repository starts at v1.0.0 as a single commit. The earlier development history, including the trial and error behind rubrics v1 to v7, is summarized on the site's [rubric changelog](https://letsvibe.todd0042.dev/changelog).

### 🔍 LetsVibe: Reality Check (`LetsVibeRC.todd0042.dev`)
* **Pre-Build Idea Filter:** Evaluates an app idea pitch, target platform, and supposed unique twist *before* writing code, querying GitHub Search & npm for real competitors and returning a candid 6-dimension viability & market saturation report (`DO NOT BUILD` / `NICHE ONLY` / `ACTUALLY VIABLE`) with shareable links (`/r/<id>`) and standalone HTML export.
* **Shared Round-Robin GPU Turns:** the host `gpu-scheduler` service (`127.0.0.1:11436`, repo `gpu-scheduler`) gives exclusive turns (`/lock/acquire`, `/lock/heartbeat`, `/lock/release`, `/lock/status`) round-robin between `letsvibe` audits, `letsvibe_rc` reality checks, Noir Studios and Graphical Studio. It keeps `llama-server` loaded whenever the GPU is idle, so LetsVibe starts instantly.

### ⚖️ 4. License
* **[PolyForm Shield License 1.0.0](LICENSE)**: you may read, use, modify and share LetsVibe — for personal, educational, nonprofit or commercial purposes — as long as you don't use it to provide a product or service that competes with LetsVibe (for example, running a rival leaderboard for vibe-coded software). See the [LICENSE](LICENSE) for the exact terms.
* **The LetsVibe name and logo are not licensed.** The license covers the code only; please don't use the name or logo for your own projects or services.
* Questions: use the in-app **Chat / Support** widget or open a GitHub issue.
* Third-party components (FastAPI, Uvicorn, Pydantic, Typer, Rich, Lizard, Jinja2, httpx, python-dotenv; Ollama and the Qwen2.5-Coder models at runtime) are used under their own licenses.

---

<p align="center">
  If LetsVibe has been useful or fun, consider supporting its development:<br/><br/>
  <a href="https://ko-fi.com/todd0042" target="_blank">
    <img src="https://img.shields.io/badge/☕_Support_LetsVibe_on_Ko--fi-F16061?style=for-the-badge&logo=ko-fi&logoColor=white" alt="Support on Ko-fi">
  </a>
</p>

---

<p align="center">
  <a href="https://ko-fi.com/todd0042">
    <img src="https://raw.githubusercontent.com/Todd0042/creator-assets/main/banners/todd_creator_banner_v2.0.png" alt="Support Todd on Ko-fi" width="100%" />
  </a>
</p>
