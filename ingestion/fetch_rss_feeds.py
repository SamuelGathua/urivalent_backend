import re
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

import requests
import feedparser
from bs4 import BeautifulSoup

from ingestion.models import make_raw_intel_item
from ingestion.fetch_lab_scrapers import (
    scrape_meta_ai_blog,
    scrape_anthropic_news,
    scrape_mistral_news
)

logger = logging.getLogger("urivalent.ingestion.rss")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Urivalent/1.0"
)

# Ingestion Streams Configuration
# 1. Official Labs (Badge: LAB)
OFFICIAL_LAB_FEEDS = [
    {"name": "OpenAI News", "url": "https://openai.com/news/rss.xml", "badge": "LAB"},
    {"name": "Google DeepMind", "url": "https://deepmind.google/blog/rss.xml", "badge": "LAB"},
    {"name": "Mistral AI", "url": "https://mistral.ai/news/rss", "badge": "LAB"},
    {"name": "Anthropic News", "url": "https://rsshub.bestblogs.dev/anthropic/news", "badge": "LAB"},
    {"name": "Hugging Face Blog", "url": "https://huggingface.co/blog/feed.xml", "badge": "LAB"},
    {"name": "BAIR Berkeley AI", "url": "https://bair.berkeley.edu/blog/feed.xml", "badge": "LAB"},
]

# 2. Hardware & Deep Tech (Badge: RSS)
HARDWARE_FEEDS = [
    {"name": "SemiAnalysis", "url": "https://www.semianalysis.com/feed", "badge": "RSS"},
    {"name": "Tom's Hardware", "url": "https://www.tomshardware.com/feeds/all", "badge": "RSS"},
    {"name": "Ars Technica Tech Lab", "url": "https://feeds.arstechnica.com/arstechnica/technology-lab", "badge": "RSS"},
    {"name": "IEEE Spectrum Computing", "url": "https://spectrum.ieee.org/feeds/topic/computing.rss", "badge": "RSS"},
]

# 3. Energy & Data Centers (Badge: RSS)
ENERGY_FEEDS = [
    {"name": "Data Center Dynamics", "url": "https://www.datacenterdynamics.com/en/rss/", "badge": "RSS"},
    {"name": "Utility Dive", "url": "https://www.utilitydive.com/feeds/news/", "badge": "RSS"},
]

# 4. Substack Curators (Badge: SUBSTACK)
SUBSTACK_FEEDS = [
    {"name": "Import AI (Jack Clark)", "url": "https://importai.substack.com/feed", "badge": "SUBSTACK"},
    {"name": "Ahead of AI (Sebastian Raschka)", "url": "https://magazine.sebastianraschka.com/feed", "badge": "SUBSTACK"},
    {"name": "Latent Space", "url": "https://www.latent.space/feed", "badge": "SUBSTACK"},
    {"name": "ChinAI", "url": "https://chinai.substack.com/feed", "badge": "SUBSTACK"},
]

# 5. Regulatory, Policy & VC Feeds (Badge: POLICY / RSS)
POLICY_FEEDS = [
    {"name": "EU Digital Strategy", "url": "https://digital-strategy.ec.europa.eu/en/news/rss.xml", "badge": "POLICY"},
    {"name": "Federal Register AI Notices", "url": "https://www.federalregister.gov/api/v1/documents.rss?conditions%5Bterm%5D=artificial+intelligence", "badge": "POLICY"},
    {"name": "UK Gov AI Safety", "url": "https://www.gov.uk/government/organisations/ai-safety-institute.atom", "badge": "POLICY"},
    {"name": "TechCrunch AI", "url": "https://techcrunch.com/category/artificial-intelligence/feed/", "badge": "RSS"},
]

ALL_RSS_FEEDS = (
    OFFICIAL_LAB_FEEDS +
    HARDWARE_FEEDS +
    ENERGY_FEEDS +
    SUBSTACK_FEEDS +
    POLICY_FEEDS
)


def clean_html_snippet(raw_html: str, max_chars: int = 400) -> str:
    """Clean HTML tags and return a normalized plaintext snippet."""
    if not raw_html:
        return ""
    text = BeautifulSoup(raw_html, "html.parser").get_text(separator=" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    return text[:max_chars].strip()


def parse_feed_safely(feed_info: Dict[str, str], max_items_per_feed: Optional[int] = None, timeout: int = 12) -> List[Dict[str, Any]]:
    """Fetch an RSS/Atom feed using custom User-Agent and timeouts, returning RawIntelItems."""
    feed_name = feed_info["name"]
    feed_url = feed_info["url"]
    badge = feed_info.get("badge", "RSS")

    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.8"
    }

    items: List[Dict[str, Any]] = []

    try:
        resp = requests.get(feed_url, headers=headers, timeout=timeout)
        if resp.status_code != 200:
            logger.warning(f"Feed '{feed_name}' returned HTTP {resp.status_code} ({feed_url})")
            return items

        parsed = feedparser.parse(resp.content)
        entries = parsed.entries if max_items_per_feed is None else parsed.entries[:max_items_per_feed]

        for entry in entries:
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            if not title or not link:
                continue

            # Summary handling (feedparser uses summary, description, or content)
            raw_summary = entry.get("summary") or entry.get("description") or ""
            if not raw_summary and "content" in entry and entry.content:
                raw_summary = entry.content[0].get("value", "")

            clean_summary = clean_html_snippet(raw_summary, max_chars=450)
            if not clean_summary:
                clean_summary = f"Published update from {feed_name}: {title}"

            published_at = entry.get("published") or entry.get("updated") or datetime.now(timezone.utc).isoformat()

            # Image extraction (media_content, enclosure, or og tag if present)
            image_url = None
            if "media_content" in entry and entry.media_content:
                image_url = entry.media_content[0].get("url")
            elif "enclosures" in entry and entry.enclosures:
                for enc in entry.enclosures:
                    if enc.get("type", "").startswith("image/"):
                        image_url = enc.get("href")
                        break

            intel_item = make_raw_intel_item(
                title=title,
                link=link,
                summary=clean_summary,
                source=feed_name,
                source_badge=badge,
                published_at=published_at,
                image_url=image_url
            )
            items.append(intel_item)

    except requests.exceptions.Timeout:
        logger.warning(f"Feed '{feed_name}' timed out after {timeout}s.")
    except Exception as e:
        logger.error(f"Error parsing feed '{feed_name}' ({feed_url}): {e}")

    return items


def fetch_rss_feeds(max_per_feed: Optional[int] = 10) -> List[Dict[str, Any]]:
    """
    Stream A Master Runner:
    Polls Official Labs, Hardware, Energy, Substack Curators, and Policy Feeds.
    Defaults to 10 newest items per feed to prevent ingestion floods and quota exhaustion.
    Includes automated HTML scraper fallbacks for frontier labs lacking native RSS support (Meta AI, Anthropic, Mistral).
    """
    all_items: List[Dict[str, Any]] = []
    logger.info(f"Initiating Stream A RSS ingestion across {len(ALL_RSS_FEEDS)} targeted feeds...")

    anthropic_found = False
    mistral_found = False

    for feed_info in ALL_RSS_FEEDS:
        feed_items = parse_feed_safely(feed_info, max_items_per_feed=max_per_feed)
        if feed_items:
            if "Anthropic" in feed_info.get("name", ""):
                anthropic_found = True
            elif "Mistral" in feed_info.get("name", ""):
                mistral_found = True
            all_items.extend(feed_items)

    # Frontier Labs Fallback Layer:
    # 1. Meta AI has no native RSS feed -> Direct HTML Blog Scraper
    try:
        meta_items = scrape_meta_ai_blog(max_items=5)
        all_items.extend(meta_items)
    except Exception as e:
        logger.warning(f"Meta AI fallback scraper failed: {e}")

    # 2. Anthropic fallback if RSSHub mirror fails or yields 0 items
    if not anthropic_found:
        try:
            logger.info("Anthropic RSS mirror yielded 0 items; triggering direct HTML fallback scraper...")
            anthropic_items = scrape_anthropic_news(max_items=5)
            all_items.extend(anthropic_items)
        except Exception as e:
            logger.warning(f"Anthropic fallback scraper failed: {e}")

    # 3. Mistral fallback if native RSS fails or yields 0 items
    if not mistral_found:
        try:
            logger.info("Mistral RSS yielded 0 items; triggering direct HTML fallback scraper...")
            mistral_items = scrape_mistral_news(max_items=5)
            all_items.extend(mistral_items)
        except Exception as e:
            logger.warning(f"Mistral fallback scraper failed: {e}")

    logger.info(f"Stream A completed. Gathered {len(all_items)} articles across RSS feeds and frontier lab scrapers.")
    return all_items


if __name__ == "__main__":
    print("Testing fetch_rss_feeds...")
    results = fetch_rss_feeds(max_per_feed=2)
    print(f"Total items fetched from RSS: {len(results)}")
    for r in results[:5]:
        print(f"[{r['source_badge']}] {r['source']}: {r['title']} ({r['link']})")

