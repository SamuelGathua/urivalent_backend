import logging
from typing import List, Dict, Any

from ingestion.fetch_market_pulse import fetch_market_pulse
from ingestion.fetch_research_pulse import fetch_research_pulse
from ingestion.fetch_rss_feeds import fetch_rss_feeds
from ingestion.fetch_social_bridge import fetch_social_bridge
from ingestion.fetch_news_apis import fetch_news_apis

logger = logging.getLogger("urivalent.ingestion.fetch_all")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


def fetch_from_sources(
    include_market: bool = True,
    include_research: bool = True,
    include_rss: bool = True,
    include_social: bool = True,
    include_news_apis: bool = True,
    persist_snapshots: bool = True
) -> List[Dict[str, Any]]:
    """
    Unified Ingestion Matrix Orchestrator:
    Combines:
    - Phase 2: Market Pulse (Basket + SEC Filings)
    - Phase 2: Research Pulse (GitHub Velocity + HuggingFace + arXiv)
    - Phase 3 Stream A: Direct RSS & Atom Feeds (Labs, Hardware, Energy, Substack, Policy)
    - Phase 3 Stream B: Social & Community Bridge (Hacker News Algolia, Telegram previews, X researchers)
    - Phase 3 Stream C: Targeted News APIs (GNews, NewsData, Mediastack, etc.)

    Returns a combined list of normalized RawIntelItem dictionaries.
    """
    all_raw_items: List[Dict[str, Any]] = []

    # Stream A: RSS & Atom Feeds
    if include_rss:
        try:
            logger.info("Executing Ingestion Stream A (RSS & Atom Feeds)...")
            rss_items = fetch_rss_feeds(max_per_feed=None)
            all_raw_items.extend(rss_items)
            logger.info(f"Stream A contributed {len(rss_items)} items.")
        except Exception as e:
            logger.error(f"Stream A RSS ingestion error: {e}")

    # Stream B: Social & Community Aggregators
    if include_social:
        try:
            logger.info("Executing Ingestion Stream B (Social & Community Bridge)...")
            social_items = fetch_social_bridge(scrape_telegram=True, max_hn=50)
            all_raw_items.extend(social_items)
            logger.info(f"Stream B contributed {len(social_items)} items.")
        except Exception as e:
            logger.error(f"Stream B Social ingestion error: {e}")

    # Stream C: Targeted News APIs
    if include_news_apis:
        try:
            logger.info("Executing Ingestion Stream C (Targeted News APIs)...")
            news_items = fetch_news_apis()
            all_raw_items.extend(news_items)
            logger.info(f"Stream C contributed {len(news_items)} items.")
        except Exception as e:
            logger.error(f"Stream C News API ingestion error: {e}")

    # Market Pulse
    if include_market:
        try:
            logger.info("Executing Ingestion Market Pulse...")
            market_items = fetch_market_pulse(persist_to_db=persist_snapshots)
            all_raw_items.extend(market_items)
            logger.info(f"Market Pulse contributed {len(market_items)} items.")
        except Exception as e:
            logger.error(f"Market pulse ingestion error: {e}")

    # Research Pulse
    if include_research:
        try:
            logger.info("Executing Ingestion Research Pulse...")
            research_items, _ = fetch_research_pulse(persist_to_db=persist_snapshots)
            all_raw_items.extend(research_items)
            logger.info(f"Research Pulse contributed {len(research_items)} items.")
        except Exception as e:
            logger.error(f"Research pulse ingestion error: {e}")

    logger.info(f"Total RawIntelItems aggregated across all sources: {len(all_raw_items)}")
    return all_raw_items


if __name__ == "__main__":
    items = fetch_from_sources(include_market=False, include_research=False)
    print(f"Aggregated {len(items)} items from external streams.")

