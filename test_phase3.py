import sys
import os
from pathlib import Path

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(backend_dir))

from ingestion.fetch_rss_feeds import fetch_rss_feeds
from ingestion.fetch_social_bridge import fetch_social_bridge, scrape_og_metadata
from ingestion.fetch_news_apis import fetch_news_apis
from ingestion.fetch_all import fetch_from_sources

def test_phase3():
    print("==================================================")
    print("URIVALENT PHASE 3: MULTI-CHANNEL INGESTION TEST")
    print("==================================================")

    required_keys = {"title", "link", "summary", "source", "source_badge", "published_at", "image_url"}

    # 1. Test Stream A: Direct RSS & Atom Feeds
    print("\n[TEST 1] Executing Stream A (fetch_rss_feeds)...")
    rss_items = fetch_rss_feeds(max_per_feed=2)
    print(f"-> Stream A returned {len(rss_items)} items.")
    assert len(rss_items) > 0, "Stream A returned zero RSS items!"

    rss_badges = set()
    for it in rss_items:
        assert required_keys.issubset(it.keys()), f"Item missing required keys: {required_keys - it.keys()}"
        assert it["title"], "RSS item title is empty!"
        assert it["link"], "RSS item link is empty!"
        rss_badges.add(it["source_badge"])

    print(f"  [OK] Sample RSS Item: [{rss_items[0]['source_badge']}] {rss_items[0]['source']}: {rss_items[0]['title'][:70]}...")
    print(f"  [OK] Stream A Badges Captured: {sorted(rss_badges)}")

    # 2. Test Stream B: Social & Community Aggregators
    print("\n[TEST 2] Executing Stream B (fetch_social_bridge)...")
    social_items = fetch_social_bridge(scrape_telegram=True, max_hn=5)
    print(f"-> Stream B returned {len(social_items)} items.")
    assert len(social_items) > 0, "Stream B returned zero social items!"

    social_badges = set()
    for it in social_items:
        assert required_keys.issubset(it.keys()), f"Item missing required keys: {required_keys - it.keys()}"
        assert it["title"], "Social item title is empty!"
        assert it["link"], "Social item link is empty!"
        social_badges.add(it["source_badge"])

    print(f"  [OK] Sample Social Item: [{social_items[0]['source_badge']}] {social_items[0]['source']}: {social_items[0]['title'][:70]}...")
    print(f"  [OK] Stream B Badges Captured: {sorted(social_badges)}")

    # 3. Test OpenGraph Metadata Scraper
    print("\n[TEST 3] Testing scrape_og_metadata utility...")
    og_meta = scrape_og_metadata("https://news.ycombinator.com")
    assert isinstance(og_meta, dict), "OG metadata is not a dictionary"
    print(f"  [OK] scrape_og_metadata returned: {og_meta}")

    # 4. Test Stream C: Targeted News APIs (Graceful unconfigured handling)
    print("\n[TEST 4] Testing Stream C (fetch_news_apis with empty keys)...")
    news_items = fetch_news_apis()
    assert isinstance(news_items, list), "fetch_news_apis did not return a list"
    print(f"  [OK] Gracefully handled unconfigured keys without throwing errors. Returned {len(news_items)} items.")

    # 5. Test Unified Ingestion Matrix (fetch_from_sources)
    print("\n[TEST 5] Testing unified fetch_from_sources()...")
    combined_items = fetch_from_sources(
        include_market=False,
        include_research=False,
        include_rss=True,
        include_social=True,
        include_news_apis=True
    )
    print(f"-> Unified matrix returned {len(combined_items)} items.")
    assert len(combined_items) >= (len(rss_items) + len(social_items) + len(news_items)), "Combined items mismatch!"

    print("\n==================================================")
    print("ALL PHASE 3 CHECKS PASSED SUCCESSFULLY WITH ZERO ERRORS!")
    print("==================================================")

if __name__ == "__main__":
    test_phase3()

