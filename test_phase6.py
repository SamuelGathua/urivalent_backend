import sys
import os
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(backend_dir))

from fastapi.testclient import TestClient
from main import app
from scheduler_service import start_scheduler, stop_scheduler, get_scheduler_status
from delivery.news_email_sender import generate_unsubscribe_token

def test_phase6():
    print("==================================================")
    print("URIVALENT PHASE 6: SCHEDULER & FASTAPI CONTROL TEST")
    print("==================================================")

    # 1. Test APScheduler Service Configuration
    print("\n[TEST 1] Testing Scheduler Service...")
    start_scheduler()
    status = get_scheduler_status()
    print(f"-> Scheduler Status: {status}")
    assert status["running"] is True, "Scheduler is not running!"
    assert len(status["jobs"]) >= 2, f"Expected at least 2 scheduled jobs, found {len(status['jobs'])}"
    
    job_ids = [j["id"] for j in status["jobs"]]
    print(f"  [OK] Registered Jobs: {job_ids}")
    assert "urivalent_morning_brief" in job_ids, "Missing morning brief job!"
    assert "urivalent_intraday_cycle" in job_ids, "Missing intraday cycle job!"
    stop_scheduler()
    print("  [OK] Scheduler started and stopped gracefully.")

    # 2. Test FastAPI Endpoints via TestClient
    print("\n[TEST 2] Testing FastAPI Endpoints via TestClient...")
    with TestClient(app) as client:
        # GET /
        resp_root = client.get("/")
        assert resp_root.status_code == 200, f"Root returned {resp_root.status_code}"
        print(f"  [OK] GET / -> HTTP 200 (Length: {len(resp_root.text)})")

        # GET /health
        resp_health = client.get("/health")
        assert resp_health.status_code == 200, f"Health returned {resp_health.status_code}"
        health_data = resp_health.json()
        print(f"  [OK] GET /health -> HTTP 200: DB={health_data['database']['mode']}, Counts={health_data['counts']}")
        assert health_data["status"] == "healthy", "Health status is not healthy!"

        # GET /assets/urivalent_logo.jfif
        resp_asset = client.get("/assets/urivalent_logo.jfif")
        assert resp_asset.status_code == 200, f"Asset returned {resp_asset.status_code}"
        print(f"  [OK] GET /assets/urivalent_logo.jfif -> HTTP 200 (Size: {len(resp_asset.content):,} bytes)")

        # GET /preview-brief
        resp_preview = client.get("/preview-brief")
        assert resp_preview.status_code == 200, f"Preview brief returned {resp_preview.status_code}"
        assert "<!DOCTYPE html>" in resp_preview.text, "Preview HTML missing DOCTYPE!"
        assert "#164A41" in resp_preview.text, "Preview HTML missing Urivalent brand color #164A41!"
        print(f"  [OK] GET /preview-brief -> HTTP 200 (Rendered HTML length: {len(resp_preview.text):,} chars)")

        # GET /auth/unsubscribe
        test_token = generate_unsubscribe_token("test@urivalent.com")
        resp_unsub = client.get(f"/auth/unsubscribe?token={test_token}")
        assert resp_unsub.status_code == 200, f"Unsubscribe returned {resp_unsub.status_code}"
        assert "Unsubscribed Successfully" in resp_unsub.text, "Unsubscribe confirmation text missing!"
        print(f"  [OK] GET /auth/unsubscribe?token=... -> HTTP 200 (Unsubscribed successfully)")

    # 3. Verify Dockerfile exists
    print("\n[TEST 3] Verifying Containerization Artifacts...")
    dockerfile_path = backend_dir / "Dockerfile"
    dockerignore_path = backend_dir / ".dockerignore"
    assert dockerfile_path.exists(), "Dockerfile missing!"
    assert dockerignore_path.exists(), ".dockerignore missing!"
    print(f"  [OK] Dockerfile verified at: {dockerfile_path}")
    print(f"  [OK] .dockerignore verified at: {dockerignore_path}")

    print("\n==================================================")
    print("ALL PHASE 6 CHECKS PASSED SUCCESSFULLY WITH ZERO ERRORS!")
    print("==================================================")

if __name__ == "__main__":
    test_phase6()

