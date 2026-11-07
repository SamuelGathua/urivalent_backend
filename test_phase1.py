import sys
import os
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(backend_dir))

from database.db_client import DatabaseClient

def test_phase1():
    print("==================================================")
    print("URIVALENT PHASE 1: VERIFICATION TEST")
    print("==================================================")

    # Initialize client with a local test database
    test_db_path = backend_dir / "database" / "test_urivalent.db"
    if test_db_path.exists():
        test_db_path.unlink()

    client = DatabaseClient(use_cloudflare_d1=False, local_sqlite_path=str(test_db_path))
    
    # 1. Initialize DB with schema
    print("\n[TEST 1] Initializing database with schema.sql...")
    client.init_db()
    print("-> Schema initialized.")

    # 2. Check that all 5 tables exist
    expected_tables = {
        "ai_intelligence_items",
        "compute_market_snapshots",
        "github_trending_snapshots",
        "research_paper_snapshots",
        "users"
    }

    tables = client.fetchall("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
    found_tables = {row["name"] for row in tables}
    print(f"\n[TEST 2] Verifying tables. Found: {sorted(found_tables)}")
    
    for tbl in expected_tables:
        assert tbl in found_tables, f"Missing table: {tbl}"
        print(f"  [OK] Table exists: {tbl}")

    # 3. Check seeded test subscriber
    print("\n[TEST 3] Verifying seeded test subscriber...")
    user = client.fetchone("SELECT * FROM users WHERE email = ?", ("test@urivalent.com",))
    assert user is not None, "Test subscriber was not seeded!"
    print(f"  [OK] Seeded user found: id={user['id']}, username={user['username']}, email={user['email']}, active={user['is_active']}, subscribed={user['is_subscribed']}")

    # 4. Check asset logo existence
    logo_path = backend_dir / "assets" / "urivalent_logo.jfif"
    print(f"\n[TEST 4] Verifying asset logo...")
    assert logo_path.exists(), f"Logo missing at {logo_path}"
    print(f"  [OK] Logo found at {logo_path} (size: {logo_path.stat().st_size} bytes)")

    # 5. Clean up test db
    if test_db_path.exists():
        test_db_path.unlink()

    # Also initialize the production default local db path
    print("\n[TEST 5] Initializing primary local db...")
    prod_client = DatabaseClient(use_cloudflare_d1=False)
    prod_client.init_db()
    print(f"  [OK] Primary database initialized at: {prod_client.sqlite_path}")

    print("\n==================================================")
    print("ALL PHASE 1 CHECKS PASSED SUCCESSFULLY WITH ZERO ERRORS!")
    print("==================================================")

if __name__ == "__main__":
    test_phase1()
