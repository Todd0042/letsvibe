import { extractUser, isAdminUser, popupAuthHtml, SessionUser, signToken } from "./auth";
import { renderBadgeSvg } from "./badge";
import { calculateLetterGrade, computeCurvedScore, rubricVersionRank } from "./curve";

export interface Env {
  DB: D1Database;
  GITHUB_CLIENT_ID: string;
  GITHUB_CLIENT_SECRET: string;
  SESSION_SECRET: string;
  GITHUB_VERIFY_TOKEN?: string;
}

const RETENTION_DAYS = 15;

const ALLOWED_ORIGINS = new Set([
  "https://anna.partners",
  "https://apps.anna.partners",
  "https://letsvibe.todd0042.dev",
  "http://localhost:5180",
  "http://127.0.0.1:5180",
]);

function corsHeaders(req: Request): Record<string, string> {
  const origin = req.headers.get("origin") || "";
  const isAllowed =
    ALLOWED_ORIGINS.has(origin) ||
    /^https:\/\/([a-z0-9-]+\.)?anna\.partners$/i.test(origin) ||
    /^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/i.test(origin);
  const allow = isAllowed ? origin : "*";
  return {
    "Access-Control-Allow-Origin": allow,
    ...(isAllowed ? { "Access-Control-Allow-Credentials": "true" } : {}),
    "Vary": "Origin",
    "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
  };
}

const jsonRes = (req: Request, body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...corsHeaders(req) },
  });

function canViewDeleted(proj: Record<string, unknown>, user: SessionUser | null): boolean {
  if (isAdminUser(user)) return true;
  if (!user?.username) return false;
  const low = user.username.toLowerCase();
  return [proj.owner, proj.submitted_by, proj.deleted_by].some(
    (v) => String(v || "").toLowerCase() === low,
  );
}

async function getActiveBan(db: D1Database, username: string): Promise<Record<string, unknown> | null> {
  if (!username) return null;
  const row = await db
    .prepare("SELECT * FROM bans WHERE lower(github_username) = lower(?)")
    .bind(username.trim())
    .first<Record<string, unknown>>();
  if (!row) return null;
  if (row.expires_at && String(row.expires_at) <= new Date().toISOString()) {
    await db.prepare("DELETE FROM bans WHERE id = ?").bind(row.id).run();
    return null;
  }
  return row;
}

async function recomputeCurve(db: D1Database): Promise<void> {
  const { results: latestRows } = await db
    .prepare(
      "SELECT r.id, r.project_id, r.final_score FROM reports r JOIN projects p ON p.id = r.project_id WHERE r.is_latest = 1 AND p.deleted_at IS NULL AND COALESCE(p.is_test, 0) = 0",
    )
    .all<{ id: number; project_id: number; final_score: number }>();
  const cohort = latestRows.map((r) => Number(r.final_score));
  if (!cohort.length) return;

  const { results: allReports } = await db
    .prepare(
      "SELECT r.id, r.project_id, r.final_score, r.is_latest, p.deleted_at, COALESCE(p.is_test, 0) AS is_test FROM reports r JOIN projects p ON p.id = r.project_id",
    )
    .all<{ id: number; project_id: number; final_score: number; is_latest: number; deleted_at: string | null; is_test: number }>();

  const stmts: D1PreparedStatement[] = [];
  for (const r of allReports) {
    const raw = Number(r.final_score);
    const activeLatest = r.is_latest === 1 && !r.deleted_at && r.is_test === 0;
    const scores = activeLatest ? cohort : [...cohort, raw];
    const curved = computeCurvedScore(raw, scores);
    const cGrade = calculateLetterGrade(curved);
    stmts.push(
      db.prepare("UPDATE reports SET curved_score = ?, curved_letter_grade = ? WHERE id = ?").bind(curved, cGrade, r.id),
    );
    if (r.is_latest === 1) {
      stmts.push(
        db.prepare("UPDATE projects SET curved_score = ?, curved_letter_grade = ? WHERE id = ?").bind(curved, cGrade, r.project_id),
      );
    }
  }
  if (stmts.length) await db.batch(stmts);
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    if (req.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders(req) });
    }

    const url = new URL(req.url);
    const path = url.pathname;

    // 1. Health / Stats
    if (req.method === "GET" && (path === "/api/anna/health" || path === "/api/anna/stats")) {
      const tp = await env.DB.prepare("SELECT COUNT(*) AS c FROM projects WHERE latest_report_id IS NOT NULL AND deleted_at IS NULL AND COALESCE(is_test, 0) = 0").first<{ c: number }>();
      const tr = await env.DB.prepare("SELECT COUNT(*) AS c FROM reports r JOIN projects p ON p.id = r.project_id WHERE p.deleted_at IS NULL AND COALESCE(p.is_test, 0) = 0").first<{ c: number }>();
      const tc = await env.DB.prepare("SELECT COUNT(DISTINCT lower(owner)) AS c FROM projects WHERE latest_report_id IS NOT NULL AND deleted_at IS NULL AND COALESCE(is_test, 0) = 0").first<{ c: number }>();
      const avgs = await env.DB.prepare("SELECT AVG(final_score) AS ar, AVG(curved_score) AS ac FROM projects WHERE latest_report_id IS NOT NULL AND deleted_at IS NULL AND COALESCE(is_test, 0) = 0").first<{ ar: number | null; ac: number | null }>();
      return jsonRes(req, {
        status: "ok",
        backend: "cloudflare",
        total_projects: Number(tp?.c || 0),
        total_reports: Number(tr?.c || 0),
        total_creators: Number(tc?.c || 0),
        avg_raw_score: Math.round(Number(avgs?.ar || 0) * 10) / 10,
        avg_curved_score: Math.round(Number(avgs?.ac || 0) * 10) / 10,
      });
    }

    // 2. GitHub OAuth (letsvibe-anna / og-bk, zero user cap)
    if (req.method === "GET" && path === "/api/anna/auth/github/login") {
      const state = await signToken({ nonce: crypto.randomUUID(), exp: Math.floor(Date.now() / 1000) + 600 }, env.SESSION_SECRET);
      const cb = `${url.origin}/api/anna/auth/github/callback`;
      const ghUrl = `https://github.com/login/oauth/authorize?client_id=${encodeURIComponent(env.GITHUB_CLIENT_ID)}&redirect_uri=${encodeURIComponent(cb)}&scope=read:user&state=${encodeURIComponent(state)}`;
      return Response.redirect(ghUrl, 302);
    }

    if (req.method === "GET" && path === "/api/anna/auth/github/callback") {
      const code = url.searchParams.get("code") || "";
      if (!code) return new Response("Missing OAuth code", { status: 400 });
      const tokRes = await fetch("https://github.com/login/oauth/access_token", {
        method: "POST",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify({
          client_id: env.GITHUB_CLIENT_ID,
          client_secret: env.GITHUB_CLIENT_SECRET,
          code,
        }),
      });
      const tokData = (await tokRes.json()) as { access_token?: string };
      if (!tokData.access_token) return new Response("GitHub OAuth token exchange failed", { status: 400 });

      const userRes = await fetch("https://api.github.com/user", {
        headers: { Authorization: `Bearer ${tokData.access_token}`, "User-Agent": "LetsVibe-Anna-Worker" },
      });
      const ghUser = (await userRes.json()) as { id: number; login: string; avatar_url?: string };
      if (!ghUser?.login) return new Response("Failed to fetch GitHub user", { status: 400 });

      const adm = ghUser.login.toLowerCase() === "todd0042" || Number(ghUser.id) === 69619147;
      const sessionUser: SessionUser = {
        username: ghUser.login,
        github_id: ghUser.id,
        avatar_url: ghUser.avatar_url || "",
        is_admin: adm,
        exp: Math.floor(Date.now() / 1000) + 7 * 86400,
      };
      const sessionToken = await signToken(sessionUser as unknown as Record<string, unknown>, env.SESSION_SECRET);
      return new Response(popupAuthHtml(sessionToken, sessionUser), {
        headers: { "Content-Type": "text/html; charset=utf-8" },
      });
    }

    if (req.method === "GET" && path === "/api/anna/auth/me") {
      const user = await extractUser(req, env.SESSION_SECRET);
      const adm = isAdminUser(user);
      const ban = user && !adm ? await getActiveBan(env.DB, user.username) : null;
      return jsonRes(req, {
        authenticated: Boolean(user),
        user: user ? { ...user, is_admin: adm } : null,
        is_admin: adm,
        ban,
      });
    }

    // 2b. User's own projects (active + 15-day pending deletion)
    if (req.method === "GET" && path === "/api/anna/me/projects") {
      const user = await extractUser(req, env.SESSION_SECRET);
      if (!user?.username) return jsonRes(req, { detail: "Sign in with GitHub first." }, 401);
      const low = user.username.toLowerCase();
      const { results } = await env.DB.prepare(
        "SELECT * FROM projects WHERE lower(owner) = ? OR lower(COALESCE(submitted_by, '')) = ? OR lower(COALESCE(deleted_by, '')) = ? ORDER BY updated_at DESC, id DESC",
      )
        .bind(low, low, low)
        .all<Record<string, unknown>>();
      return jsonRes(req, { status: "ok", projects: results });
    }

    if (req.method === "POST" && path === "/api/anna/me/delete-all") {
      const user = await extractUser(req, env.SESSION_SECRET);
      if (!user?.username) return jsonRes(req, { detail: "Sign in with GitHub first." }, 401);
      const now = new Date();
      const deletedAt = now.toISOString();
      const purgeAfter = new Date(now.getTime() + RETENTION_DAYS * 86400 * 1000).toISOString();
      const low = user.username.toLowerCase();
      const res = await env.DB.prepare(
        "UPDATE projects SET deleted_at = COALESCE(deleted_at, ?), purge_after = COALESCE(purge_after, ?), deleted_by = COALESCE(deleted_by, ?), updated_at = ? WHERE (lower(owner) = ? OR lower(COALESCE(submitted_by, '')) = ?) AND deleted_at IS NULL",
      )
        .bind(deletedAt, purgeAfter, user.username, deletedAt, low, low)
        .run();
      const cnt = Number(res.meta.changes || 0);
      if (cnt > 0) await recomputeCurve(env.DB);
      return jsonRes(req, {
        status: "ok",
        hidden_count: cnt,
        purge_after: purgeAfter,
        retention_days: RETENTION_DAYS,
        message: `Hidden ${cnt} project(s) from public view; permanent purge scheduled on ${purgeAfter.slice(0, 10)} (15-day legal hold).`,
      });
    }

    // 3. Leaderboard
    if (req.method === "GET" && path === "/api/anna/leaderboard") {
      const scale = url.searchParams.get("scale") === "raw" ? "raw" : "curve";
      const limit = Math.min(500, Math.max(1, Number(url.searchParams.get("limit") || 100)));
      const offset = Math.max(0, Number(url.searchParams.get("offset") || 0));
      const orderCol = scale === "raw" ? "final_score" : "curved_score";
      const user = await extractUser(req, env.SESSION_SECRET);

      const { results: rows } = await env.DB.prepare(
        `SELECT * FROM projects WHERE latest_report_id IS NOT NULL AND deleted_at IS NULL AND COALESCE(is_test, 0) = 0 ORDER BY ${orderCol} DESC, upvote_count DESC, id DESC LIMIT ? OFFSET ?`,
      )
        .bind(limit, offset)
        .all<Record<string, unknown>>();

      const upvotedIds = new Set<number>();
      if (user) {
        const { results: uRows } = await env.DB.prepare("SELECT project_id FROM upvotes WHERE lower(user_login) = lower(?)")
          .bind(user.username)
          .all<{ project_id: number }>();
        for (const u of uRows) upvotedIds.add(Number(u.project_id));
      }

      const projects = rows.map((r, i) => ({
        ...r,
        rank: offset + i + 1,
        user_upvoted: upvotedIds.has(Number(r.id)),
      }));
      return jsonRes(req, { status: "ok", projects, total: projects.length, scale });
    }

    // 4. Creators
    if (req.method === "GET" && path === "/api/anna/creators") {
      const { results: rows } = await env.DB.prepare(
        "SELECT owner, owner_avatar_url, full_slug, primary_language, final_score, curved_score, upvote_count FROM projects WHERE latest_report_id IS NOT NULL AND deleted_at IS NULL AND COALESCE(is_test, 0) = 0 ORDER BY curved_score DESC",
      ).all<Record<string, unknown>>();
      const map = new Map<string, any>();
      for (const r of rows) {
        const owner = String(r.owner || "");
        if (!owner) continue;
        let entry = map.get(owner);
        if (!entry) {
          entry = {
            owner,
            owner_avatar_url: r.owner_avatar_url || `https://github.com/${owner}.png?size=96`,
            total_repos: 0,
            scores: [] as number[],
            raw_scores: [] as number[],
            languages: [] as string[],
            total_upvotes: 0,
            top_repo_slug: r.full_slug,
            _best: -1,
          };
          map.set(owner, entry);
        }
        entry.total_repos += 1;
        const cs = Number(r.curved_score || 0);
        entry.scores.push(cs);
        entry.raw_scores.push(Number(r.final_score || 0));
        entry.total_upvotes += Number(r.upvote_count || 0);
        const lang = String(r.primary_language || "").trim();
        if (lang && !entry.languages.includes(lang)) entry.languages.push(lang);
        if (cs > entry._best) {
          entry._best = cs;
          entry.top_repo_slug = r.full_slug;
        }
      }
      const creators = Array.from(map.values()).map((c) => ({
        ...c,
        avg_curved_score: Math.round((c.scores.reduce((a: number, b: number) => a + b, 0) / c.scores.length) * 10) / 10,
        avg_raw_score: Math.round((c.raw_scores.reduce((a: number, b: number) => a + b, 0) / c.raw_scores.length) * 10) / 10,
      }));
      return jsonRes(req, { status: "ok", creators });
    }

    // 5. Badges
    const badgeMatch = /^\/api\/anna\/badge\/([^/]+)\/([^/]+)$/.exec(path);
    if (req.method === "GET" && badgeMatch) {
      const owner = decodeURIComponent(badgeMatch[1]);
      const repoRaw = decodeURIComponent(badgeMatch[2]).replace(/\.svg$/i, "");
      const proj = await env.DB.prepare(
        `SELECT p.*, r.factor_scores_json
         FROM projects p
         LEFT JOIN reports r ON r.id = p.latest_report_id
         WHERE lower(p.full_slug) = lower(?) AND p.deleted_at IS NULL AND COALESCE(p.is_test, 0) = 0`
      )
        .bind(`${owner}/${repoRaw}`)
        .first<Record<string, unknown>>();
      const style = url.searchParams.get("style") || "flat";
      const scale = url.searchParams.get("scale") === "raw" ? "raw" : "curve";
      return new Response(renderBadgeSvg(style, proj, scale), {
        headers: {
          "Content-Type": "image/svg+xml",
          "Cache-Control": "public, max-age=60",
          ...corsHeaders(req),
        },
      });
    }

    // 6. Soft-delete (15-day retention + instant un-viewability for others)
    const delMatch = /^\/api\/anna\/project\/([^/]+)\/([^/]+)\/delete$/.exec(path);
    if (req.method === "POST" && delMatch) {
      const user = await extractUser(req, env.SESSION_SECRET);
      if (!user) return jsonRes(req, { detail: "Sign in with GitHub to delete." }, 401);
      const slug = `${decodeURIComponent(delMatch[1])}/${decodeURIComponent(delMatch[2])}`;
      const proj = await env.DB.prepare("SELECT * FROM projects WHERE lower(full_slug) = lower(?)")
        .bind(slug)
        .first<Record<string, unknown>>();
      if (!proj) return jsonRes(req, { detail: "Project not found." }, 404);
      if (!canViewDeleted(proj, user)) {
        return jsonRes(req, { detail: "You do not have permission to delete this project." }, 403);
      }
      const now = new Date();
      const deletedAt = now.toISOString();
      const purgeAfter = new Date(now.getTime() + RETENTION_DAYS * 86400 * 1000).toISOString();
      await env.DB.prepare("UPDATE projects SET deleted_at = ?, purge_after = ?, deleted_by = ?, updated_at = ? WHERE id = ?")
        .bind(deletedAt, purgeAfter, user.username, deletedAt, proj.id)
        .run();
      await recomputeCurve(env.DB);
      return jsonRes(req, {
        status: "ok",
        full_slug: slug,
        deleted_at: deletedAt,
        purge_after: purgeAfter,
        retention_days: RETENTION_DAYS,
        message: `${slug} is now hidden from all other users and scheduled for permanent deletion on ${purgeAfter.slice(0, 10)} (15-day legal-hold retention).`,
      });
    }

    // 7. Upvote
    const upvoteMatch = /^\/api\/anna\/project\/([^/]+)\/([^/]+)\/upvote$/.exec(path);
    if (req.method === "POST" && upvoteMatch) {
      const user = await extractUser(req, env.SESSION_SECRET);
      if (!user) return jsonRes(req, { detail: "Sign in with GitHub to upvote." }, 401);
      const slug = `${decodeURIComponent(upvoteMatch[1])}/${decodeURIComponent(upvoteMatch[2])}`;
      const proj = await env.DB.prepare("SELECT id FROM projects WHERE lower(full_slug) = lower(?) AND deleted_at IS NULL")
        .bind(slug)
        .first<{ id: number }>();
      if (!proj) return jsonRes(req, { detail: "Project not found." }, 404);

      const existing = await env.DB.prepare("SELECT id FROM upvotes WHERE project_id = ? AND lower(user_login) = lower(?)")
        .bind(proj.id, user.username)
        .first<{ id: number }>();
      let upvoted = false;
      if (existing) {
        await env.DB.prepare("DELETE FROM upvotes WHERE id = ?").bind(existing.id).run();
      } else {
        const other = await env.DB.prepare(
          `SELECT u.id, p.full_slug
           FROM upvotes u
           JOIN projects p ON p.id = u.project_id
           WHERE lower(u.user_login) = lower(?) AND u.project_id != ? AND p.deleted_at IS NULL
           LIMIT 1`
        )
          .bind(user.username, proj.id)
          .first<{ id: number; full_slug: string }>();
        if (other) {
          return jsonRes(
            req,
            {
              detail: `You already upvoted ${other.full_slug}. Remove that upvote first (1 upvote per user).`,
              existing_slug: other.full_slug,
            },
            409
          );
        }
        await env.DB.prepare("INSERT INTO upvotes (project_id, user_login, created_at) VALUES (?, ?, ?)")
          .bind(proj.id, user.username, new Date().toISOString())
          .run();
        upvoted = true;
      }
      const cntRow = await env.DB.prepare("SELECT COUNT(*) AS c FROM upvotes WHERE project_id = ?").bind(proj.id).first<{ c: number }>();
      const cnt = Number(cntRow?.c || 0);
      await env.DB.prepare("UPDATE projects SET upvote_count = ? WHERE id = ?").bind(cnt, proj.id).run();
      return jsonRes(req, { status: "ok", full_slug: slug, upvote_count: cnt, user_upvoted: upvoted });
    }

    // 8. Project detail & reports history
    const projMatch = /^\/api\/anna\/project\/([^/]+)\/([^/]+?)(\/reports)?$/.exec(path);
    if (req.method === "GET" && projMatch) {
      const user = await extractUser(req, env.SESSION_SECRET);
      const owner = decodeURIComponent(projMatch[1]);
      const repo = decodeURIComponent(projMatch[2]);
      const slug = `${owner}/${repo}`;
      const proj = await env.DB.prepare("SELECT * FROM projects WHERE lower(full_slug) = lower(?)")
        .bind(slug)
        .first<Record<string, unknown>>();
      if (!proj) return jsonRes(req, { detail: `Anna project '${slug}' not found.` }, 404);
      if (Number(proj.is_test || 0) === 1 && !isAdminUser(user)) {
        return jsonRes(req, { detail: `Anna project '${slug}' not found.` }, 404);
      }
      if (proj.deleted_at && !canViewDeleted(proj, user)) {
        return jsonRes(req, { detail: `Anna project '${slug}' not found.` }, 404);
      }

      const { results: history } = await env.DB.prepare(
        "SELECT id, rubric_version, model_name, submitted_by, commit_sha, final_score, letter_grade, curved_score, curved_letter_grade, is_latest, created_at FROM reports WHERE project_id = ? ORDER BY rubric_rank DESC, id DESC",
      )
        .bind(proj.id)
        .all<Record<string, unknown>>();

      if (projMatch[3]) return jsonRes(req, { status: "ok", reports: history });

      const reportParam = url.searchParams.get("report");
      const rep = reportParam
        ? await env.DB.prepare("SELECT * FROM reports WHERE id = ? AND project_id = ?").bind(Number(reportParam), proj.id).first<Record<string, unknown>>()
        : await env.DB.prepare("SELECT * FROM reports WHERE project_id = ? ORDER BY is_latest DESC, rubric_rank DESC, id DESC LIMIT 1").bind(proj.id).first<Record<string, unknown>>();
      if (!rep) return jsonRes(req, { detail: "Report not found." }, 404);

      return jsonRes(req, {
        status: "ok",
        project: {
          ...proj,
          report_id: rep.id,
          rubric_version: rep.rubric_version,
          model_name: rep.model_name,
          commit_sha: rep.commit_sha,
          final_score: rep.final_score,
          letter_grade: rep.letter_grade,
          curved_score: rep.curved_score,
          curved_letter_grade: rep.curved_letter_grade,
          verdict_title: rep.verdict_title,
          verdict_summary: rep.verdict_summary,
          graded_at: rep.created_at,
          is_latest: Boolean(rep.is_latest),
          full_report: JSON.parse(String(rep.report_data_json || "{}")),
          history,
        },
      });
    }

    // 9. Import report (owner-only; Admin can submit external repos as admin-only test)
    if (req.method === "POST" && path === "/api/anna/reports/import") {
      const user = await extractUser(req, env.SESSION_SECRET);
      if (!user) return jsonRes(req, { detail: "Sign in with GitHub before publishing." }, 401);
      const adm = isAdminUser(user);
      if (!adm) {
        const ban = await getActiveBan(env.DB, user.username);
        if (ban) {
          return jsonRes(req, { detail: `Account banned (${ban.duration_label}): ${ban.reason}` }, 403);
        }
      }

      const body = (await req.json().catch(() => ({}))) as { report?: Record<string, any> };
      const rep = body.report || {};
      const rv = String(rep.rubric_version || "").trim().toLowerCase();
      if (!["v10a", "v10.3a", "v11a", "anna-v1"].includes(rv)) {
        return jsonRes(req, { detail: "Only Anna edition reports can be imported here." }, 400);
      }

      const rawTarget = String(rep.full_slug || rep.repo_source || "").replace(/^https?:\/\/github\.com\//i, "").replace(/\.git$/i, "").replace(/\/+$/, "");
      const [owner, repoName] = rawTarget.split("/");
      if (!owner || !repoName) return jsonRes(req, { detail: "Invalid GitHub repository slug." }, 400);
      const fullSlug = `${owner}/${repoName}`;
      const isOtherOwner = owner.toLowerCase() !== user.username.toLowerCase();
      if (isOtherOwner && !adm) {
        return jsonRes(req, { detail: `You can only publish repositories owned by @${user.username}.` }, 403);
      }
      const isTestReport = isOtherOwner && adm ? 1 : 0;

      const sha = String(rep.commit_sha || "").trim().toLowerCase();
      if (!/^[0-9a-f]{7,40}$/.test(sha)) {
        return jsonRes(req, { detail: "Report is missing a valid commit_sha." }, 400);
      }

      const ghHeaders: Record<string, string> = { Accept: "application/vnd.github+json", "User-Agent": "LetsVibe-Anna-Worker" };
      if (env.GITHUB_VERIFY_TOKEN) ghHeaders.Authorization = `Bearer ${env.GITHUB_VERIFY_TOKEN}`;
      const repoRes = await fetch(`https://api.github.com/repos/${owner}/${repoName}`, { headers: ghHeaders });
      if (!repoRes.ok) return jsonRes(req, { detail: "Repository not found on GitHub or is private." }, 400);
      const repoJson = (await repoRes.json()) as { private?: boolean; owner?: { avatar_url?: string } };
      if (repoJson.private) return jsonRes(req, { detail: "Private repositories cannot be published." }, 400);

      const commitRes = await fetch(`https://api.github.com/repos/${owner}/${repoName}/commits/${sha}`, { headers: ghHeaders });
      if (!commitRes.ok) return jsonRes(req, { detail: "Commit SHA could not be verified on GitHub." }, 400);
      const verifiedSha = String(((await commitRes.json()) as { sha?: string }).sha || sha);

      const now = String(rep.timestamp || new Date().toISOString());
      const finalScore = Math.round(Number(rep.final_vibe_score ?? rep.final_score ?? 0) * 10) / 10;
      const letterGrade = String(rep.letter_grade || calculateLetterGrade(finalScore));
      const rk = rubricVersionRank(rv);
      const arch = (rep.archetype || {}) as Record<string, string>;
      const ownerAvatar = repoJson.owner?.avatar_url || `https://github.com/${owner}.png?size=96`;
      const langs = (rep.deterministic_metrics?.languages || {}) as Record<string, number>;
      const primaryLang = Object.entries(langs).sort((a, b) => Number(b[1] || 0) - Number(a[1] || 0))[0]?.[0] || "";

      rep.commit_sha = verifiedSha;
      rep.rubric_version = rv;
      rep.full_slug = fullSlug;

      const existingProj = await env.DB.prepare("SELECT id FROM projects WHERE lower(full_slug) = lower(?)")
        .bind(fullSlug)
        .first<{ id: number }>();

      let projectId: number;
      if (existingProj) {
        projectId = existingProj.id;
        await env.DB.prepare(
          "UPDATE projects SET owner_avatar_url = ?, primary_language = COALESCE(NULLIF(?, ''), primary_language), archetype_category = ?, archetype_name = ?, submitted_by = COALESCE(?, submitted_by), deleted_at = NULL, purge_after = NULL, deleted_by = NULL, updated_at = ? WHERE id = ?",
        )
          .bind(ownerAvatar, primaryLang, arch.category || "Other", arch.name || arch.category || "Other", user.username, new Date().toISOString(), projectId)
          .run();
      } else {
        const ins = await env.DB.prepare(
          "INSERT INTO projects (provider, owner, repo_name, full_slug, canonical_url, owner_avatar_url, primary_language, archetype_category, archetype_name, rubric_version, submitted_by, is_test, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        )
          .bind("github", owner, repoName, fullSlug, `https://github.com/${fullSlug}`, ownerAvatar, primaryLang, arch.category || "Other", arch.name || arch.category || "Other", rv, user.username, isTestReport, now, now)
          .run();
        projectId = Number(ins.meta.last_row_id);
      }

      const insRep = await env.DB.prepare(
        "INSERT INTO reports (project_id, rubric_version, rubric_rank, model_name, submitted_by, is_test, commit_sha, final_score, letter_grade, verdict_title, verdict_summary, factor_scores_json, report_data_json, is_latest, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
      )
        .bind(
          projectId,
          rv,
          rk,
          rep.model_name || null,
          user.username,
          isTestReport,
          verifiedSha,
          finalScore,
          letterGrade,
          String(rep.verdict_title || ""),
          String(rep.verdict_summary || ""),
          JSON.stringify(rep.factor_evaluations || {}),
          JSON.stringify(rep),
          now,
        )
        .run();
      const reportId = Number(insRep.meta.last_row_id);

      const winner = await env.DB.prepare("SELECT * FROM reports WHERE project_id = ? ORDER BY rubric_rank DESC, id DESC LIMIT 1")
        .bind(projectId)
        .first<Record<string, unknown>>();
      if (winner) {
        await env.DB.prepare("UPDATE reports SET is_latest = CASE WHEN id = ? THEN 1 ELSE 0 END WHERE project_id = ?")
          .bind(winner.id, projectId)
          .run();
        await env.DB.prepare(
          "UPDATE projects SET latest_report_id = ?, final_score = ?, letter_grade = ?, rubric_version = ?, graded_at = ?, updated_at = ? WHERE id = ?",
        )
          .bind(winner.id, winner.final_score, winner.letter_grade, winner.rubric_version, winner.created_at, new Date().toISOString(), projectId)
          .run();
      }

      await recomputeCurve(env.DB);
      const saved = await env.DB.prepare("SELECT curved_score, curved_letter_grade FROM reports WHERE id = ?")
        .bind(reportId)
        .first<{ curved_score: number; curved_letter_grade: string }>();

      return jsonRes(req, {
        status: "ok",
        message: `Published ${fullSlug} (${rv}) to Cloudflare Anna D1.`,
        project_id: projectId,
        report_id: reportId,
        full_slug: fullSlug,
        final_score: finalScore,
        letter_grade: letterGrade,
        curved_score: saved?.curved_score ?? finalScore,
        curved_letter_grade: saved?.curved_letter_grade ?? letterGrade,
        rubric_version: rv,
      });
    }

    // 10. Admin endpoints
    if (path.startsWith("/api/anna/admin")) {
      const user = await extractUser(req, env.SESSION_SECRET);
      if (!isAdminUser(user)) return jsonRes(req, { detail: "Admin authorization required." }, 403);

      if (req.method === "GET" && path === "/api/anna/admin/overview") {
        const tp = await env.DB.prepare("SELECT COUNT(*) AS c FROM projects WHERE latest_report_id IS NOT NULL AND deleted_at IS NULL").first<{ c: number }>();
        const tr = await env.DB.prepare("SELECT COUNT(*) AS c FROM reports").first<{ c: number }>();
        const sd = await env.DB.prepare("SELECT COUNT(*) AS c FROM projects WHERE deleted_at IS NOT NULL").first<{ c: number }>();
        const up = await env.DB.prepare("SELECT COUNT(*) AS c FROM upvotes").first<{ c: number }>();
        const avgs = await env.DB.prepare("SELECT AVG(final_score) AS ar, AVG(curved_score) AS ac FROM projects WHERE latest_report_id IS NOT NULL AND deleted_at IS NULL").first<{ ar: number | null; ac: number | null }>();
        const { results: projects } = await env.DB.prepare("SELECT * FROM projects ORDER BY updated_at DESC, id DESC LIMIT 300").all<Record<string, unknown>>();
        const { results: reports } = await env.DB.prepare(
          "SELECT r.id, r.project_id, p.full_slug, r.rubric_version, r.model_name, r.submitted_by, r.final_score, r.letter_grade, r.curved_score, r.curved_letter_grade, r.is_latest, r.created_at FROM reports r JOIN projects p ON p.id = r.project_id ORDER BY r.id DESC LIMIT 200",
        ).all<Record<string, unknown>>();
        const { results: ticketUsers } = await env.DB.prepare("SELECT user_login FROM support_tickets").all<{ user_login: string }>();
        const { results: bans } = await env.DB.prepare("SELECT * FROM bans").all<Record<string, unknown>>();

        const banByUser = new Map<string, Record<string, unknown>>();
        for (const b of bans) banByUser.set(String(b.github_username || "").toLowerCase(), b);
        const usersMap = new Map<string, { username: string; repo_count: number; ticket_count: number; ban: Record<string, unknown> | null }>();
        const ensureU = (raw: unknown) => {
          const clean = String(raw || "").trim();
          if (!clean) return null;
          const low = clean.toLowerCase();
          if (!usersMap.has(low)) {
            usersMap.set(low, { username: clean, repo_count: 0, ticket_count: 0, ban: banByUser.get(low) || null });
          }
          return usersMap.get(low)!;
        };
        for (const p of projects) {
          const u = ensureU(p.submitted_by || p.owner);
          if (u) u.repo_count += 1;
        }
        for (const t of ticketUsers) {
          const u = ensureU(t.user_login);
          if (u) u.ticket_count += 1;
        }
        for (const b of bans) ensureU(b.github_username);
        const users = Array.from(usersMap.values()).sort((a, b) =>
          (a.ban ? 0 : 1) - (b.ban ? 0 : 1) || b.repo_count - a.repo_count || b.ticket_count - a.ticket_count || a.username.localeCompare(b.username),
        );

        return jsonRes(req, {
          status: "ok",
          backend: "cloudflare",
          stats: {
            total_projects: Number(tp?.c || 0),
            total_reports: Number(tr?.c || 0),
            soft_deleted_projects: Number(sd?.c || 0),
            total_upvotes: Number(up?.c || 0),
            avg_raw_score: Math.round(Number(avgs?.ar || 0) * 10) / 10,
            avg_curved_score: Math.round(Number(avgs?.ac || 0) * 10) / 10,
          },
          projects,
          reports,
          users,
        });
      }

      const restoreMatch = /^\/api\/anna\/admin\/project\/([^/]+)\/([^/]+)\/restore$/.exec(path);
      if (req.method === "POST" && restoreMatch) {
        const slug = `${decodeURIComponent(restoreMatch[1])}/${decodeURIComponent(restoreMatch[2])}`;
        await env.DB.prepare("UPDATE projects SET deleted_at = NULL, purge_after = NULL, deleted_by = NULL, updated_at = ? WHERE lower(full_slug) = lower(?)")
          .bind(new Date().toISOString(), slug)
          .run();
        await recomputeCurve(env.DB);
        return jsonRes(req, { status: "ok", full_slug: slug, restored: true });
      }

      const adminDelMatch = /^\/api\/anna\/admin\/project\/([^/]+)\/([^/]+)$/.exec(path);
      if (req.method === "DELETE" && adminDelMatch) {
        const slug = `${decodeURIComponent(adminDelMatch[1])}/${decodeURIComponent(adminDelMatch[2])}`;
        if (url.searchParams.get("hard") === "1") {
          await env.DB.prepare("DELETE FROM projects WHERE lower(full_slug) = lower(?)").bind(slug).run();
          await recomputeCurve(env.DB);
          return jsonRes(req, { status: "ok", purged: true, full_slug: slug });
        }
        const now = new Date();
        const purgeAfter = new Date(now.getTime() + RETENTION_DAYS * 86400 * 1000).toISOString();
        await env.DB.prepare("UPDATE projects SET deleted_at = ?, purge_after = ?, deleted_by = ?, updated_at = ? WHERE lower(full_slug) = lower(?)")
          .bind(now.toISOString(), purgeAfter, user?.username || "admin", now.toISOString(), slug)
          .run();
        await recomputeCurve(env.DB);
        return jsonRes(req, { status: "ok", full_slug: slug, deleted_at: now.toISOString(), purge_after: purgeAfter });
      }

      if (req.method === "POST" && path === "/api/anna/admin/purge-expired") {
        const res = await env.DB.prepare("DELETE FROM projects WHERE deleted_at IS NOT NULL AND purge_after IS NOT NULL AND purge_after <= ?")
          .bind(new Date().toISOString())
          .run();
        await recomputeCurve(env.DB);
        return jsonRes(req, { status: "ok", purged_count: Number(res.meta.changes || 0) });
      }

      if (req.method === "POST" && path === "/api/anna/admin/recompute-curve") {
        await recomputeCurve(env.DB);
        return jsonRes(req, { status: "ok", message: "Recomputed Anna cohort bell curve." });
      }

      if (req.method === "GET" && path === "/api/anna/admin/tickets") {
        const { results: tickets } = await env.DB.prepare(
          "SELECT * FROM support_tickets ORDER BY CASE WHEN status = 'open' THEN 0 ELSE 1 END, id DESC LIMIT 200",
        ).all<Record<string, unknown>>();
        const { results: msgs } = await env.DB.prepare(
          "SELECT * FROM support_ticket_messages ORDER BY id ASC",
        ).all<Record<string, unknown>>();
        const byTid = new Map<number, Record<string, unknown>[]>();
        for (const m of msgs) {
          const tid = Number(m.ticket_id);
          if (!byTid.has(tid)) byTid.set(tid, []);
          byTid.get(tid)!.push(m);
        }
        const enriched = tickets.map((t) => ({
          ...t,
          messages: byTid.get(Number(t.id)) || [
            { id: 0, ticket_id: t.id, sender_login: t.user_login, sender_role: "user", body: t.message, created_at: t.created_at },
            ...(t.admin_reply ? [{ id: 0, ticket_id: t.id, sender_login: "admin", sender_role: "admin", body: t.admin_reply, created_at: t.updated_at }] : []),
          ],
        }));
        return jsonRes(req, { status: "ok", tickets: enriched });
      }

      if (req.method === "GET" && path === "/api/anna/admin/bans") {
        await env.DB.prepare("DELETE FROM bans WHERE expires_at IS NOT NULL AND expires_at <= ?").bind(new Date().toISOString()).run();
        const { results } = await env.DB.prepare("SELECT * FROM bans ORDER BY id DESC").all<Record<string, unknown>>();
        return jsonRes(req, { status: "ok", bans: results });
      }

      if (req.method === "POST" && path === "/api/anna/admin/ban") {
        const body = (await req.json().catch(() => ({}))) as { github_username?: string; reason?: string; duration_value?: number; duration_unit?: string };
        const uname = String(body.github_username || "").trim().replace(/^@+/, "");
        const reason = String(body.reason || "").trim().slice(0, 500);
        const unit = String(body.duration_unit || "days").trim().toLowerCase();
        if (!uname || !reason) return jsonRes(req, { detail: "GitHub username and reason are required." }, 400);
        const now = new Date();
        const nowIso = now.toISOString();
        const purgeAfter = new Date(now.getTime() + RETENTION_DAYS * 86400 * 1000).toISOString();
        let durationLabel = "permanent";
        let expiresAt: string | null = null;
        if (unit !== "permanent") {
          const val = Math.max(1, Number(body.duration_value || 1));
          const days = unit.startsWith("month") ? val * 30 : unit.startsWith("week") ? val * 7 : val;
          const singular = unit.replace(/s$/, "");
          durationLabel = `${val} ${singular}${val === 1 ? "" : "s"}`;
          expiresAt = new Date(now.getTime() + days * 86400 * 1000).toISOString();
        }
        await env.DB.prepare(
          "INSERT INTO bans (github_username, reason, duration_label, expires_at, banned_by, created_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(github_username) DO UPDATE SET reason=excluded.reason, duration_label=excluded.duration_label, expires_at=excluded.expires_at, banned_by=excluded.banned_by, created_at=excluded.created_at",
        ).bind(uname, reason, durationLabel, expiresAt, user?.username || "admin", nowIso).run();

        const low = uname.toLowerCase();
        await env.DB.prepare(
          "UPDATE projects SET deleted_at = COALESCE(deleted_at, ?), purge_after = COALESCE(purge_after, ?), deleted_by = ? WHERE lower(owner) = ? OR lower(COALESCE(submitted_by, '')) = ?",
        ).bind(nowIso, purgeAfter, `ban:${user?.username || "admin"}`, low, low).run();
        await env.DB.prepare("DELETE FROM upvotes WHERE lower(user_login) = ?").bind(low).run();
        await env.DB.prepare("UPDATE support_tickets SET status = 'resolved', updated_at = ? WHERE lower(user_login) = ? AND status = 'open'").bind(nowIso, low).run();
        await recomputeCurve(env.DB);
        const ban = await env.DB.prepare("SELECT * FROM bans WHERE lower(github_username) = ?").bind(low).first<Record<string, unknown>>();
        return jsonRes(req, { status: "ok", ban });
      }

      if (req.method === "POST" && path === "/api/anna/admin/unban") {
        const body = (await req.json().catch(() => ({}))) as { github_username?: string };
        const low = String(body.github_username || "").trim().replace(/^@+/, "").toLowerCase();
        const res = await env.DB.prepare("DELETE FROM bans WHERE lower(github_username) = ?").bind(low).run();
        return jsonRes(req, { status: "ok", unbanned: Number(res.meta.changes || 0) > 0 });
      }

      const ticketAdminMatch = /^\/api\/anna\/admin\/tickets\/(\d+)$/.exec(path);
      if (req.method === "POST" && ticketAdminMatch) {
        const tid = Number(ticketAdminMatch[1]);
        const body = (await req.json().catch(() => ({}))) as { admin_reply?: string; status?: string };
        const existing = await env.DB.prepare("SELECT * FROM support_tickets WHERE id = ?").bind(tid).first<Record<string, unknown>>();
        if (!existing) return jsonRes(req, { detail: "Ticket not found." }, 404);
        const now = new Date().toISOString();
        const reply = body.admin_reply ? String(body.admin_reply).trim().slice(0, 4000) : "";
        if (reply) {
          await env.DB.prepare(
            "INSERT INTO support_ticket_messages (ticket_id, sender_login, sender_role, body, created_at) VALUES (?, ?, 'admin', ?, ?)",
          ).bind(tid, user?.username || "admin", reply, now).run();
        }
        const st = body.status === "open" || body.status === "resolved" ? body.status : existing.status;
        await env.DB.prepare("UPDATE support_tickets SET admin_reply = COALESCE(NULLIF(?, ''), admin_reply), status = ?, updated_at = ? WHERE id = ?")
          .bind(reply, st, now, tid)
          .run();
        const updated = await env.DB.prepare("SELECT * FROM support_tickets WHERE id = ?").bind(tid).first<Record<string, unknown>>();
        return jsonRes(req, { status: "ok", ticket: updated });
      }
    }

    // 11. User Support Tickets (ongoing thread)
    const replyMatch = /^\/api\/anna\/tickets\/(\d+)\/reply$/.exec(path);
    if (req.method === "POST" && replyMatch) {
      const user = await extractUser(req, env.SESSION_SECRET);
      if (!user?.username) return jsonRes(req, { detail: "Sign in with GitHub to reply." }, 401);
      const tid = Number(replyMatch[1]);
      const body = (await req.json().catch(() => ({}))) as { body?: string };
      const msgBody = String(body.body || "").trim().slice(0, 4000);
      if (!msgBody) return jsonRes(req, { detail: "Reply message is required." }, 400);
      const t = await env.DB.prepare("SELECT * FROM support_tickets WHERE id = ?").bind(tid).first<Record<string, unknown>>();
      if (!t) return jsonRes(req, { detail: "Ticket not found." }, 404);
      const adm = isAdminUser(user);
      if (!adm && String(t.user_login || "").toLowerCase() !== user.username.toLowerCase()) {
        return jsonRes(req, { detail: "Forbidden." }, 403);
      }
      const now = new Date().toISOString();
      const role = adm ? "admin" : "user";
      await env.DB.prepare(
        "INSERT INTO support_ticket_messages (ticket_id, sender_login, sender_role, body, created_at) VALUES (?, ?, ?, ?, ?)",
      ).bind(tid, user.username, role, msgBody, now).run();
      await env.DB.prepare(
        "UPDATE support_tickets SET status = CASE WHEN ? = 'user' THEN 'open' ELSE status END, admin_reply = CASE WHEN ? = 'admin' THEN ? ELSE admin_reply END, updated_at = ? WHERE id = ?",
      ).bind(role, role, msgBody, now, tid).run();
      return jsonRes(req, { status: "ok" });
    }

    if (req.method === "POST" && path === "/api/anna/tickets") {
      const user = await extractUser(req, env.SESSION_SECRET);
      if (!user?.username) return jsonRes(req, { detail: "Sign in with GitHub to submit a support ticket." }, 401);
      const adm = isAdminUser(user);
      if (!adm) {
        const ban = await getActiveBan(env.DB, user.username);
        if (ban) return jsonRes(req, { detail: `Account banned (${ban.duration_label}): ${ban.reason}` }, 403);
        const openCnt = await env.DB.prepare("SELECT COUNT(*) AS c FROM support_tickets WHERE lower(user_login) = lower(?) AND status = 'open'")
          .bind(user.username)
          .first<{ c: number }>();
        if (Number(openCnt?.c || 0) >= 5) {
          return jsonRes(req, { detail: "You already have 5 open support tickets. Please wait for a resolution before opening another." }, 429);
        }
      }
      const body = (await req.json().catch(() => ({}))) as { subject?: string; message?: string; repo_slug?: string };
      const subject = String(body.subject || "").trim().slice(0, 160);
      const message = String(body.message || "").trim().slice(0, 4000);
      const repoSlug = String(body.repo_slug || "").trim().slice(0, 160);
      if (!subject || !message) return jsonRes(req, { detail: "Subject and message are required." }, 400);
      const now = new Date().toISOString();
      const ins = await env.DB.prepare(
        "INSERT INTO support_tickets (user_login, subject, repo_slug, message, status, created_at, updated_at) VALUES (?, ?, ?, ?, 'open', ?, ?)",
      )
        .bind(user.username, subject, repoSlug, message, now, now)
        .run();
      const tid = Number(ins.meta.last_row_id);
      await env.DB.prepare(
        "INSERT INTO support_ticket_messages (ticket_id, sender_login, sender_role, body, created_at) VALUES (?, ?, 'user', ?, ?)",
      ).bind(tid, user.username, message, now).run();
      const ticket = await env.DB.prepare("SELECT * FROM support_tickets WHERE id = ?").bind(tid).first<Record<string, unknown>>();
      return jsonRes(req, { status: "ok", ticket });
    }

    if (req.method === "GET" && path === "/api/anna/me/tickets") {
      const user = await extractUser(req, env.SESSION_SECRET);
      if (!user?.username) return jsonRes(req, { detail: "Sign in with GitHub to view your tickets." }, 401);
      const { results: tickets } = await env.DB.prepare(
        "SELECT * FROM support_tickets WHERE lower(user_login) = lower(?) ORDER BY id DESC LIMIT 100",
      )
        .bind(user.username)
        .all<Record<string, unknown>>();
      const { results: msgs } = await env.DB.prepare("SELECT * FROM support_ticket_messages ORDER BY id ASC").all<Record<string, unknown>>();
      const byTid = new Map<number, Record<string, unknown>[]>();
      for (const m of msgs) {
        const tid = Number(m.ticket_id);
        if (!byTid.has(tid)) byTid.set(tid, []);
        byTid.get(tid)!.push(m);
      }
      const enriched = tickets.map((t) => ({
        ...t,
        messages: byTid.get(Number(t.id)) || [
          { id: 0, ticket_id: t.id, sender_login: t.user_login, sender_role: "user", body: t.message, created_at: t.created_at },
          ...(t.admin_reply ? [{ id: 0, ticket_id: t.id, sender_login: "admin", sender_role: "admin", body: t.admin_reply, created_at: t.updated_at }] : []),
        ],
      }));
      return jsonRes(req, { status: "ok", tickets: enriched });
    }

    return jsonRes(req, { detail: "Not found" }, 404);
  },

  async scheduled(_event: ScheduledEvent, env: Env): Promise<void> {
    const now = new Date();
    const nowIso = now.toISOString();
    const today = nowIso.slice(0, 10);
    // 1. Purge expired 15-day soft-deletes & expired bans
    await env.DB.prepare("DELETE FROM projects WHERE deleted_at IS NOT NULL AND purge_after IS NOT NULL AND purge_after <= ?").bind(nowIso).run();
    await env.DB.prepare("DELETE FROM bans WHERE expires_at IS NOT NULL AND expires_at <= ?").bind(nowIso).run();
    await recomputeCurve(env.DB);
    // 2. Rolling 7-day backup snapshot inside D1 (keeps latest 7 daily snapshots)
    const [p, r, t, m, b] = await Promise.all([
      env.DB.prepare("SELECT * FROM projects").all(),
      env.DB.prepare("SELECT * FROM reports").all(),
      env.DB.prepare("SELECT * FROM support_tickets").all(),
      env.DB.prepare("SELECT * FROM support_ticket_messages").all(),
      env.DB.prepare("SELECT * FROM bans").all(),
    ]);
    const snap = JSON.stringify({
      snapshot_date: today,
      projects: p.results,
      reports: r.results,
      support_tickets: t.results,
      support_ticket_messages: m.results,
      bans: b.results,
    });
    await env.DB.prepare(
      "INSERT INTO backup_snapshots (snapshot_date, payload_json, created_at) VALUES (?, ?, ?) ON CONFLICT(snapshot_date) DO UPDATE SET payload_json=excluded.payload_json, created_at=excluded.created_at",
    ).bind(today, snap, nowIso).run();
    await env.DB.prepare(
      "DELETE FROM backup_snapshots WHERE id NOT IN (SELECT id FROM backup_snapshots ORDER BY snapshot_date DESC LIMIT 7)",
    ).run();
  },
};
