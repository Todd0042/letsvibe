# Working on LetsVibe

## Two sites, one front end

There are two copies of LetsVibe, and their front ends are kept in sync:

- `letsvibe` (~/Documents/GitHub/letsvibe): the current site, letsvibe.todd0042.dev, grading with rubric v9
  (hybrid experience grading: value, creativity, user journey, functionality, UI/UX; the App Dossier plus per-factor
  7B code evidence, compared item by item).
- `letsvibe-og-bk` (~/Documents/GitHub/letsvibe-og-bk): the original grader (rubric v7: 7B + 14B code reading,
  seven factors) kept online at letsvibe-og-bk.todd0042.dev to compare the two grading engines.

**Rule: every front-end change is applied to both repos in the same piece of work.** Front end means anything a
visitor or the admin sees or uses: `letsvibe/web/templates/**` (dashboard.html, pages/, partials/),
`letsvibe/web/static/**`, and the server endpoints that only exist to serve the page (images, badges, admin buttons).
Make the change in one repo, port it to the other, run both test suites (`python -m pytest -q --ignore=tests/ui` and
`python -m pytest -q tests/ui`), and check the page in both.

What stays different on purpose (do not "sync" these): the grading engine and pipeline, the factors each site grades
and shows (labels, weights, radar axes, mini bars, factor tables), rubric version text, the how-it-works / grading
pages that describe each engine, and each site's data, `.env`, port (8000 / 8001) and calibration file.

## Other standing rules

- Commit and push only when asked.
- Never change grading or analysis code unasked; discuss it first.
- Privacy first: no emails, owners submit only their own repos (admin test mode is the temporary, admin-only exception
  marked `TEST MODE (remove before launch)`), deletions reach live data and backups.
- og-bk's two tests that expect the folder to be named `letsvibe` fail there by design (folder name only).
