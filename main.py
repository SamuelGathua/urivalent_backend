import os
import sys
import time
import logging
import inspect
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List

from fastapi import FastAPI, APIRouter, HTTPException, Query, Header, Depends, Request, Form, Body, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from qstash import Receiver

# Ensure backend root on path
backend_dir = Path(__file__).resolve().parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from database.db_client import db_client
from delivery.news_email_sender import (
    compile_brief_html,
    verify_unsubscribe_token,
    send_test_email,
    send_daily_news_brief,
    fetch_intelligence_for_brief,
    fetch_market_snapshots_for_brief,
    fetch_market_categories_for_brief,
    fetch_code_and_paper_snapshots,
    get_daily_greeting
)
from scheduler_service import (
    start_scheduler,
    stop_scheduler,
    get_scheduler_status,
    run_news_cycle,
    run_daily_morning_brief_job
)
from security import (
    require_admin,
    verify_admin_credentials,
    create_admin_token
)

logger = logging.getLogger("urivalent.api")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


async def startup_freshness_check():
    """Non-blocking background worker to ensure fresh intelligence if data is stale."""
    await asyncio.sleep(2)  # Allow server to complete binding port
    try:
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        latest_market = db_client.fetchone("SELECT date FROM compute_market_snapshots ORDER BY date DESC, id DESC LIMIT 1;")
        is_stale = not latest_market or latest_market.get("date") != today_str

        if is_stale:
            logger.info("Market data is stale or missing for today. Triggering automatic startup refresh...")
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, run_news_cycle)
            logger.info("Startup intelligence refresh completed successfully.")
    except Exception as e:
        logger.warning(f"Startup freshness check encountered: {e}")


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
    asyncio.create_task(startup_freshness_check())
    yield
    logger.info("Shutting down background scheduler...")
    stop_scheduler()


app = FastAPI(
    title="Urivalent AI Intelligence Engine",
    description="Automated, zero-cost, full-stack AI intelligence pipeline and delivery engine.",
    version="1.0.0",
    lifespan=lifespan
)

# ------------------------------------------------------------------------------
# 1. CORS CONFIGURATION (PUBLIC INTELLIGENCE FEED + ADMIN CONSOLE)
# ------------------------------------------------------------------------------
cors_origins_env = os.getenv("CORS_ORIGINS", "")
allowed_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://urivalent.tech",
    "https://www.urivalent.tech",
    "https://api.urivalent.tech",
]
if cors_origins_env:
    for o in cors_origins_env.split(","):
        o_clean = o.strip()
        if o_clean and o_clean not in allowed_origins:
            allowed_origins.append(o_clean)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_origin_regex=r"^(https?://(localhost|127\.0\.0\.1)(:\d+)?|https://[a-zA-Z0-9-]+\.pages\.dev)$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount /assets static directory for logo and graphics
assets_path = backend_dir / "assets"
if not assets_path.exists():
    assets_path.mkdir(parents=True, exist_ok=True)
app.mount("/assets", StaticFiles(directory=str(assets_path)), name="assets")

# Initialize Jinja2 Templates
templates_dir = backend_dir / "templates"
templates = Jinja2Templates(directory=str(templates_dir))


class TestEmailPayload(BaseModel):
    email: str
    admin_key: Optional[str] = None


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class DeleteUserRequest(BaseModel):
    id: Optional[int] = None
    email: Optional[str] = None


# ------------------------------------------------------------------------------
# 2. PURE REST API: JSON FEED FOR NEXT.JS
# ------------------------------------------------------------------------------

@app.get("/api/feed")
def get_intelligence_feed():
    """
    Pure REST API Feed Endpoint for Next.js Frontend:
    Returns structured JSON with market_tickers, tier1_items, pillar_groups,
    trending_repos, trending_papers, total_arxiv_count, formatted_date, and greeting.
    """
    try:
        tier1_items, pillar_groups = fetch_intelligence_for_brief()
        market_tickers = fetch_market_snapshots_for_brief()
        market_categories = fetch_market_categories_for_brief()
        trending_repos, trending_papers, arxiv_count = fetch_code_and_paper_snapshots()

        base_url = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
        logo_url = os.getenv("LOGO_URL", f"{base_url}/assets/urivalent_logo.png")
        if not logo_url.startswith("http"):
            logo_url = f"{base_url}{logo_url}"

        now_dt = datetime.now(timezone.utc)
        formatted_date = now_dt.strftime("%A, %B %d, %Y")

        return {
            "status": "success",
            "metadata": {
                "service": "Urivalent AI Intelligence Pipeline",
                "timestamp": now_dt.isoformat(),
                "formatted_date": formatted_date,
                "greeting": get_daily_greeting(),
                "logo_url": logo_url,
                "total_arxiv_count": arxiv_count
            },
            "market_tickers": market_tickers,
            "market_categories": market_categories,
            "tier1_items": tier1_items,
            "pillar_groups": pillar_groups,
            "trending_repos": trending_repos,
            "trending_papers": trending_papers
        }
    except Exception as e:
        logger.error(f"Error compiling API feed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to generate intelligence feed: {str(e)}")


# ------------------------------------------------------------------------------
# 3. REST ADMIN CONTROLLER ENDPOINTS (SECURED WITH JWT & REQUIRE_ADMIN)
# ------------------------------------------------------------------------------

# Rate limiting / lockout tracker for admin login (3 trials maximum)
MAX_LOGIN_TRIALS = 3
LOCKOUT_SECONDS = 900  # 15 minutes lockout
LOGIN_ATTEMPTS: Dict[str, Dict[str, Any]] = {}

@app.post("/admin/login")
async def api_admin_login(
    req: AdminLoginRequest,
    request: Request
):
    """
    Hardened Executive Admin Login:
    Enforces maximum 3 login trials per IP with a 15-minute lockout.
    Validates username and passcode with constant-time comparison.
    Issues 8-hour signed JWT access token and secure session cookie.
    """
    client_ip = request.client.host if request and request.client else "unknown"
    now = time.time()

    # 1. Check if IP is currently locked out
    ip_record = LOGIN_ATTEMPTS.get(client_ip)
    if ip_record and ip_record.get("lockout_until", 0) > now:
        remaining_wait = int(ip_record["lockout_until"] - now)
        minutes = remaining_wait // 60
        seconds = remaining_wait % 60
        logger.warning(f"Blocked login attempt from locked-out IP {client_ip} ({remaining_wait}s remaining)")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Maximum login trials exceeded (3/3). Terminal locked for security. Please try again in {minutes}m {seconds:02d}s."
        )

    logger.info(f"Admin login attempt from {client_ip} for username: {req.username}")

    # 2. Verify credentials
    if not verify_admin_credentials(req.username, req.password):
        await asyncio.sleep(1)  # Mitigate timing attacks
        record = LOGIN_ATTEMPTS.get(client_ip, {"attempts": 0, "lockout_until": 0})
        record["attempts"] += 1

        if record["attempts"] >= MAX_LOGIN_TRIALS:
            record["lockout_until"] = now + LOCKOUT_SECONDS
            LOGIN_ATTEMPTS[client_ip] = record
            logger.warning(f"IP {client_ip} reached maximum login trials ({MAX_LOGIN_TRIALS}) — locked out for 15 minutes.")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Maximum login trials exceeded (3/3). Terminal locked for 15 minutes for security."
            )
        else:
            LOGIN_ATTEMPTS[client_ip] = record
            remaining_trials = MAX_LOGIN_TRIALS - record["attempts"]
            logger.warning(f"Failed admin authentication attempt from {client_ip} for username: {req.username}. {remaining_trials} trial(s) remaining.")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid administrator username or password. {remaining_trials} trial{'s' if remaining_trials != 1 else ''} remaining.",
                headers={"WWW-Authenticate": "Bearer"}
            )

    # 3. Successful authentication: Clear failed trials
    if client_ip in LOGIN_ATTEMPTS:
        LOGIN_ATTEMPTS.pop(client_ip, None)

    token = create_admin_token(req.username)
    res = JSONResponse(
        content={
            "success": True,
            "message": "Executive administrator authorization granted.",
            "access_token": token,
            "token_type": "bearer",
            "username": req.username,
            "expires_in": 28800
        },
        status_code=200
    )
    res.set_cookie(
        key="admin_token",
        value=token,
        max_age=28800,
        httponly=True,
        samesite="lax",
        secure=False
    )
    res.set_cookie(
        key="admin_logged_in",
        value="true",
        max_age=28800,
        httponly=False,
        samesite="lax",
        secure=False
    )
    logger.info(f"Admin authentication successful for {req.username} from {client_ip}")
    return res


@app.post("/admin/logout")
async def api_admin_logout():
    """Invalidates session cookies and signs out admin."""
    res = JSONResponse(
        content={"success": True, "message": "Admin session terminated successfully."},
        status_code=200
    )
    res.delete_cookie(key="admin_token", path="/")
    res.delete_cookie(key="admin_logged_in", path="/")
    return res


@app.get("/admin/verify")
async def api_admin_verify(admin: dict = Depends(require_admin)):
    """Verifies currently active administrator session."""
    return {
        "status": "authenticated",
        "username": admin.get("username"),
        "role": admin.get("role")
    }


@app.post("/admin/test-email")
async def api_test_email(
    request: Request,
    admin: dict = Depends(require_admin)
):
    """
    Triggers a test email via Resend strictly to target_email without altering is_dispatched.
    Accepts JSON body: {"email": "user@example.com"} or Form data: email=...
    Protected by require_admin.
    """
    target_email = None

    try:
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            body_data = await request.json()
            target_email = body_data.get("email")
        else:
            form_data = await request.form()
            target_email = form_data.get("email")
    except Exception as e:
        logger.warning(f"Error parsing request payload: {e}")

    if not target_email:
        raise HTTPException(status_code=400, detail="Missing required 'email' field in request.")

    logger.info(f"API admin ({admin.get('username')}) requested test email for: {target_email}")
    res_or_coro = send_test_email(str(target_email))
    if inspect.iscoroutine(res_or_coro):
        result = await res_or_coro
    else:
        result = res_or_coro

    if result.get("status") == "success":
        return {
            "status": "success",
            "message": f"Test briefing successfully dispatched to {target_email}!",
            "target_email": target_email,
            "resend_live": result.get("resend_live", False),
            "triggered_by": admin.get("username")
        }
    else:
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to dispatch test email."))


@app.post("/admin/force-news")
def api_force_news(
    admin: dict = Depends(require_admin)
):
    """Triggers on-demand scraping and synthesis into DB. Protected by require_admin."""
    logger.info(f"REST API triggered force-news cycle by admin: {admin.get('username')}")
    result = run_news_cycle()
    if result.get("status") == "success":
        return {
            "status": "success",
            "message": f"Ingestion cycle completed successfully! Ingested {result.get('raw_count', 0)} raw items and synthesized {result.get('synthesized_count', 0)} entries.",
            "data": result,
            "triggered_by": admin.get("username")
        }
    else:
        raise HTTPException(status_code=500, detail=result.get("error", "Ingestion cycle failed."))


@app.post("/admin/send-morning-brief")
@app.post("/send-daily-news-email")
def api_send_morning_brief(
    admin: dict = Depends(require_admin)
):
    """Triggers on-demand morning briefing batch dispatch. Protected by require_admin."""
    logger.info(f"REST API triggered morning brief delivery cycle by admin: {admin.get('username')}")
    result = run_daily_morning_brief_job()
    if result.get("status") == "success":
        return {
            "status": "success",
            "message": "Morning briefing cycle executed and emails dispatched successfully!",
            "data": result,
            "triggered_by": admin.get("username")
        }
    else:
        raise HTTPException(status_code=500, detail=result.get("error", "Morning brief execution failed."))


@app.get("/admin/users")
@app.get("/admin/subscribers")
def api_get_admin_users(
    admin: dict = Depends(require_admin)
):
    """
    Fetches user and subscriber records directly from the database for the executive admin console.
    Protected by require_admin.
    """
    try:
        users = db_client.fetchall(
            """
            SELECT id, username, email, is_active, is_verified, is_subscribed, created_at 
            FROM users 
            ORDER BY id DESC;
            """
        )
        return {
            "status": "success",
            "count": len(users),
            "users": users
        }
    except Exception as e:
        logger.error(f"Error reading users from database: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Database query error: {str(e)}")


@app.delete("/admin/users/{user_id}")
@app.delete("/admin/subscribers/{user_id}")
def api_delete_admin_user_by_id(
    user_id: int,
    admin: dict = Depends(require_admin)
):
    """
    Deletes a subscriber's details directly from the database (master unsubscribe).
    Removes the subscriber from both `users` and `subscribers` tables.
    Protected by require_admin.
    """
    try:
        user = db_client.fetchone("SELECT id, username, email FROM users WHERE id = ?;", (user_id,))
        if not user:
            raise HTTPException(status_code=404, detail=f"Subscriber with ID {user_id} was not found in the database.")

        target_email = user.get("email")

        # 1. Permanently delete from users table
        db_client.execute("DELETE FROM users WHERE id = ?;", (user_id,))

        # 2. Also delete from subscribers table if it exists
        if target_email:
            try:
                db_client.execute("DELETE FROM subscribers WHERE email = ?;", (target_email,))
            except Exception as e:
                logger.warning(f"Note: Could not delete from subscribers table: {e}")

        logger.info(f"Admin '{admin.get('username')}' master-deleted subscriber {target_email} (ID: {user_id}).")
        return {
            "status": "success",
            "message": f"Subscriber {target_email} has been permanently deleted and master-unsubscribed.",
            "deleted_id": user_id,
            "deleted_email": target_email
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting subscriber ID {user_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Database deletion error: {str(e)}")


@app.delete("/admin/users")
@app.delete("/admin/subscribers")
def api_delete_admin_user(
    req: Optional[DeleteUserRequest] = Body(None),
    id: Optional[int] = Query(None),
    email: Optional[str] = Query(None),
    admin: dict = Depends(require_admin)
):
    """
    Alternative delete endpoint supporting query params or JSON body (id or email).
    Protected by require_admin.
    """
    target_id = (req.id if req and req.id is not None else None) or id
    target_email = (req.email if req and req.email else None) or email

    if target_id is None and not target_email:
        raise HTTPException(status_code=400, detail="Missing required 'id' or 'email' parameter.")

    try:
        if target_id is not None:
            user = db_client.fetchone("SELECT id, username, email FROM users WHERE id = ?;", (target_id,))
        else:
            user = db_client.fetchone("SELECT id, username, email FROM users WHERE email = ?;", (target_email,))

        if not user:
            raise HTTPException(status_code=404, detail="Subscriber record not found in the database.")

        uid = user.get("id")
        uemail = user.get("email")

        db_client.execute("DELETE FROM users WHERE id = ?;", (uid,))
        if uemail:
            try:
                db_client.execute("DELETE FROM subscribers WHERE email = ?;", (uemail,))
            except Exception:
                pass

        logger.info(f"Admin '{admin.get('username')}' master-deleted subscriber {uemail} (ID: {uid}).")
        return {
            "status": "success",
            "message": f"Subscriber {uemail} has been permanently deleted and master-unsubscribed.",
            "deleted_id": uid,
            "deleted_email": uemail
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting subscriber: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Database deletion error: {str(e)}")


# ------------------------------------------------------------------------------
# 4. CRYPTOGRAPHICALLY VERIFIED QSTASH NOTIFICATION TRIGGER
# ------------------------------------------------------------------------------

router = APIRouter()

# Initialize the QStash receiver with environment variables
receiver = Receiver(
    current_signing_key=os.environ.get("QSTASH_CURRENT_SIGNING_KEY"),
    next_signing_key=os.environ.get("QSTASH_NEXT_SIGNING_KEY"),
)

@router.post("/api/trigger-notifications")
async def trigger_scheduled_emails(request: Request):
    signature = request.headers.get("Upstash-Signature")
    
    if not signature:
        raise HTTPException(status_code=401, detail="Missing QStash signature")
        
    body = await request.body()
    
    # 1. Strict Security Check: Cryptographically verify QStash sent this
    try:
        active_receiver = receiver
        current_k = os.environ.get("QSTASH_CURRENT_SIGNING_KEY")
        next_k = os.environ.get("QSTASH_NEXT_SIGNING_KEY")
        if (current_k or next_k) and (active_receiver._current_signing_key != current_k or active_receiver._next_signing_key != next_k):
            active_receiver = Receiver(current_signing_key=current_k, next_signing_key=next_k)

        expected_url = os.environ.get("QSTASH_TRIGGER_URL", "https://api.urivalent.tech/api/trigger-notifications")
        active_receiver.verify(
            body=body.decode("utf-8"),
            signature=signature,
            url=expected_url # Must match exactly
        )
    except Exception as e:
        logger.warning(f"QStash cryptographic signature verification failed: {e}")
        raise HTTPException(status_code=401, detail="Invalid execution signature")
    
    # 2. Execute Notification Logic
    try:
        logger.info("Executing QStash-verified daily notification dispatch via Resend...")
        loop = asyncio.get_running_loop()

        # Check if custom payload requested full morning cycle vs direct brief dispatch
        payload = {}
        if body:
            try:
                import json
                payload = json.loads(body.decode("utf-8"))
            except Exception:
                pass

        if payload.get("full_cycle") or payload.get("action") == "full_cycle":
            dispatch_report = await loop.run_in_executor(None, run_daily_morning_brief_job)
        else:
            dispatch_report = await loop.run_in_executor(None, send_daily_news_brief)

        if isinstance(dispatch_report, dict) and dispatch_report.get("status") == "error":
            raise RuntimeError(dispatch_report.get("error", "Failed to dispatch daily notification briefing."))

        logger.info(f"QStash notification dispatch completed successfully: {dispatch_report}")
        return {
            "status": "success",
            "message": "Notifications dispatched and verified by QStash.",
            "report": dispatch_report
        }
    except Exception as e:
        logger.error(f"Error in QStash notification pipeline: {e}", exc_info=True)
        # If this fails, returning a 500 tells QStash to retry later
        raise HTTPException(status_code=500, detail=str(e))

app.include_router(router)


# ------------------------------------------------------------------------------
# 5. HEALTH, PREVIEWS & UNSUBSCRIBE
# ------------------------------------------------------------------------------

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

    db_client.execute("UPDATE users SET is_subscribed = 0 WHERE email = ?;", (email,))
    logger.info(f"Subscriber {email} successfully unsubscribed.")

    return HTMLResponse(
        content=f"""
        <!DOCTYPE html>
        <html>
        <head><title>Unsubscribed — Urivalent</title></head>
        <body style="font-family: sans-serif; background: #FFFFFF; color: #164A41; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0;">
          <div style="background: #F8FAF8; border: 1px solid #9DC88D; border-radius: 8px; padding: 32px; max-width: 480px; text-align: center; box-shadow: 0 4px 12px rgba(22, 74, 65, 0.08);">
            <h2 style="color: #F1B24A; margin-top: 0;">Unsubscribed Successfully</h2>
            <p style="color: #4D774E; font-size: 14px; line-height: 1.5;">
              <strong>{email}</strong> has been unsubscribed from the daily Urivalent Intelligence Briefing.
            </p>
          </div>
        </body>
        </html>
        """,
        status_code=200
    )


@app.get("/", response_class=HTMLResponse)
def fallback_home(request: Request):
    """Fallback home: renders index.html template or redirects to Next.js."""
    try:
        tier1_items, pillar_groups = fetch_intelligence_for_brief()
        market_tickers = fetch_market_snapshots_for_brief()
        trending_repos, trending_papers, arxiv_count = fetch_code_and_paper_snapshots()
        base_url = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
        logo_url = os.getenv("LOGO_URL", f"{base_url}/assets/urivalent_logo.jfif")
        now_dt = datetime.now(timezone.utc)
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "request": request,
                "logo_url": logo_url,
                "formatted_date": now_dt.strftime("%A, %B %d, %Y"),
                "greeting": get_daily_greeting(),
                "market_tickers": market_tickers,
                "total_arxiv_count": arxiv_count,
                "tier1_items": tier1_items,
                "pillar_groups": pillar_groups,
                "trending_repos": trending_repos,
                "trending_papers": trending_papers,
                "current_year": now_dt.year
            }
        )
    except Exception:
        return JSONResponse({"service": "Urivalent API", "feed": "/api/feed", "health": "/health"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
