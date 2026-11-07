import sys
import os
from pathlib import Path

# Set up paths
backend_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(backend_dir))

from database.db_client import db_client
from ingestion.fetch_market_pulse import fetch_market_pulse, CORE_TICKERS
from ingestion.fetch_research_pulse import fetch_research_pulse

def test_phase2():
    print("==================================================")
    print("URIVALENT PHASE 2: VERIFICATION TEST")
    print("==================================================")

    # Ensure clean tables
    db_client.init_db()

    # 1. Test Market Pulse
    print("\n[TEST 1] Executing fetch_market_pulse()...")
    # Test with top subset of tickers for fast verification (e.g. NVDA, TSM, MSFT, CEG)
    market_items = fetch_market_pulse(persist_to_db=True)
    print(f"-> Market pulse returned {len(market_items)} intel alert items.")

    # Verify compute_market_snapshots
    market_snapshots = db_client.fetchall("SELECT * FROM compute_market_snapshots;")
    print(f"-> Database contains {len(market_snapshots)} compute market snapshot rows.")
    assert len(market_snapshots) > 0, "No snapshots saved to compute_market_snapshots!"

    sample_m = market_snapshots[0]
    print(f"  [OK] Sample Market Snapshot: Ticker={sample_m['ticker']}, Date={sample_m['date']}, "
          f"Close=${sample_m['close_price']}, Change={sample_m['daily_change_pct']}%, "
          f"5d Trend={sample_m['five_day_trend_pct']}%, Vol={sample_m['volume']:,}, Anomaly={sample_m['volume_anomaly']}")

    # 2. Test Research Pulse
    print("\n[TEST 2] Executing fetch_research_pulse()...")
    research_items, arxiv_count = fetch_research_pulse(persist_to_db=True)
    print(f"-> Research pulse returned {len(research_items)} intel items.")
    print(f"-> Total 24h new arXiv paper count: {arxiv_count}")

    # Verify github_trending_snapshots
    gh_snapshots = db_client.fetchall("SELECT * FROM github_trending_snapshots;")
    print(f"-> Database contains {len(gh_snapshots)} GitHub trending snapshot rows.")
    assert len(gh_snapshots) > 0, "No snapshots saved to github_trending_snapshots!"
    sample_gh = gh_snapshots[0]
    print(f"  [OK] Sample GitHub Snapshot: Repo={sample_gh['repo_name']}, Lang={sample_gh['language']}, "
          f"Stars={sample_gh['total_stars']:,}, +{sample_gh['stars_gained']} stars surge")

    # Verify research_paper_snapshots
    paper_snapshots = db_client.fetchall("SELECT * FROM research_paper_snapshots;")
    print(f"-> Database contains {len(paper_snapshots)} research paper snapshot rows.")
    assert len(paper_snapshots) > 0, "No snapshots saved to research_paper_snapshots!"
    sample_p = paper_snapshots[0]
    print(f"  [OK] Sample Research Paper Snapshot: ID={sample_p['paper_id']}, Source={sample_p['source']}, "
          f"Title={sample_p['title'][:60]}..., Upvotes={sample_p['upvotes']}")

    # 3. Verify RawIntelItem standard contract
    print("\n[TEST 3] Verifying RawIntelItem data structure contract...")
    all_items = market_items + research_items
    required_keys = {"title", "link", "summary", "source", "source_badge", "published_at", "image_url"}
    badges_found = set()

    for item in all_items:
        assert isinstance(item, dict), "Intel item is not a dictionary!"
        assert required_keys.issubset(item.keys()), f"Intel item missing required keys: {required_keys - item.keys()}"
        assert item["title"], "Intel item has empty title!"
        assert item["link"], "Intel item has empty link!"
        badges_found.add(item["source_badge"])

    print(f"  [OK] Verified {len(all_items)} RawIntelItem records. Badges encountered: {sorted(badges_found)}")

    print("\n==================================================")
    print("ALL PHASE 2 CHECKS PASSED SUCCESSFULLY WITH ZERO ERRORS!")
    print("==================================================")

if __name__ == "__main__":
    test_phase2()

