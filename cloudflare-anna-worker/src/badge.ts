const FONT = "Verdana,Geneva,'DejaVu Sans',sans-serif";

const GRADE_COLOURS: Record<string, [string, string]> = {
  A: ["#10b981", "#047857"],
  B: ["#0ea5e9", "#0369a1"],
  C: ["#f59e0b", "#b45309"],
  D: ["#f97316", "#c2410c"],
  F: ["#ef4444", "#b91c1c"],
};

const esc = (s: string) =>
  String(s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");

export function renderBadgeSvg(
  style: string,
  proj: Record<string, unknown> | null,
  scale: "curve" | "raw",
): string {
  if (!proj) {
    return `<svg xmlns="http://www.w3.org/2000/svg" width="160" height="20"><rect width="160" height="20" rx="3" fill="#475569"/><text x="80" y="14" fill="#fff" font-family="${FONT}" font-size="11" text-anchor="middle">LetsVibe | not graded</text></svg>`;
  }
  const useRaw = scale === "raw";
  const grade = String((useRaw ? proj.letter_grade : proj.curved_letter_grade) || proj.letter_grade || "C");
  const score = Number((useRaw ? proj.final_score : proj.curved_score) ?? proj.final_score ?? 0).toFixed(1);
  const slug = String(proj.full_slug || proj.repo_name || "Project");
  const [mainCol, darkCol] = GRADE_COLOURS[grade[0]] || ["#64748b", "#475569"];
  const label = `LetsVibe ${useRaw ? "Raw" : "Curve"}`;
  const val = `${grade} · ${score}`;

  if (style === "card") {
    let factors: Record<string, unknown> = {};
    try {
      if (typeof proj.factor_scores_json === "string" && proj.factor_scores_json) {
        factors = JSON.parse(proj.factor_scores_json) as Record<string, unknown>;
      }
    } catch {
      factors = {};
    }
    const factorDefs: Array<[string, string]> = [
      ["resonance", "Value"],
      ["creativity", "Creativity"],
      ["user_journey", "Journey"],
      ["functionality", "Function"],
      ["ui_ux", "UI/UX"],
    ];
    const bars = factorDefs
      .map(([key, flabel], i) => {
        const entry = factors[key];
        const rawVal = Number(
          entry && typeof entry === "object" && "score" in entry
            ? (entry as Record<string, unknown>).score
            : (entry ?? 0)
        );
        const v = Number.isFinite(rawVal) ? Math.max(0, Math.min(100, rawVal)) : 0;
        const y = 48 + i * 13;
        const barW = Math.max(0, Math.min(140, 1.4 * v));
        return `<text x="18" y="${(y + 6.5).toFixed(1)}" font-family="${FONT}" font-size="8.5" fill="#94a3b8">${esc(flabel)}</text>
  <rect x="76" y="${y.toFixed(1)}" width="140" height="6" rx="3" fill="#ffffff" fill-opacity=".08"/>
  <rect x="76" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="6" rx="3" fill="url(#bar)"/>
  <text x="224" y="${(y + 6.5).toFixed(1)}" font-family="${FONT}" font-size="8.5" fill="#cbd5e1">${v.toFixed(0)}</text>`;
      })
      .join("\n  ");

    return `<svg xmlns="http://www.w3.org/2000/svg" width="400" height="122" viewBox="0 0 400 122" role="img" aria-label="${esc(slug)} on LetsVibe: ${esc(grade)} (${esc(score)})">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#1f2a47"/><stop offset="1" stop-color="#0b1020"/></linearGradient>
    <linearGradient id="bar" x2="1"><stop offset="0" stop-color="#22d3ee"/><stop offset="1" stop-color="${mainCol}"/></linearGradient>
  </defs>
  <rect x=".5" y=".5" width="399" height="121" rx="12" fill="url(#bg)" stroke="${mainCol}" stroke-opacity=".55"/>
  <text x="18" y="23" fill="#f8fafc" font-family="${FONT}" font-size="13" font-weight="bold">${esc(slug.slice(0, 34))}</text>
  <text x="18" y="36" fill="#94a3b8" font-family="${FONT}" font-size="9">${esc(label)} · ${esc(String(proj.rubric_version || "v11a"))}</text>
  ${bars}
  <rect x="298" y="18" width="84" height="86" rx="10" fill="${darkCol}"/>
  <text x="340" y="58" fill="#fff" font-family="${FONT}" font-size="26" font-weight="bold" text-anchor="middle">${esc(grade)}</text>
  <text x="340" y="80" fill="#f8fafc" font-family="${FONT}" font-size="12" text-anchor="middle">${esc(score)}</text>
</svg>`;
  }

  if (style === "medal") {
    return `<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96" viewBox="0 0 96 96" role="img" aria-label="LetsVibe ${esc(grade)} (${esc(score)})">
  <circle cx="48" cy="48" r="44" fill="#141a2e" stroke="${mainCol}" stroke-width="4"/>
  <circle cx="48" cy="48" r="36" fill="${darkCol}"/>
  <text x="48" y="47" fill="#fff" font-family="${FONT}" font-size="24" font-weight="bold" text-anchor="middle">${esc(grade)}</text>
  <text x="48" y="66" fill="#e2e8f0" font-family="${FONT}" font-size="11" text-anchor="middle">${esc(score)}</text>
</svg>`;
  }

  if (style === "for-the-badge") {
    const lw = 128;
    const rw = 96;
    return `<svg xmlns="http://www.w3.org/2000/svg" width="${lw + rw}" height="28">
  <rect width="${lw}" height="28" fill="#141a2e"/>
  <rect x="${lw}" width="${rw}" height="28" fill="${darkCol}"/>
  <text x="${lw / 2}" y="18" fill="#fff" font-family="${FONT}" font-size="11" font-weight="bold" text-anchor="middle">${esc(label.toUpperCase())}</text>
  <text x="${lw + rw / 2}" y="18" fill="#fff" font-family="${FONT}" font-size="11" font-weight="bold" text-anchor="middle">${esc(val.toUpperCase())}</text>
</svg>`;
  }

  const lw = 104;
  const rw = 82;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${lw + rw}" height="20">
  <rect width="${lw}" height="20" fill="#141a2e"/>
  <rect x="${lw}" width="${rw}" height="20" fill="${darkCol}"/>
  <text x="${lw / 2}" y="14" fill="#fff" font-family="${FONT}" font-size="11" text-anchor="middle">${esc(label)}</text>
  <text x="${lw + rw / 2}" y="14" fill="#fff" font-family="${FONT}" font-size="11" font-weight="bold" text-anchor="middle">${esc(val)}</text>
</svg>`;
}
