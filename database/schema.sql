-- ==============================================================================
-- URIVALENT APPEND-ONLY SCHEMA (SQLite & Cloudflare D1 Compatible)
-- CRITICAL DIRECTIVE: NEVER USE TRUNCATE. Append-only historical intelligence.
-- ==============================================================================

-- 1. AI Intelligence Items
CREATE TABLE IF NOT EXISTS ai_intelligence_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id TEXT,
    pillar_category TEXT,
    primary_entity TEXT,
    headline TEXT UNIQUE,
    what_happened TEXT,
    technical_summary TEXT,
    strategic_implication TEXT,
    hype_vs_substance TEXT,
    signal_score INTEGER,
    source_urls TEXT,
    image_url TEXT,
    published_at TEXT,
    is_dispatched INTEGER DEFAULT 0
);

-- 2. Compute & Macro Market Snapshots
CREATE TABLE IF NOT EXISTS compute_market_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT,
    ticker TEXT,
    close_price REAL,
    daily_change_pct REAL,
    five_day_trend_pct REAL,
    volume INTEGER,
    volume_anomaly INTEGER DEFAULT 0,
    UNIQUE(date, ticker)
);

-- 3. GitHub Velocity Trending Snapshots
CREATE TABLE IF NOT EXISTS github_trending_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT,
    repo_name TEXT,
    repo_url TEXT,
    language TEXT,
    stars_gained INTEGER,
    total_stars INTEGER,
    description TEXT,
    UNIQUE(date, repo_name)
);

-- 4. Research Paper Radar Snapshots (HuggingFace & arXiv)
CREATE TABLE IF NOT EXISTS research_paper_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT,
    paper_id TEXT UNIQUE,
    title TEXT,
    url TEXT,
    upvotes INTEGER DEFAULT 0,
    summary TEXT,
    source TEXT
);

-- 5. Subscribers / Users
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT,
    email TEXT UNIQUE,
    is_active INTEGER DEFAULT 1,
    is_verified INTEGER DEFAULT 1,
    is_subscribed INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Performance Indexes
CREATE INDEX IF NOT EXISTS idx_intel_published_at ON ai_intelligence_items(published_at);
CREATE INDEX IF NOT EXISTS idx_intel_is_dispatched ON ai_intelligence_items(is_dispatched);
CREATE INDEX IF NOT EXISTS idx_intel_cluster_id ON ai_intelligence_items(cluster_id);
CREATE INDEX IF NOT EXISTS idx_compute_date ON compute_market_snapshots(date);
CREATE INDEX IF NOT EXISTS idx_github_date ON github_trending_snapshots(date);
CREATE INDEX IF NOT EXISTS idx_paper_date ON research_paper_snapshots(date);

