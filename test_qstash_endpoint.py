import sys
import os
import time
import hashlib
import base64
from pathlib import Path
from unittest.mock import patch
import jwt
from fastapi.testclient import TestClient

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

# Configure test QStash keys
TEST_CURRENT_KEY = "sig_test_key_current_0123456789abcdef"
TEST_NEXT_KEY = "sig_test_key_next_0123456789abcdef"
os.environ["QSTASH_CURRENT_SIGNING_KEY"] = TEST_CURRENT_KEY
os.environ["QSTASH_NEXT_SIGNING_KEY"] = TEST_NEXT_KEY
os.environ["QSTASH_TRIGGER_URL"] = "https://api.urivalent.tech/api/trigger-notifications"

from main import app


def generate_qstash_signature(body: str, key: str, url: str = "https://api.urivalent.tech/api/trigger-notifications", expired: bool = False) -> str:
    body_hash = hashlib.sha256(body.encode("utf-8")).digest()
    body_b64 = base64.urlsafe_b64encode(body_hash).decode("utf-8").rstrip("=")
    now = int(time.time())
    payload = {
        "iss": "Upstash",
        "sub": url,
        "exp": (now - 300) if expired else (now + 300),
        "nbf": now - 30,
        "body": body_b64
    }
    return jwt.encode(payload, key, algorithm="HS256")


def test_qstash_verification():
    print("==================================================")
    print("UPSTASH QSTASH CRYPTOGRAPHIC VERIFICATION TEST SUITE")
    print("==================================================")

    # Mock background freshness check and email dispatch to keep unit tests fast and hermetic
    with patch("main.startup_freshness_check"), \
         patch("main.send_daily_news_brief", return_value={"status": "success", "subscribers_count": 5, "dispatched_count": 5}) as mock_send:

        client = TestClient(app, raise_server_exceptions=False)

        # 1. Missing Upstash-Signature header
        print("\n[TEST 1] Missing Upstash-Signature header...")
        resp = client.post("/api/trigger-notifications", content=b"")
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
        assert resp.json().get("detail") == "Missing QStash signature", f"Unexpected detail: {resp.text}"
        print("  [OK] Successfully rejected with 401 (Missing QStash signature)")

        # 2. Tampered / Garbage signature
        print("\n[TEST 2] Invalid / Garbage signature...")
        resp = client.post(
            "/api/trigger-notifications",
            headers={"Upstash-Signature": "invalid_jwt_format"},
            content=b""
        )
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
        assert resp.json().get("detail") == "Invalid execution signature", f"Unexpected detail: {resp.text}"
        print("  [OK] Successfully rejected with 401 (Invalid execution signature)")

        # 3. Signature with wrong signing key
        print("\n[TEST 3] Signature with wrong key...")
        bad_sig = generate_qstash_signature("", key="completely_wrong_key_1234567890123")
        resp = client.post(
            "/api/trigger-notifications",
            headers={"Upstash-Signature": bad_sig},
            content=b""
        )
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
        assert resp.json().get("detail") == "Invalid execution signature", f"Unexpected detail: {resp.text}"
        print("  [OK] Successfully rejected with 401 (Wrong key)")

        # 4. Signature with mismatched URL / Subject
        print("\n[TEST 4] Signature with mismatched destination URL...")
        wrong_url_sig = generate_qstash_signature("", key=TEST_CURRENT_KEY, url="https://malicious-site.com/webhook")
        resp = client.post(
            "/api/trigger-notifications",
            headers={"Upstash-Signature": wrong_url_sig},
            content=b""
        )
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
        assert resp.json().get("detail") == "Invalid execution signature", f"Unexpected detail: {resp.text}"
        print("  [OK] Successfully rejected with 401 (Mismatched URL)")

        # 5. Signature with tampered body
        print("\n[TEST 5] Signature with tampered body...")
        valid_body_sig = generate_qstash_signature("expected_body", key=TEST_CURRENT_KEY)
        resp = client.post(
            "/api/trigger-notifications",
            headers={"Upstash-Signature": valid_body_sig},
            content=b"tampered_body_content"
        )
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
        assert resp.json().get("detail") == "Invalid execution signature", f"Unexpected detail: {resp.text}"
        print("  [OK] Successfully rejected with 401 (Body hash mismatch)")

        # 6. Signature that has expired
        print("\n[TEST 6] Expired signature...")
        expired_sig = generate_qstash_signature("", key=TEST_CURRENT_KEY, expired=True)
        resp = client.post(
            "/api/trigger-notifications",
            headers={"Upstash-Signature": expired_sig},
            content=b""
        )
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
        assert resp.json().get("detail") == "Invalid execution signature", f"Unexpected detail: {resp.text}"
        print("  [OK] Successfully rejected with 401 (Expired signature)")

        # 7. Valid signature with Current Signing Key
        print("\n[TEST 7] Valid signature with Current Signing Key...")
        valid_body = '{"batch": "test"}'
        valid_sig = generate_qstash_signature(valid_body, key=TEST_CURRENT_KEY)
        resp = client.post(
            "/api/trigger-notifications",
            headers={"Upstash-Signature": valid_sig},
            content=valid_body.encode("utf-8")
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data.get("status") == "success", f"Status not success: {data}"
        assert data.get("message") == "Notifications dispatched and verified by QStash."
        assert mock_send.called, "Expected send_daily_news_brief to have been called"
        print(f"  [OK] Successfully verified and dispatched: {data['message']}")

        # 8. Valid signature with Next Signing Key (Key Rotation test)
        print("\n[TEST 8] Valid signature with Next Signing Key (Key Rotation)...")
        valid_next_sig = generate_qstash_signature(valid_body, key=TEST_NEXT_KEY)
        resp = client.post(
            "/api/trigger-notifications",
            headers={"Upstash-Signature": valid_next_sig},
            content=valid_body.encode("utf-8")
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data.get("status") == "success", f"Status not success: {data}"
        print(f"  [OK] Key rotation verified with next signing key: {data['message']}")

        # 9. Failure during notification execution -> returns 500 for QStash retry
        print("\n[TEST 9] Failure in dispatch pipeline triggers HTTP 500 for QStash retry...")
        mock_send.side_effect = RuntimeError("Simulated Resend API gateway timeout")
        resp = client.post(
            "/api/trigger-notifications",
            headers={"Upstash-Signature": valid_sig},
            content=valid_body.encode("utf-8")
        )
        assert resp.status_code == 500, f"Expected 500, got {resp.status_code}: {resp.text}"
        assert "Simulated Resend API gateway timeout" in resp.json().get("detail", "")
        print("  [OK] Correctly returned HTTP 500 to trigger QStash exponential backoff retry.")

    print("\n==================================================")
    print("ALL 9 QSTASH SECURITY & PIPELINE CHECKS PASSED PERFECTLY!")
    print("==================================================")


if __name__ == "__main__":
    test_qstash_verification()
