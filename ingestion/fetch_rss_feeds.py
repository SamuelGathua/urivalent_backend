import re
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

import requests
import feedparser
from bs4 import BeautifulSoup

from ingestion.models import make_raw_intel_item

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
    {"name": "Anthropic Research", "url": "https://www.anthropic.com/news/rss.xml", "badge": "LAB"},
    {"name": "Google DeepMind", "url": "https://deepmind.google/blog/rss.xml", "badge": "LAB"},
    {"name": "Meta AI", "url": "https://about.fb.com/news/tag/artificial-intelligence/feed/", "badge": "LAB"},
    {"name": "Mistral AI", "url": "https://mistral.ai/news/index.xml", "badge": "LAB"},
    {"name": "xAI Blog", "url": "https://x.ai/blog/rss.xml", "badge": "LAB"},
]

# 2. Hardware & Deep Tech (Badge: RSS)
HARDWARE_FEEDS = [
    {"name": "SemiAnalysis", "url": "https://www.semianalysis.com/feed", "badge": "RSS"},
    {"name": "Tom's Hardware", "url": "https://www.tomshardware.com/feeds/all", "badge": "RSS"},
    {"name": "AnandTech", "url": "https://www.anandtech.com/rss/", "badge": "RSS"},
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
    {"name": "VentureBeat AI", "url": "https://venturebeat.com/category/ai/feed/", "badge": "RSS"},
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


def parse_feed_safely(feed_info: Dict[str, str], max_items_per_feed: int = 6, timeout: int = 12) -> List[Dict[str, Any]]:
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
        entries = parsed.entries[:max_items_per_feed]

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


def fetch_rss_feeds(max_per_feed: int = 5) -> List[Dict[str, Any]]:
    """
    Stream A Master Runner:
    Polls Official Labs, Hardware, Energy, Substack Curators, and Policy Feeds.
    Returns normalized RawIntelItem dictionaries.
    """
    all_items: List[Dict[str, Any]] = []
    logger.info(f"Initiating Stream A RSS ingestion across {len(ALL_RSS_FEEDS)} targeted feeds...")

    for feed_info in ALL_RSS_FEEDS:
        feed_items = parse_feed_safely(feed_info, max_items_per_feed=max_per_feed)
        all_items.extend(feed_items)

    logger.info(f"Stream A completed. Scraped {len(all_items)} articles across {len(ALL_RSS_FEEDS)} RSS feeds.")
    return all_items


if __name__ == "__main__":
    print("Testing fetch_rss_feeds...")
    results = fetch_rss_feeds(max_per_feed=2)
    print(f"Total items fetched from RSS: {len(results)}")
    for r in results[:5]:
        print(f"[{r['source_badge']}] {r['source']}: {r['title']} ({r['link']})")

