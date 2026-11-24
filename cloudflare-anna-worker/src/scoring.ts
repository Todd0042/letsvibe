// Scores and letters exactly as on letsvibe.todd0042.dev (letsvibe/evaluator/rubric.py, storage/db.py):
// a project's score is its graded score plus the community Spark bonus, capped at 100. No curve (removed 2026-11-21).

const GRADE_CUTOFFS: [number, string][] = [
  [97.0, "A+"], [93.0, "A"], [90.0, "A-"],
  [87.0, "B+"], [83.0, "B"], [80.0, "B-"],
  [77.0, "C+"], [73.0, "C"], [70.0, "C-"],
  [67.0, "D+"], [63.0, "D"], [60.0, "D-"],
];

/** Uncapped diminishing-returns community Spark bonus: 0.5 * log2(1 + n). */
export function upvoteScoreBonus(upvoteCount: number): number {
  const n = Math.max(0, Math.floor(Number(upvoteCount || 0)));
  if (n <= 0) return 0.0;
  return Math.round(0.5 * Math.log2(1 + n) * 100) / 100;
}

export function calculateLetterGrade(score: number): string {
  for (const [cutoff, grade] of GRADE_CUTOFFS) {
    if (score >= cutoff) return grade;
  }
  return "F";
}

/** The score shown and ranked: graded score + Spark bonus, at most 100. */
export function displayedScore(score: number, upvoteCount: number): number {
  return Math.min(100.0, Math.round((Number(score || 0) + upvoteScoreBonus(upvoteCount)) * 10) / 10);
}

/**
 * Which report is a project's current one: the newest engine wins. The old Anna-only engines (v10a, v10.3a, v11a,
 * v12a, v13a) rank below every live-site engine version (v10.4 and later), whatever their number.
 */
export function rubricVersionRank(version: string): number {
  const s = String(version || "").trim().toLowerCase();
  if (!s) return 0;
  const m = /^v?(\d+)(?:\.(\d+))?(a)?$/.exec(s);
  if (!m) return 0;
  const n = parseInt(m[1], 10) * 100 + parseInt(m[2] || "0", 10);
  return m[3] ? n - 1000 : n; // legacy Anna "…a": v13a -> 300, v10.4 -> 1004
}
