import re
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

import requests
from bs4 import BeautifulSoup

from ingestion.models import make_raw_intel_item

logger = logging.getLogger("urivalent.ingestion.labs")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

DEFAULT_SCRAPER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def scrape_anthropic_news(max_items: int = 5, timeout: int = 10) -> List[Dict[str, Any]]:
    """
    Direct HTML scraper fallback for Anthropic Newsroom (https://www.anthropic.com/news)
    and Engineering blog (https://www.anthropic.com/engineering).
    Used when RSSHub mirrors or third-party feeds are offline or missing.
    """
    items: List[Dict[str, Any]] = []
    target_urls = [
        "https://www.anthropic.com/news",
        "https://www.anthropic.com/engineering"
    ]
    seen_links = set()

    for target in target_urls:
        try:
            resp = requests.get(target, headers=DEFAULT_SCRAPER_HEADERS, timeout=timeout)
            if resp.status_code != 200:
                logger.warning(f"Anthropic scrape returned HTTP {resp.status_code} for {target}")
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                if ("/news/" in href or "/engineering/" in href) and href not in ("/news/", "/engineering/", "/news/announcements"):
                    full_url = href if href.startswith("http") else f"https://www.anthropic.com{href}"
                    if full_url in seen_links:
                        continue

                    raw_text = a.get_text(separator=" ", strip=True)
                    # Clean leading category markers like 'Announcements', 'Research', etc.
                    clean_title = re.sub(r"^(Announcements|Research|Company|Product|Engineering)\s*", "", raw_text).strip()
                    if len(clean_title) < 15:
                        continue

                    # If title contains body text snippet, split headline
                    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", clean_title) if s.strip()]
                    headline = sentences[0] if sentences else clean_title[:120]
                    summary = " ".join(sentences[1:]) if len(sentences) > 1 else f"Anthropic research disclosure: {headline}"

                    seen_links.add(full_url)
                    items.append(make_raw_intel_item(
                        title=headline[:140],
                        link=full_url,
                        summary=summary[:400],
                        source="Anthropic Research",
                        source_badge="LAB",
                        published_at=datetime.now(timezone.utc).isoformat()
                    ))

                    if len(items) >= max_items:
                        break
        except Exception as e:
            logger.error(f"Error scraping Anthropic page {target}: {e}")

    logger.info(f"Anthropic HTML scraper retrieved {len(items)} frontier items.")
    return items


def scrape_meta_ai_blog(max_items: int = 5, timeout: int = 10) -> List[Dict[str, Any]]:
    """
    Direct HTML scraper fallback for Meta AI Blog (https://ai.meta.com/blog/).
    Extracts frontier research, model weights, and compute announcements.
    """
    items: List[Dict[str, Any]] = []
    seen_links = set()
    url = "https://ai.meta.com/blog/"

    # Headers tuned for Meta CDN
    headers = {
        "User-Agent": "Urivalent/1.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }

    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        if resp.status_code != 200:
            logger.warning(f"Meta AI scrape returned HTTP {resp.status_code}")
            return items

        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if "/blog/" in href and href != "/blog/" and not href.endswith("/blog/"):
                full_url = href if href.startswith("http") else f"https://ai.meta.com{href}"
                if full_url in seen_links:
                    continue

                raw_text = a.get_text(separator=" ", strip=True)
                clean_title = re.sub(r"^(FEATURED|Learn More)\s*", "", raw_text).strip()
                if len(clean_title) < 15 or clean_title in ("Learn More", "Next", "Previous"):
                    continue

                seen_links.add(full_url)
                items.append(make_raw_intel_item(
                    title=clean_title[:140],
                    link=full_url,
                    summary=f"Meta AI research disclosure: {clean_title}",
                    source="Meta AI",
                    source_badge="LAB",
                    published_at=datetime.now(timezone.utc).isoformat()
                ))

                if len(items) >= max_items:
                    break
    except Exception as e:
        logger.error(f"Error scraping Meta AI Blog: {e}")

    logger.info(f"Meta AI HTML scraper retrieved {len(items)} frontier items.")
    return items


def scrape_mistral_news(max_items: int = 5, timeout: int = 10) -> List[Dict[str, Any]]:
    """
    Direct HTML scraper fallback for Mistral AI News (https://mistral.ai/news/).
    Used if their native RSS endpoint (https://mistral.ai/news/rss) is ever unreachable.
    """
    items: List[Dict[str, Any]] = []
    seen_links = set()
    url = "https://mistral.ai/news/"

    try:
        resp = requests.get(url, headers=DEFAULT_SCRAPER_HEADERS, timeout=timeout)
        if resp.status_code != 200:
            logger.warning(f"Mistral AI scrape returned HTTP {resp.status_code}")
            return items

        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if "/news/" in href and href != "/news/" and not href.endswith("/news/rss"):
                full_url = href if href.startswith("http") else f"https://mistral.ai{href}"
                if full_url in seen_links:
                    continue

                text = a.get_text(separator=" ", strip=True)
                clean_title = re.sub(r"^(Product|Research|Company|Blog)\s*", "", text).strip()
                if len(clean_title) < 15:
                    continue

                seen_links.add(full_url)
                items.append(make_raw_intel_item(
                    title=clean_title[:140],
                    link=full_url,
                    summary=f"Mistral AI model and research update: {clean_title}",
                    source="Mistral AI",
                    source_badge="LAB",
                    published_at=datetime.now(timezone.utc).isoformat()
                ))

                if len(items) >= max_items:
                    break
    except Exception as e:
        logger.error(f"Error scraping Mistral AI News: {e}")

    logger.info(f"Mistral AI HTML scraper retrieved {len(items)} frontier items.")
    return items


def scrape_frontier_labs_fallback(
    need_anthropic: bool = False,
    need_meta: bool = True,
    need_mistral: bool = False
) -> List[Dict[str, Any]]:
    """
    Master runner for frontier labs without native RSS feeds or whose feeds failed.
    Always includes Meta AI (no RSS), and dynamically includes Anthropic / Mistral if needed.
    """
    fallback_items: List[Dict[str, Any]] = []

    # Meta AI (Native RSS does not exist)
    if need_meta:
        fallback_items.extend(scrape_meta_ai_blog(max_items=5))

    # Anthropic (If RSSHub mirror is offline or failed)
    if need_anthropic:
        fallback_items.extend(scrape_anthropic_news(max_items=5))

    # Mistral AI (If /news/rss is offline or failed)
    if need_mistral:
        fallback_items.extend(scrape_mistral_news(max_items=5))

    return fallback_items
