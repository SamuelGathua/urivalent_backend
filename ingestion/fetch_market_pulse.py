import os
import re
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

import yfinance as yf
import requests
import feedparser
from bs4 import BeautifulSoup

from database.db_client import db_client
from ingestion.models import make_raw_intel_item

logger = logging.getLogger("urivalent.ingestion.market")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# 10 Core AI Compute & Macro Tickers
CORE_TICKERS = [
    "NVDA",   # NVIDIA - GPUs / AI Accelerators
    "TSM",    # Taiwan Semiconductor - Foundry / Lithography
    "ASML",   # ASML - EUV Lithography
    "ARM",    # ARM Holdings - Architecture & IP
    "AVGO",   # Broadcom - Custom Silicon & Networking
    "MSFT",   # Microsoft - Hyperscaler & Azure AI
    "GOOGL",  # Alphabet - Hyperscaler & TPU Compute
    "META",   # Meta Platforms - Open Weights & Llama Compute
    "CEG",    # Constellation Energy - Nuclear Power for Data Centers
    "CCJ"     # Cameco - Uranium / Nuclear Fuel
]

# SEC EDGAR Target Keywords for CapEx and Hardware Alerts
SEC_KEYWORDS = [
    "data center",
    "gpu",
    "power purchase agreement",
    "custom silicon",
    "nuclear",
    "capex",
    "wafer",
    "export control"
]

# Mapping Tickers to CIKs for SEC EDGAR tracking
TICKER_TO_CIK = {
    "NVDA": "0001045810",
    "TSM": "0001046179",
    "ASML": "0000937966",
    "ARM": "0001973239",
    "AVGO": "0001730168",
    "MSFT": "0000789019",
    "GOOGL": "0001652044",
    "META": "0001326801",
    "CEG": "0001868275",
    "CCJ": "0001009840"
}


def fetch_public_ai_basket(tickers: Optional[List[str]] = None, persist_to_db: bool = True) -> List[Dict[str, Any]]:
    """
    Part A: Fetches 5-day price/volume history for AI basket tickers using yfinance.
    Calculates: close_price, daily_change_pct, five_day_trend_pct, volume, volume_anomaly.
    Saves snapshots to compute_market_snapshots.
    Emits RawIntelItem alerts (badge: 'MARKET') if daily_change_pct > 4.0% or volume_anomaly == 1.
    """
    if tickers is None:
        tickers = CORE_TICKERS

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    intel_alerts: List[Dict[str, Any]] = []

    logger.info(f"Fetching 5-day market data for {len(tickers)} core tickers...")

    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="5d", interval="1d")

            if hist.empty or len(hist) < 2:
                logger.warning(f"Insufficient history data returned for ticker {ticker}")
                continue

            # Latest day & previous day closes
            latest_row = hist.iloc[-1]
            prev_row = hist.iloc[-2]
            first_row = hist.iloc[0]

            close_price = round(float(latest_row["Close"]), 2)
            prev_close = float(prev_row["Close"])
            daily_change_pct = round(((close_price - prev_close) / prev_close) * 100, 2) if prev_close else 0.0

            first_close = float(first_row["Close"])
            five_day_trend_pct = round(((close_price - first_close) / first_close) * 100, 2) if first_close else 0.0

            volume = int(latest_row["Volume"])

            # 5-day average volume
            avg_volume_5d = float(hist["Volume"].mean())
            volume_anomaly = 1 if avg_volume_5d > 0 and volume > (1.5 * avg_volume_5d) else 0

            # Persist snapshot to database
            if persist_to_db:
                query = """
                INSERT OR REPLACE INTO compute_market_snapshots
                (date, ticker, close_price, daily_change_pct, five_day_trend_pct, volume, volume_anomaly)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """
                db_client.execute(
                    query,
                    (today_str, ticker, close_price, daily_change_pct, five_day_trend_pct, volume, volume_anomaly)
                )

            # High-priority alert condition: abs(daily_change_pct) >= 4.0 or volume anomaly
            is_large_move = abs(daily_change_pct) >= 4.0
            if is_large_move or volume_anomaly == 1:
                direction_str = "surges" if daily_change_pct > 0 else "drops"
                reasons = []
                if is_large_move:
                    reasons.append(f"{daily_change_pct:+.2f}% 24h swing")
                if volume_anomaly == 1:
                    vol_ratio = round(volume / avg_volume_5d, 1) if avg_volume_5d > 0 else 0
                    reasons.append(f"volume spike ({vol_ratio}x 5d avg)")

                headline = f"COMPUTE MARKET ALERT: {ticker} {direction_str} {abs(daily_change_pct):.2f}% to ${close_price:.2f} ({', '.join(reasons)})"
                summary = (
                    f"{ticker} recorded a closing price of ${close_price:.2f} with a 24-hour move of {daily_change_pct:+.2f}% "
                    f"and a 5-day trend of {five_day_trend_pct:+.2f}%. Trading volume hit {volume:,} shares "
                    f"({'volume anomaly detected' if volume_anomaly else 'normal volume'})."
                )

                intel_item = make_raw_intel_item(
                    title=headline,
                    link=f"https://finance.yahoo.com/quote/{ticker}",
                    summary=summary,
                    source=f"yfinance / {ticker}",
                    source_badge="MARKET",
                    published_at=datetime.now(timezone.utc).isoformat()
                )
                intel_alerts.append(intel_item)
                logger.info(f"Triggered market alert for {ticker}: {headline}")

        except Exception as e:
            logger.error(f"Error processing ticker {ticker} in yfinance: {e}")

    logger.info(f"Completed market basket watch. Stored snapshots, emitted {len(intel_alerts)} high-priority market alerts.")
    return intel_alerts


def fetch_sec_edgar_filings(tickers: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """
    Part B: Polls SEC EDGAR feeds for 8-K, 10-Q, and 10-K filings for the core tickers.
    Filters for CapEx/Compute keywords: data center, GPU, power purchase agreement, custom silicon, nuclear, capex, wafer, export control.
    Emits RawIntelItem objects (badge: 'SEC').
    """
    if tickers is None:
        tickers = CORE_TICKERS

    sec_user_agent = os.getenv("SEC_USER_AGENT", "UrivalentResearch/1.0 (contact@urivalent.com)")
    headers = {
        "User-Agent": sec_user_agent,
        "Accept-Encoding": "gzip, deflate",
        "Host": "www.sec.gov"
    }

    intel_items: List[Dict[str, Any]] = []

    # Target SEC EDGAR 8-K, 10-Q, 10-K recent feeds
    edgar_urls = [
        "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&CIK=&type=8-K&company=&dateb=&owner=include&start=0&count=40&output=atom",
        "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&CIK=&type=10-Q&company=&dateb=&owner=include&start=0&count=20&output=atom",
        "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&CIK=&type=10-K&company=&dateb=&owner=include&start=0&count=20&output=atom",
    ]

    target_ciks = set(TICKER_TO_CIK.values())
    cik_to_ticker = {v: k for k, v in TICKER_TO_CIK.items()}

    logger.info(f"Polling SEC EDGAR Atom feeds with User-Agent: {sec_user_agent}...")

    for feed_url in edgar_urls:
        try:
            req_headers = {"User-Agent": sec_user_agent}
            resp = requests.get(feed_url, headers=req_headers, timeout=15)
            if resp.status_code != 200:
                logger.warning(f"SEC feed returned HTTP {resp.status_code}: {feed_url}")
                continue

            feed = feedparser.parse(resp.content)
            for entry in feed.entries:
                title = entry.get("title", "")
                summary_raw = entry.get("summary", "")
                clean_summary = BeautifulSoup(summary_raw, "html.parser").get_text(strip=True) if summary_raw else ""
                link = entry.get("link", "")
                published = entry.get("updated", entry.get("published", datetime.now(timezone.utc).isoformat()))

                # Check if this filing belongs to any of our target tickers (using word boundaries)
                matched_ticker = None
                for ticker in tickers:
                    cik = TICKER_TO_CIK.get(ticker)
                    ticker_pattern = rf"\b{re.escape(ticker)}\b"
                    if re.search(ticker_pattern, title, re.IGNORECASE) or (cik and cik in title) or (cik and cik in summary_raw):
                        matched_ticker = ticker
                        break

                content_to_check = f"{title} {clean_summary}".lower()
                matched_keywords = [kw for kw in SEC_KEYWORDS if kw in content_to_check]

                # If ticker matched OR contains critical AI/compute keywords from major filings
                if matched_ticker or matched_keywords:
                    badge_ticker = matched_ticker or "MACRO"
                    kw_note = f" [Keywords: {', '.join(matched_keywords)}]" if matched_keywords else ""
                    headline = f"SEC FILING ({badge_ticker}): {title}{kw_note}"

                    intel_item = make_raw_intel_item(
                        title=headline,
                        link=link,
                        summary=f"SEC EDGAR Material Filing: {clean_summary[:400]}... Matches: {', '.join(matched_keywords)}",
                        source="SEC EDGAR",
                        source_badge="SEC",
                        published_at=published
                    )
                    intel_items.append(intel_item)
                    logger.info(f"Captured relevant SEC filing: {headline}")

        except Exception as e:
            logger.error(f"Error fetching SEC EDGAR feed ({feed_url}): {e}")

    logger.info(f"Completed SEC EDGAR filing check. Captured {len(intel_items)} filings.")
    return intel_items


def fetch_market_pulse(persist_to_db: bool = True) -> List[Dict[str, Any]]:
    """
    Master runner for Phase 2 Market Pulse (Parts A & B).
    Returns normalized RawIntelItem dictionaries.
    """
    alerts = []
    # Part A: AI basket watch
    alerts.extend(fetch_public_ai_basket(persist_to_db=persist_to_db))
    # Part B: SEC filings
    alerts.extend(fetch_sec_edgar_filings())
    return alerts


if __name__ == "__main__":
    print("Testing fetch_market_pulse...")
    items = fetch_market_pulse(persist_to_db=True)
    print(f"Total market pulse items generated: {len(items)}")
    for it in items[:3]:
        print(f"- [{it['source_badge']}] {it['title']}")

