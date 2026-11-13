import os
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any

import requests
from ingestion.models import make_raw_intel_item

logger = logging.getLogger("urivalent.ingestion.news_apis")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# Macro, Policy, and Private Funding targeted queries
TARGET_QUERIES = [
    "AI regulation",
    "Series B AI",
    "Department of Commerce chips"
]


def fetch_gnews(api_key: str, max_items: int = 100) -> List[Dict[str, Any]]:
    """Poll GNews.io for targeted AI policy & funding queries."""
    if not api_key:
        return []
    items: List[Dict[str, Any]] = []
    for q in TARGET_QUERIES:
        try:
            url = f"https://gnews.io/api/v4/search?q={q}&lang=en&max={max_items}&token={api_key}"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                for art in data.get("articles", []):
                    items.append(make_raw_intel_item(
                        title=art.get("title", ""),
                        link=art.get("url", ""),
                        summary=art.get("description", ""),
                        source=f"GNews / {art.get('source', {}).get('name', 'Media')}",
                        source_badge="NEWS",
                        published_at=art.get("publishedAt"),
                        image_url=art.get("image")
                    ))
        except Exception as e:
            logger.error(f"GNews query failed ({q}): {e}")
    return items


def fetch_newsdata(api_key: str, max_items: int = 100) -> List[Dict[str, Any]]:
    """Poll NewsData.io for targeted queries."""
    if not api_key:
        return []
    items: List[Dict[str, Any]] = []
    for q in TARGET_QUERIES:
        try:
            url = f"https://newsdata.io/api/1/news?apikey={api_key}&q={q}&language=en"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                for art in data.get("results", []):
                    items.append(make_raw_intel_item(
                        title=art.get("title", ""),
                        link=art.get("link", ""),
                        summary=art.get("description", ""),
                        source=f"NewsData / {art.get('source_id', 'Media')}",
                        source_badge="NEWS",
                        published_at=art.get("pubDate"),
                        image_url=art.get("image_url")
                    ))
        except Exception as e:
            logger.error(f"NewsData query failed ({q}): {e}")
    return items


def fetch_thenewsapi(api_key: str, max_items: int = 100) -> List[Dict[str, Any]]:
    """Poll TheNewsAPI.com for targeted queries."""
    if not api_key:
        return []
    items: List[Dict[str, Any]] = []
    try:
        url = f"https://api.thenewsapi.com/v1/news/all?api_token={api_key}&search=AI+regulation&language=en&limit={max_items}"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            for art in data.get("data", []):
                items.append(make_raw_intel_item(
                    title=art.get("title", ""),
                    link=art.get("url", ""),
                    summary=art.get("description", "") or art.get("snippet", ""),
                    source=f"TheNewsAPI / {art.get('source', 'Media')}",
                    source_badge="NEWS",
                    published_at=art.get("published_at"),
                    image_url=art.get("image_url")
                ))
    except Exception as e:
        logger.error(f"TheNewsAPI query failed: {e}")
    return items


def fetch_worldnewsapi(api_key: str, max_items: int = 100) -> List[Dict[str, Any]]:
    """Poll WorldNewsAPI for targeted queries."""
    if not api_key:
        return []
    items: List[Dict[str, Any]] = []
    try:
        url = f"https://api.worldnewsapi.com/search-news?api-key={api_key}&text=semiconductor+export+control&language=en&number={max_items}"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            for art in data.get("news", []):
                items.append(make_raw_intel_item(
                    title=art.get("title", ""),
                    link=art.get("url", ""),
                    summary=art.get("summary", "") or art.get("text", "")[:300],
                    source="WorldNewsAPI",
                    source_badge="NEWS",
                    published_at=art.get("publish_date"),
                    image_url=art.get("image")
                ))
    except Exception as e:
        logger.error(f"WorldNewsAPI query failed: {e}")
    return items


def fetch_mediastack(api_key: str, max_items: int = 100) -> List[Dict[str, Any]]:
    """Poll Mediastack for targeted queries."""
    if not api_key:
        return []
    items: List[Dict[str, Any]] = []
    try:
        url = f"http://api.mediastack.com/v1/news?access_key={api_key}&keywords=artificial+intelligence+regulation&languages=en&limit={max_items}"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            for art in data.get("data", []):
                items.append(make_raw_intel_item(
                    title=art.get("title", ""),
                    link=art.get("url", ""),
                    summary=art.get("description", ""),
                    source=f"Mediastack / {art.get('source', 'Media')}",
                    source_badge="NEWS",
                    published_at=art.get("published_at"),
                    image_url=art.get("image")
                ))
    except Exception as e:
        logger.error(f"Mediastack query failed: {e}")
    return items


def fetch_newsapi_org(api_key: str, max_items: int = 100) -> List[Dict[str, Any]]:
    """Poll NewsAPI.org for targeted queries."""
    if not api_key:
        return []
    items: List[Dict[str, Any]] = []
    try:
        url = f"https://newsapi.org/v2/everything?q=AI+regulation+OR+chips+act&language=en&pageSize={max_items}&apiKey={api_key}"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            for art in data.get("articles", []):
                items.append(make_raw_intel_item(
                    title=art.get("title", ""),
                    link=art.get("url", ""),
                    summary=art.get("description", ""),
                    source=f"NewsAPI / {art.get('source', {}).get('name', 'Media')}",
                    source_badge="NEWS",
                    published_at=art.get("publishedAt"),
                    image_url=art.get("urlToImage")
                ))
    except Exception as e:
        logger.error(f"NewsAPI.org query failed: {e}")
    return items


def fetch_news_apis() -> List[Dict[str, Any]]:
    """
    Stream C Master Runner:
    Checks for configured News API keys in environment and queries them for macro/policy news.
    Gracefully skips any unconfigured API with zero errors.
    """
    all_news: List[Dict[str, Any]] = []

    api_handlers = [
        ("GNews", os.getenv("GNEWS_API_KEY", "").strip(), fetch_gnews),
        ("NewsData", os.getenv("NEWSDATA_API_KEY", "").strip(), fetch_newsdata),
        ("TheNewsAPI", os.getenv("THENEWSAPI_KEY", "").strip(), fetch_thenewsapi),
        ("WorldNewsAPI", os.getenv("WORLDNEWS_API_KEY", "").strip(), fetch_worldnewsapi),
        ("Mediastack", os.getenv("MEDIASTACK_API_KEY", "").strip(), fetch_mediastack),
        ("NewsAPI.org", os.getenv("NEWSAPI_ORG_KEY", "").strip(), fetch_newsapi_org),
    ]

    for name, key, func in api_handlers:
        if not key:
            logger.info(f"Skipping {name}: API key not configured in environment.")
            continue
        logger.info(f"Querying configured News API: {name}...")
        results = func(key)
        all_news.extend(results)

    logger.info(f"Stream C completed. Retrieved {len(all_news)} articles from configured News APIs.")
    return all_news


if __name__ == "__main__":
    print("Testing fetch_news_apis...")
    news = fetch_news_apis()
    print(f"Retrieved {len(news)} items from news APIs.")

