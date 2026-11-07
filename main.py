import os
import sys
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Header, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

# Ensure backend root on path
backend_dir = Path(__file__).resolve().parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from database.db_client import db_client
from delivery.news_email_sender import compile_brief_html, verify_unsubscribe_token
from scheduler_service import (
    start_scheduler,
    stop_scheduler,
    get_scheduler_status,
    run_news_cycle,
    run_daily_morning_brief_job
)

logger = logging.getLogger("urivalent.api")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager: Initializes DB and triggers background scheduler."""
    logger.info("Initializing Urivalent database schema...")
    try:
        db_client.init_db()
    except Exception as e:
        logger.error(f"DB initialization warning: {e}")

    logger.info("Starting background scheduler service...")
    start_scheduler()
    yield
    logger.info("Shutting down background scheduler...")
    stop_scheduler()


app = FastAPI(
    title="Urivalent AI Intelligence Engine",
    description="Automated, zero-cost, full-stack AI intelligence pipeline and delivery engine.",
    version="1.0.0",
    lifespan=lifespan
)

# CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount /assets static directory for logo and graphics
assets_path = backend_dir / "assets"
if not assets_path.exists():
    assets_path.mkdir(parents=True, exist_ok=True)
app.mount("/assets", StaticFiles(directory=str(assets_path)), name="assets")


# ------------------------------------------------------------------------------
# 1. ROOT & HEALTH ENDPOINTS
# ------------------------------------------------------------------------------

@app.get("/")
def root():
    return {
        "service": "Urivalent AI Intelligence Pipeline",
        "status": "online",
        "version": "1.0.0",
        "docs_url": "/docs",
        "health_url": "/health",
        "preview_url": "/preview-brief"
    }


@app.get("/health")
def health_check():
    """Returns DB mode, snapshot counts, subscriber count, and scheduler status."""
    db_mode = "Cloudflare D1" if db_client.use_cf_d1 else "Local SQLite"
    
    try:
        intel_count = db_client.fetchone("SELECT COUNT(*) as c FROM ai_intelligence_items;")
        users_count = db_client.fetchone("SELECT COUNT(*) as c FROM users WHERE is_active = 1;")
        market_count = db_client.fetchone("SELECT COUNT(*) as c FROM compute_market_snapshots;")
        repos_count = db_client.fetchone("SELECT COUNT(*) as c FROM github_trending_snapshots;")
        papers_count = db_client.fetchone("SELECT COUNT(*) as c FROM research_paper_snapshots;")

        stored_counts = {
            "ai_intelligence_items": intel_count["c"] if intel_count else 0,
            "active_users": users_count["c"] if users_count else 0,
            "compute_market_snapshots": market_count["c"] if market_count else 0,
            "github_trending_snapshots": repos_count["c"] if repos_count else 0,
            "research_paper_snapshots": papers_count["c"] if papers_count else 0
        }
    except Exception as e:
        logger.error(f"Error reading health counts: {e}")
        stored_counts = {"error": str(e)}

    return {
        "status": "healthy",
        "database": {
            "mode": db_mode,
            "sqlite_path": str(db_client.sqlite_path) if not db_client.use_cf_d1 else None,
            "cf_database_id": db_client.cf_d1_database_id if db_client.use_cf_d1 else None
        },
        "counts": stored_counts,
        "scheduler": get_scheduler_status()
    }


# ------------------------------------------------------------------------------
# 2. PREVIEW & AUTH ENDPOINTS
# ------------------------------------------------------------------------------

@app.get("/preview-brief", response_class=HTMLResponse)
def preview_brief():
    """Renders the live compiled ai_brief.html directly in the browser."""
    try:
        html_content = compile_brief_html(preview_mode=True)
        return HTMLResponse(content=html_content, status_code=200)
    except Exception as e:
        logger.error(f"Error compiling preview brief: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to compile briefing HTML: {str(e)}")


@app.get("/auth/unsubscribe", response_class=HTMLResponse)
def unsubscribe(token: str = Query(..., description="Signed JWT unsubscribe token")):
    """Validates 30-day JWT token and updates subscriber status."""
    email = verify_unsubscribe_token(token)
    if not email:
        return HTMLResponse(
            content="""
            <div style="font-family: sans-serif; text-align: center; padding: 40px; background: #0F352E; color: #FFF;">
              <h2 style="color: #FF5252;">Invalid or Expired Link</h2>
              <p>The unsubscribe token provided is invalid or has expired.</p>
            </div>
            """,
            status_code=400
        )

    # Deactivate subscription
    db_client.execute("UPDATE users SET is_subscribed = 0 WHERE email = ?;", (email,))
    logger.info(f"Subscriber {email} successfully unsubscribed.")

    return HTMLResponse(
        content=f"""
        <!DOCTYPE html>
        <html>
        <head><title>Unsubscribed — Urivalent</title></head>
        <body style="font-family: sans-serif; background: #0F352E; color: #FFFFFF; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0;">
          <div style="background: #164A41; border: 1px solid #9DC88D; border-radius: 8px; padding: 32px; max-width: 480px; text-align: center;">
            <h2 style="color: #F1B24A; margin-top: 0;">Unsubscribed Successfully</h2>
            <p style="color: #E8F5E9; font-size: 14px; line-height: 1.5;">
              <strong>{email}</strong> has been unsubscribed from the daily Urivalent Intelligence Briefing.
            </p>
            <p style="color: #9DC88D; font-size: 12px; margin-top: 24px;">
              You can re-subscribe at any time from your account preferences.
            </p>
          </div>
        </body>
        </html>
        """,
        status_code=200
    )


# ------------------------------------------------------------------------------
# 3. ADMINISTRATIVE TRIGGER ENDPOINTS
# ------------------------------------------------------------------------------

@app.post("/admin/send-morning-brief")
@app.post("/send-daily-news-email")
def trigger_morning_brief():
    """Manual on-demand trigger: Full scraping, LLM synthesis, and batch email dispatch."""
    logger.info("Manual trigger received for morning brief delivery cycle...")
    result = run_daily_morning_brief_job()
    return JSONResponse(content=result, status_code=200 if result.get("status") == "success" else 500)


@app.post("/admin/force-news")
def trigger_force_news():
    """Manual on-demand trigger: Scraping and synthesis into DB without emailing."""
    logger.info("Manual trigger received for force-news ingestion cycle...")
    result = run_news_cycle()
    return JSONResponse(content=result, status_code=200 if result.get("status") == "success" else 500)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
