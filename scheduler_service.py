import os
import sys
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

backend_dir = Path(__file__).resolve().parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from ingestion.fetch_all import fetch_from_sources
from engine.ai_intelligence_engine import process_intelligence_pipeline
from engine.notifications_engine import run_market_notifications
from delivery.news_email_sender import send_daily_news_brief

logger = logging.getLogger("urivalent.scheduler")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

NAIROBI_TZ = pytz.timezone(os.getenv("TIMEZONE", "Africa/Nairobi"))
scheduler = BackgroundScheduler(timezone=NAIROBI_TZ)


def run_news_cycle() -> Dict[str, Any]:
    """
    Intraday Ingestion Cycle:
    Runs ingestion streams across market pulse, research pulse, RSS, and social aggregators.
    Deduplicates, clusters, and synthesizes into ai_intelligence_items without sending emails.
    """
    start_time = datetime.now(NAIROBI_TZ)
    logger.info("Starting automated Intraday Ingestion Cycle...")

    try:
        raw_items = fetch_from_sources(
            include_market=True,
            include_research=True,
            include_rss=True,
            include_social=True,
            include_news_apis=True,
            persist_snapshots=True
        )
        logger.info(f"Intraday cycle: fetched {len(raw_items)} raw items.")

        synthesized = process_intelligence_pipeline(raw_items)
        notifications = run_market_notifications(synthesized)

        duration = (datetime.now(NAIROBI_TZ) - start_time).total_seconds()
        logger.info(f"Intraday Ingestion Cycle completed in {duration:.1f}s. Synthesized {len(synthesized)} items.")

        return {
            "status": "success",
            "cycle": "intraday",
            "raw_count": len(raw_items),
            "synthesized_count": len(synthesized),
            "notifications_count": len(notifications),
            "duration_seconds": duration
        }
    except Exception as e:
        logger.error(f"Error in Intraday Ingestion Cycle: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


def run_daily_morning_brief_job() -> Dict[str, Any]:
    """
    Morning Delivery Cycle (08:50 AM EAT, mon-sun):
    1. Runs full ingestion matrix.
    2. Clusters and synthesizes structured insights.
    3. Evaluates Tier-1 breakthroughs.
    4. Compiles and dispatches daily HTML briefing to subscribers via Resend.
    """
    start_time = datetime.now(NAIROBI_TZ)
    logger.info("Executing Urivalent Daily Morning Brief Job (08:50 AM EAT)...")

    try:
        # Step 1: Ingestion
        raw_items = fetch_from_sources(
            include_market=True,
            include_research=True,
            include_rss=True,
            include_social=True,
            include_news_apis=True,
            persist_snapshots=True
        )

        # Step 2: Synthesis
        synthesized = process_intelligence_pipeline(raw_items)

        # Step 3: Push alerts
        notifications = run_market_notifications(synthesized)

        # Step 4: Email Delivery
        dispatch_report = send_daily_news_brief(batch_size=100)

        duration = (datetime.now(NAIROBI_TZ) - start_time).total_seconds()
        logger.info(f"Morning Brief Job completed in {duration:.1f}s. Dispatch: {dispatch_report}")

        return {
            "status": "success",
            "cycle": "morning_brief",
            "raw_count": len(raw_items),
            "synthesized_count": len(synthesized),
            "dispatch": dispatch_report,
            "duration_seconds": duration
        }
    except Exception as e:
        logger.error(f"Error in Daily Morning Brief Job: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


def configure_scheduler_jobs():
    """Register cron schedules according to Urivalent operational timetable."""
    # 1. Morning Delivery Cycle: 08:50 AM EAT daily (mon-sun)
    scheduler.add_job(
        run_daily_morning_brief_job,
        trigger=CronTrigger(hour=8, minute=50, day_of_week="mon-sun", timezone=NAIROBI_TZ),
        id="urivalent_morning_brief",
        name="Urivalent Morning Delivery Cycle (08:50 EAT)",
        replace_existing=True
    )

    # 2. Ingestion Cycle: Every 30 mins for 24 hours daily (mon-sun)
    scheduler.add_job(
        run_news_cycle,
        trigger=CronTrigger(minute="0,30", timezone=NAIROBI_TZ),
        id="urivalent_intraday_cycle",
        name="Urivalent Ingestion (Every 30m, 24 Hours EAT)",
        replace_existing=True
    )

    logger.info("Configured Urivalent scheduler cron jobs.")


def start_scheduler():
    """Start APScheduler in background if not already running."""
    if not scheduler.running:
        configure_scheduler_jobs()
        scheduler.start()
        logger.info("APScheduler service started successfully.")


def stop_scheduler():
    """Gracefully shutdown APScheduler."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("APScheduler service stopped.")


def get_scheduler_status() -> Dict[str, Any]:
    """Inspect active jobs and next run times."""
    jobs_info = []
    if scheduler.running:
        for job in scheduler.get_jobs():
            next_run = job.next_run_time.isoformat() if job.next_run_time else None
            jobs_info.append({
                "id": job.id,
                "name": job.name,
                "next_run_time": next_run
            })
    return {
        "running": scheduler.running,
        "timezone": str(NAIROBI_TZ),
        "jobs": jobs_info
    }


if __name__ == "__main__":
    start_scheduler()
    print("Scheduler Status:", get_scheduler_status())
    stop_scheduler()

