-- Isolated Anna Edition SQLite / Cloudflare D1 Schema
-- Zero dependency on canonical letsvibe.db

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL DEFAULT 'github',
    owner TEXT NOT NULL,
    repo_name TEXT NOT NULL,
    full_slug TEXT NOT NULL UNIQUE,
    canonical_url TEXT NOT NULL,
    repo_icon_url TEXT,
    owner_avatar_url TEXT,
    primary_language TEXT,
    archetype_category TEXT,
    archetype_name TEXT,
    general_category TEXT DEFAULT 'other',
    latest_report_id INTEGER,
    final_score REAL DEFAULT 0.0,
    letter_grade TEXT DEFAULT 'C',
    curved_score REAL DEFAULT 0.0,
    curved_letter_grade TEXT DEFAULT 'C',
    rubric_version TEXT DEFAULT 'v11a',
    model_name TEXT,
    submitted_by TEXT,
    upvote_count INTEGER NOT NULL DEFAULT 0,
    is_test INTEGER NOT NULL DEFAULT 0,
    deleted_at TEXT,
    purge_after TEXT,
    deleted_by TEXT,
    graded_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    rubric_version TEXT NOT NULL,
    rubric_rank INTEGER NOT NULL DEFAULT 0,
    model_name TEXT,
    submitted_by TEXT,
    commit_sha TEXT,
    final_score REAL NOT NULL,
    letter_grade TEXT NOT NULL,
    curved_score REAL NOT NULL DEFAULT 0.0,
    curved_letter_grade TEXT NOT NULL DEFAULT 'C',
    verdict_title TEXT,
    verdict_summary TEXT,
    factor_scores_json TEXT NOT NULL,
    report_data_json TEXT NOT NULL,
    is_latest INTEGER NOT NULL DEFAULT 0,
    is_test INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS upvotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    user_login TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(project_id, user_login),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS support_tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_login TEXT NOT NULL,
    subject TEXT NOT NULL,
    repo_slug TEXT,
    message TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    admin_reply TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS support_ticket_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER NOT NULL,
    sender_login TEXT NOT NULL,
    sender_role TEXT NOT NULL DEFAULT 'user',
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (ticket_id) REFERENCES support_tickets(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS bans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    github_username TEXT NOT NULL UNIQUE,
    reason TEXT NOT NULL,
    duration_label TEXT NOT NULL,
    expires_at TEXT,
    banned_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS backup_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date TEXT NOT NULL UNIQUE,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_anna_projects_slug ON projects(full_slug);
CREATE INDEX IF NOT EXISTS idx_anna_projects_curved ON projects(curved_score DESC, upvote_count DESC);
CREATE INDEX IF NOT EXISTS idx_anna_projects_deleted ON projects(deleted_at, purge_after);
CREATE INDEX IF NOT EXISTS idx_anna_reports_project ON reports(project_id, rubric_rank DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_anna_upvotes_project ON upvotes(project_id);
CREATE INDEX IF NOT EXISTS idx_anna_tickets_user ON support_tickets(user_login, id DESC);
CREATE INDEX IF NOT EXISTS idx_anna_tickets_status ON support_tickets(status, id DESC);
CREATE INDEX IF NOT EXISTS idx_anna_ticket_msgs ON support_ticket_messages(ticket_id, id ASC);
CREATE INDEX IF NOT EXISTS idx_anna_bans_user ON bans(github_username);
