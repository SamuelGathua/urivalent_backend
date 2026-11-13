import os
import re
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple

import requests
import feedparser
from bs4 import BeautifulSoup

from database.db_client import db_client
from ingestion.models import make_raw_intel_item

logger = logging.getLogger("urivalent.ingestion.research")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


def fetch_github_velocity(persist_to_db: bool = True) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Part A: Queries GitHub Search API for trending repos in Python, C++, Rust tagged with
    llm, agent, mcp, cuda, inference, or vllm created or active in the past 7 days.
    Saves top 10 to github_trending_snapshots.
    Emits top 5 as RawIntelItem objects (badge: 'GH').
    Returns (raw_intel_items, snapshot_rows).
    """
    github_pat = os.getenv("GITHUB_PAT", "").strip()
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "Urivalent-Research/1.0"
    }
    if github_pat:
        headers["Authorization"] = f"Bearer {github_pat}"

    seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Target queries: high-velocity AI repos
    queries = [
        f"(llm OR agent OR mcp OR cuda OR inference OR vllm) created:>{seven_days_ago}",
        "(mcp OR vllm OR tensor) stars:>50 pushed:>2026-01-01"
    ]

    repos = []
    seen_repos = set()

    for q in queries:
        try:
            url = f"https://api.github.com/search/repositories?q={q}&sort=stars&order=desc&per_page=100"
            resp = requests.get(url, headers=headers, timeout=12)
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get("items", []):
                    full_name = item.get("full_name", "")
                    if full_name and full_name not in seen_repos:
                        seen_repos.add(full_name)
                        repos.append(item)
            elif resp.status_code in (403, 429):
                logger.warning(f"GitHub API rate limit hit ({resp.status_code}).")
                break
            else:
                logger.warning(f"GitHub API returned HTTP {resp.status_code} for query: {q}")
        except Exception as e:
            logger.error(f"Error querying GitHub API ({q}): {e}")

    # Sort repos by star count descending - keep all scraped repositories without capping
    repos.sort(key=lambda x: x.get("stargazers_count", 0), reverse=True)

    intel_items: List[Dict[str, Any]] = []
    snapshots: List[Dict[str, Any]] = []

    for idx, repo in enumerate(repos):
        repo_name = repo.get("full_name", "")
        repo_url = repo.get("html_url", "")
        language = repo.get("language") or "Python"
        total_stars = int(repo.get("stargazers_count", 0))
        description = repo.get("description") or "Open-source AI tool/framework"

        # Estimate stars gained recently if repo created in last 7 days
        created_at_str = repo.get("created_at", "")
        stars_gained = total_stars
        if created_at_str:
            try:
                created_dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                days_old = max(1, (datetime.now(timezone.utc) - created_dt).days)
                stars_gained = max(5, int(total_stars / days_old))
            except Exception:
                stars_gained = total_stars

        # Persist to database
        if persist_to_db:
            query = """
            INSERT OR REPLACE INTO github_trending_snapshots
            (date, repo_name, repo_url, language, stars_gained, total_stars, description)
            VALUES (?, ?, ?, ?, ?, ?, ?);
            """
            db_client.execute(query, (today_str, repo_name, repo_url, language, stars_gained, total_stars, description))

        snapshots.append({
            "repo_name": repo_name,
            "repo_url": repo_url,
            "language": language,
            "stars_gained": stars_gained,
            "total_stars": total_stars,
            "description": description
        })

        # Emit every repository into the intelligence pipeline
        headline = f"GITHUB VELOCITY: {repo_name} (★ {total_stars:,} stars, +{stars_gained} surge)"
        summary = (
            f"High-velocity repository '{repo_name}' written in {language}. "
            f"Description: {description}. Total stars: {total_stars:,}."
        )
        intel_items.append(make_raw_intel_item(
            title=headline,
            link=repo_url,
            summary=summary,
            source="GitHub",
            source_badge="GH",
            published_at=repo.get("pushed_at", datetime.now(timezone.utc).isoformat())
        ))

    logger.info(f"GitHub Velocity Tracker stored {len(snapshots)} snapshots, emitted {len(intel_items)} RawIntelItems.")
    return intel_items, snapshots


def fetch_huggingface_papers(persist_to_db: bool = True) -> List[Dict[str, Any]]:
    """
    Part B1: Polls Hugging Face Daily Papers API (https://huggingface.co/api/daily_papers)
    and trending models. Stores top papers in research_paper_snapshots.
    Emits top items as RawIntelItem objects (badge: 'HF').
    """
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    intel_items: List[Dict[str, Any]] = []

    hf_token = os.getenv("HUGGINGFACE_TOKEN", "").strip()
    headers = {"User-Agent": "Urivalent-Research/1.0"}
    if hf_token:
        headers["Authorization"] = f"Bearer {hf_token}"

    # 1. Hugging Face Daily Papers API
    daily_papers_url = "https://huggingface.co/api/daily_papers"
    try:
        resp = requests.get(daily_papers_url, headers=headers, timeout=15)
        if resp.status_code == 200:
            papers_data = resp.json()
            if isinstance(papers_data, list):
                # Sort by upvotes descending
                papers_data.sort(key=lambda p: p.get("paper", {}).get("upvotes", 0) if isinstance(p.get("paper"), dict) else p.get("upvotes", 0), reverse=True)
                for item in papers_data:
                    paper_obj = item.get("paper", item)
                    paper_id = paper_obj.get("id") or item.get("id", "")
                    title = paper_obj.get("title", "")
                    summary = paper_obj.get("summary") or paper_obj.get("ai_summary") or ""
                    upvotes = int(paper_obj.get("upvotes", 0))
                    paper_url = f"https://huggingface.co/papers/{paper_id}" if paper_id else item.get("url", "")
                    published_at = paper_obj.get("publishedAt", datetime.now(timezone.utc).isoformat())

                    if persist_to_db and paper_id and title:
                        query = """
                        INSERT OR REPLACE INTO research_paper_snapshots
                        (date, paper_id, title, url, upvotes, summary, source)
                        VALUES (?, ?, ?, ?, ?, ?, ?);
                        """
                        db_client.execute(query, (today_str, f"hf_{paper_id}", title, paper_url, upvotes, summary[:500], "HuggingFace"))

                    if title:
                        headline = f"HUGGING FACE RADAR: {title} (▲ {upvotes} upvotes)"
                        intel_items.append(make_raw_intel_item(
                            title=headline,
                            link=paper_url,
                            summary=summary[:400] if summary else f"Daily paper trending on Hugging Face Hub with {upvotes} community upvotes.",
                            source="HuggingFace Daily Papers",
                            source_badge="HF",
                            published_at=published_at
                        ))
        else:
            logger.warning(f"HuggingFace Daily Papers returned HTTP {resp.status_code}")
    except Exception as e:
        logger.error(f"Error fetching HuggingFace Daily Papers: {e}")

    # 2. Hugging Face Trending Models / Open Weights
    trending_models_url = "https://huggingface.co/api/models?sort=likes7d&direction=-1&limit=50"
    try:
        resp = requests.get(trending_models_url, headers=headers, timeout=12)
        if resp.status_code == 200:
            models_data = resp.json()
            for m in models_data:
                model_id = m.get("id", "")
                likes = m.get("likes", 0)
                downloads = m.get("downloads", 0)
                if model_id:
                    headline = f"OPEN WEIGHTS SURGE: {model_id} (❤️ {likes:,} likes, {downloads:,} downloads)"
                    summary = f"Trending open-weights model '{model_id}' on Hugging Face with {likes:,} likes and {downloads:,} recent downloads."
                    intel_items.append(make_raw_intel_item(
                        title=headline,
                        link=f"https://huggingface.co/{model_id}",
                        summary=summary,
                        source="HuggingFace Hub",
                        source_badge="HF",
                        published_at=datetime.now(timezone.utc).isoformat()
                    ))
    except Exception as e:
        logger.error(f"Error fetching HuggingFace Trending Models: {e}")

    return intel_items


def fetch_arxiv_radar(persist_to_db: bool = True) -> Tuple[List[Dict[str, Any]], int]:
    """
    Part B2: Polls arXiv RSS feeds (cs.AI, cs.LG, cs.CL, cs.AR).
    Calculates total 24h new arXiv paper count.
    Stores top papers in research_paper_snapshots.
    Emits top items as RawIntelItem objects (badge: 'ARXIV').
    Returns (raw_intel_items, total_24h_paper_count).
    """
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    categories = ["cs.AI", "cs.LG", "cs.CL", "cs.AR"]
    intel_items: List[Dict[str, Any]] = []
    total_new_papers_count = 0
    seen_titles = set()

    for cat in categories:
        feed_url = f"http://export.arxiv.org/rss/{cat}"
        try:
            feed = feedparser.parse(feed_url)
            entries = feed.entries
            cat_count = len(entries)
            total_new_papers_count += cat_count
            logger.info(f"arXiv {cat}: found {cat_count} recent papers.")

            for entry in entries:  # Process all entries from arXiv RSS feed
                raw_title = entry.get("title", "")
                # Clean arXiv title format e.g. "Title. (arXiv:2401.xxxx [cs.AI])"
                clean_title = re.sub(r"\(arXiv:[^)]+\)", "", raw_title).strip()
                if not clean_title or clean_title in seen_titles:
                    continue
                seen_titles.add(clean_title)

                link = entry.get("link", "")
                paper_id = link.split("/abs/")[-1] if "/abs/" in link else f"arxiv_{abs(hash(clean_title)) % 1000000}"
                raw_summary = entry.get("summary", "")
                clean_summary = BeautifulSoup(raw_summary, "html.parser").get_text(strip=True) if raw_summary else ""
                published_at = entry.get("published", datetime.now(timezone.utc).isoformat())

                if persist_to_db:
                    query = """
                    INSERT OR REPLACE INTO research_paper_snapshots
                    (date, paper_id, title, url, upvotes, summary, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?);
                    """
                    db_client.execute(query, (today_str, f"arxiv_{paper_id}", clean_title, link, 0, clean_summary[:500], "arXiv"))

                headline = f"ARXIV PREPRINT [{cat}]: {clean_title}"
                intel_items.append(make_raw_intel_item(
                    title=headline,
                    link=link,
                    summary=clean_summary[:400] if clean_summary else f"New research preprint in category {cat} published on arXiv.",
                    source=f"arXiv ({cat})",
                    source_badge="ARXIV",
                    published_at=published_at
                ))

        except Exception as e:
            logger.error(f"Error fetching arXiv feed ({cat}): {e}")

    logger.info(f"arXiv Radar parsed {len(intel_items)} featured preprints. Total new papers counted across feeds: {total_new_papers_count}")
    return intel_items, total_new_papers_count


def fetch_research_pulse(persist_to_db: bool = True) -> Tuple[List[Dict[str, Any]], int]:
    """
    Master runner for Phase 2 Research Pulse (Parts A & B).
    Returns (normalized RawIntelItem list, total_arxiv_24h_count).
    """
    all_intel: List[Dict[str, Any]] = []

    # Part A: GitHub Velocity
    gh_intel, _ = fetch_github_velocity(persist_to_db=persist_to_db)
    all_intel.extend(gh_intel)

    # Part B1: Hugging Face Radar
    hf_intel = fetch_huggingface_papers(persist_to_db=persist_to_db)
    all_intel.extend(hf_intel)

    # Part B2: arXiv Radar
    arxiv_intel, total_arxiv_count = fetch_arxiv_radar(persist_to_db=persist_to_db)
    all_intel.extend(arxiv_intel)

    return all_intel, total_arxiv_count


if __name__ == "__main__":
    print("Testing fetch_research_pulse...")
    items, arxiv_count = fetch_research_pulse(persist_to_db=True)
    print(f"Total research pulse items: {len(items)}, Total arXiv 24h count: {arxiv_count}")
    for it in items[:3]:
        print(f"- [{it['source_badge']}] {it['title']}")

