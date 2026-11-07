import sys
import os
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(backend_dir))

from database.db_client import db_client
from delivery.news_email_sender import (
    compile_brief_html,
    send_daily_news_brief,
    generate_unsubscribe_token,
    verify_unsubscribe_token
)

def test_phase5():
    print("==================================================")
    print("URIVALENT PHASE 5: HTML TEMPLATE & DISPATCH TEST")
    print("==================================================")

    # 1. Test JWT Unsubscribe Token Generation & Verification
    print("\n[TEST 1] Testing RFC JWT Unsubscribe Token...")
    test_email = "test@urivalent.com"
    token = generate_unsubscribe_token(test_email)
    assert token and isinstance(token, str), "Token generation failed!"
    print(f"-> Generated JWT Token: {token[:35]}...")

    verified_email = verify_unsubscribe_token(token)
    assert verified_email == test_email, f"Verification failed! Expected {test_email}, got {verified_email}"
    print(f"  [OK] JWT Token verified successfully for subscriber: {verified_email}")

    # 2. Test HTML Brief Compilation
    print("\n[TEST 2] Compiling live HTML briefing using templates/ai_brief.html...")
    html_output = compile_brief_html(subscriber_email=test_email, preview_mode=True)
    assert len(html_output) > 2000, f"Compiled HTML output is too short ({len(html_output)} bytes)"

    # Save to preview_output.html
    preview_file = backend_dir / "preview_output.html"
    with open(preview_file, "w", encoding="utf-8") as f:
        f.write(html_output)
    print(f"-> Saved preview briefing file to: {preview_file} ({len(html_output):,} bytes)")

    # 3. Verify Exact Urivalent Palette & Sections in HTML
    print("\n[TEST 3] Verifying Exact Urivalent Palette & Structure...")
    palette_colors = {
        "#0F352E": "Outer Wrapper",
        "#164A41": "Main Container",
        "#4D774E": "Cards Background",
        "#9DC88D": "Secondary / Accent Green",
        "#F1B24A": "Primary Gold Accent",
        "#FFFFFF": "Primary Text"
    }

    for color, name in palette_colors.items():
        assert color in html_output, f"Missing required brand color {color} ({name}) in rendered template!"
        print(f"  [OK] Brand Color Verified: {color} ({name})")

    # Verify Core Sections
    required_sections = [
        "urivalent_logo.jfif",
        "THE COMPUTE & CAPITAL TICKER",
        "SECTION 1 — TIER-1 BREAKTHROUGHS",
        "The Engineering:",
        "The Business:",
        "SECTION 2 — SECTOR PILLARS",
        "SECTION 3 — THE CODE & PAPER DROP",
        "/auth/unsubscribe?token="
    ]

    for sec in required_sections:
        assert sec in html_output, f"Missing required section/marker: '{sec}'"
        print(f"  [OK] Section/Marker Verified: '{sec}'")

    # 4. Test Batch Email Sender Dispatch Runner
    print("\n[TEST 4] Testing send_daily_news_brief() batch runner...")
    dispatch_result = send_daily_news_brief(batch_size=100)
    print(f"-> Dispatch result: {dispatch_result}")
    assert dispatch_result["status"] == "success", "Dispatch runner did not report success!"
    assert dispatch_result["dispatched_count"] >= 1, "No subscribers were processed in dispatch test!"

    # Verify items marked as dispatched in database
    dispatched_items = db_client.fetchall("SELECT COUNT(*) as count FROM ai_intelligence_items WHERE is_dispatched = 1;")
    print(f"-> Verified items marked is_dispatched=1: {dispatched_items[0]['count']} items.")

    print("\n==================================================")
    print("ALL PHASE 5 CHECKS PASSED SUCCESSFULLY WITH ZERO ERRORS!")
    print("==================================================")

if __name__ == "__main__":
    test_phase5()

