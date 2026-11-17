// Exact TypeScript port of global_curve_details + calculate_letter_grade from letsvibe.

const DEFAULT_CURVE_LOW = 20.0;
const DEFAULT_CURVE_HIGH = 90.0;
const OUTLIER_THRESHOLD = 5;
const GLOBAL_PRIOR_MEAN = 75.0;
const GLOBAL_PRIOR_SD = 8.5;
const PEER_MIN_PROJECTS = 30;

const CURVED_SCALE: [number, number][] = [
  [-4.0, 0.0], [-1.5, 60.0], [-0.5, 70.0], [-0.17, 73.0],
  [0.17, 77.0], [0.5, 80.0], [0.83, 83.0], [1.17, 87.0],
  [1.5, 90.0], [1.75, 93.0], [2.0, 97.0], [3.0, 100.0],
];

const GRADE_CUTOFFS: [number, string][] = [
  [94.0, "A+"], [89.0, "A"], [85.0, "A-"],
  [81.0, "B+"], [77.0, "B"], [72.0, "B-"],
  [68.0, "C+"], [62.0, "C"], [55.0, "C-"],
  [40.0, "D"],
];

export function calculateLetterGrade(score: number): string {
  for (const [cutoff, grade] of GRADE_CUTOFFS) {
    if (score >= cutoff) return grade;
  }
  return "F";
}

function curvedScaleRaw(z: number): number {
  if (z <= CURVED_SCALE[0][0]) return CURVED_SCALE[0][1];
  if (z >= CURVED_SCALE[CURVED_SCALE.length - 1][0]) return CURVED_SCALE[CURVED_SCALE.length - 1][1];
  for (let i = 0; i < CURVED_SCALE.length - 1; i++) {
    const [x0, y0] = CURVED_SCALE[i];
    const [x1, y1] = CURVED_SCALE[i + 1];
    if (x0 <= z && z <= x1) return y0 + ((y1 - y0) * (z - x0)) / (x1 - x0);
  }
  return 75.0;
}

function globalScaleEnds(allScores: number[]): [number, number] {
  let low = DEFAULT_CURVE_LOW;
  let high = DEFAULT_CURVE_HIGH;
  if (!allScores.length) return [low, high];
  const below = allScores.filter((s) => s < DEFAULT_CURVE_LOW).sort((a, b) => a - b);
  if (below.length >= OUTLIER_THRESHOLD) low = below[OUTLIER_THRESHOLD - 1];
  const above = allScores.filter((s) => s > DEFAULT_CURVE_HIGH).sort((a, b) => b - a);
  if (above.length >= OUTLIER_THRESHOLD) high = above[OUTLIER_THRESHOLD - 1];
  return [low, Math.max(low + 1.0, high)];
}

export function computeCurvedScore(score: number, allScores: number[]): number {
  const [low, high] = globalScaleEnds(allScores);
  const n = allScores.length;
  const w = n >= 2 ? Math.min(1.0, n / PEER_MIN_PROJECTS) : 0.0;
  let mean = GLOBAL_PRIOR_MEAN;
  let sd = GLOBAL_PRIOR_SD;
  if (w > 0 && n >= 2) {
    const sampleMean = allScores.reduce((a, b) => a + b, 0) / n;
    const variance = allScores.reduce((acc, s) => acc + (s - sampleMean) ** 2, 0) / (n - 1);
    const sampleSd = Math.max(Math.sqrt(variance), 1.0);
    mean = (1 - w) * mean + w * sampleMean;
    sd = (1 - w) * sd + w * sampleSd;
  }
  mean = Math.min(high - 1.0, Math.max(low + 1.0, mean));
  const z = (score - mean) / sd;
  const zHigh = (high - mean) / sd;

  let curved: number;
  if (score >= high) {
    curved = 100.0;
  } else if (z > 0.0) {
    const rawY = curvedScaleRaw(z);
    const yMid = curvedScaleRaw(0.0);
    const yHigh = curvedScaleRaw(zHigh);
    const denom = Math.max(1e-6, yHigh - yMid);
    const bellUp = mean + (100.0 - mean) * ((rawY - yMid) / denom);
    curved = Math.max(score, Math.min(100.0, bellUp));
  } else {
    curved = Math.max(0.0, Math.min(100.0, score));
  }
  return Math.round(curved * 10) / 10;
}

export function rubricVersionRank(version: string): number {
  const s = String(version || "").trim().toLowerCase();
  if (!s) return 0;
  if (s === "v10a") return 1000;
  const m = /^v?(\d+)(?:\.(\d+))?/.exec(s);
  if (!m) return 0;
  return parseInt(m[1], 10) * 100 + parseInt(m[2] || "0", 10);
}
