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

# Categorized Sovereign & Compute Market Watchlist
TRACKED_STOCKS_CONFIG = [
    # 1. Silicon & Nanotech
    {"category": "Silicon & Nanotech", "name": "NVIDIA", "ticker": "NVDA", "exchange": "NASDAQ", "is_private": False},
    {"category": "Silicon & Nanotech", "name": "ASML Holding", "ticker": "ASML", "exchange": "NASDAQ", "is_private": False},
    {"category": "Silicon & Nanotech", "name": "Taiwan Semiconductor Manufacturing Co.", "ticker": "TSM", "exchange": "NYSE", "is_private": False},
    {"category": "Silicon & Nanotech", "name": "Arm Holdings", "ticker": "ARM", "exchange": "NASDAQ", "is_private": False},

    # 2. Energy & Infra (including Space & Power)
    {"category": "Energy & Infra (including Space & Power)", "name": "Constellation Energy", "ticker": "CEG", "exchange": "NASDAQ", "is_private": False},
    {"category": "Energy & Infra (including Space & Power)", "name": "Vertiv", "ticker": "VRT", "exchange": "NYSE", "is_private": False},
    {"category": "Energy & Infra (including Space & Power)", "name": "SpaceX", "ticker": "SPCX", "exchange": "NASDAQ", "is_private": False, "tradingview_url": "https://www.tradingview.com/chart/?symbol=NASDAQ%3ASPCX"},
    {"category": "Energy & Infra (including Space & Power)", "name": "NextEra Energy", "ticker": "NEE", "exchange": "NYSE", "is_private": False},

    # 3. Models & Research (including Autonomous AI)
    {"category": "Models & Research (including Autonomous AI)", "name": "Alphabet", "ticker": "GOOGL", "exchange": "NASDAQ", "is_private": False},
    {"category": "Models & Research (including Autonomous AI)", "name": "Meta Platforms", "ticker": "META", "exchange": "NASDAQ", "is_private": False},
    {"category": "Models & Research (including Autonomous AI)", "name": "Microsoft", "ticker": "MSFT", "exchange": "NASDAQ", "is_private": False},
    {"category": "Models & Research (including Autonomous AI)", "name": "Tesla", "ticker": "TSLA", "exchange": "NASDAQ", "is_private": False},

    # 4. Bio-Intelligence & Medicine
    {"category": "Bio-Intelligence & Medicine", "name": "Recursion Pharmaceuticals", "ticker": "RXRX", "exchange": "NASDAQ", "is_private": False},
    {"category": "Bio-Intelligence & Medicine", "name": "Tempus AI", "ticker": "TEM", "exchange": "NASDAQ", "is_private": False},
    {"category": "Bio-Intelligence & Medicine", "name": "Schrödinger", "ticker": "SDGR", "exchange": "NASDAQ", "is_private": False},
    {"category": "Bio-Intelligence & Medicine", "name": "Eli Lilly and Company", "ticker": "LLY", "exchange": "NYSE", "is_private": False},

    # 5. Agents & Tooling (including Embodied Robotics)
    {"category": "Agents & Tooling (including Embodied Robotics)", "name": "ServiceNow", "ticker": "NOW", "exchange": "NYSE", "is_private": False},
    {"category": "Agents & Tooling (including Embodied Robotics)", "name": "Cloudflare", "ticker": "NET", "exchange": "NYSE", "is_private": False},
    {"category": "Agents & Tooling (including Embodied Robotics)", "name": "Figure AI", "ticker": "FIGURE", "exchange": "PRIVATE", "is_private": True, "private_note": "$2.6B+ (Private)", "target_url": "https://www.figure.ai"},
    {"category": "Agents & Tooling (including Embodied Robotics)", "name": "UiPath", "ticker": "PATH", "exchange": "NYSE", "is_private": False},

    # 6. Capital & Markets
    {"category": "Capital & Markets", "name": "SoftBank Group", "ticker": "SFTBY", "exchange": "OTC", "is_private": False},
    {"category": "Capital & Markets", "name": "Blackstone", "ticker": "BX", "exchange": "NYSE", "is_private": False},
    {"category": "Capital & Markets", "name": "Coinbase", "ticker": "COIN", "exchange": "NASDAQ", "is_private": False},

    # 7. Policy & Sovereignty (Defense & State Tech)
    {"category": "Policy & Sovereignty (Defense & State Tech)", "name": "Palantir", "ticker": "PLTR", "exchange": "NASDAQ", "is_private": False},
    {"category": "Policy & Sovereignty (Defense & State Tech)", "name": "Booz Allen Hamilton", "ticker": "BAH", "exchange": "NYSE", "is_private": False},
    {"category": "Policy & Sovereignty (Defense & State Tech)", "name": "Anduril Industries", "ticker": "ANDURIL", "exchange": "PRIVATE", "is_private": True, "private_note": "$14B+ (Private)", "target_url": "https://www.anduril.com"},
    {"category": "Policy & Sovereignty (Defense & State Tech)", "name": "Lockheed Martin", "ticker": "LMT", "exchange": "NYSE", "is_private": False}
]

# Tickers that are actively queried via market data APIs
CORE_TICKERS = [s["ticker"] for s in TRACKED_STOCKS_CONFIG if not s.get("is_private")]
TICKER_TO_CONFIG = {s["ticker"]: s for s in TRACKED_STOCKS_CONFIG}

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
    "MSFT": "0000789019",
    "GOOGL": "0001652044",
    "META": "0001326801",
    "CEG": "0001868275",
    "VRT": "0001674101",
    "NEE": "0000753308",
    "TSLA": "0001318605",
    "PLTR": "0001321655",
    "LMT": "0000936468",
    "LLY": "0000059478",
    "NOW": "0001373715",
    "NET": "0001477333",
    "COIN": "0001679788",
    "BX": "0001393818"
}


def fetch_public_ai_basket(tickers: Optional[List[str]] = None, persist_to_db: bool = True) -> List[Dict[str, Any]]:
    """
    Part A: Fetches 5-day price/volume history for categorized AI & compute basket tickers using yfinance.
    Calculates: close_price, change, daily_change_pct, five_day_trend_pct, volume, volume_anomaly.
    Saves snapshots to compute_market_snapshots.
    Emits RawIntelItem alerts (badge: 'MARKET') if daily_change_pct > 4.0% or volume_anomaly == 1.
    """
    if tickers is None:
        tickers = CORE_TICKERS

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    intel_alerts: List[Dict[str, Any]] = []

    logger.info(f"Fetching 5-day market data for {len(tickers)} core tickers...")

    # Batch download to optimize latency and reliability
    try:
        batch_data = yf.download(tickers, period="5d", interval="1d", group_by="ticker", progress=False)
    except Exception as batch_err:
        logger.warning(f"Batch yfinance download failed: {batch_err}. Falling back to individual queries.")
        batch_data = None

    for ticker in tickers:
        try:
            cfg = TICKER_TO_CONFIG.get(ticker, {})
            name = cfg.get("name", ticker)
            category = cfg.get("category", "Compute & Capital")

            hist = None
            if batch_data is not None:
                try:
                    df = batch_data[ticker].dropna()
                    if len(df) >= 2:
                        hist = df
                except Exception:
                    pass

            if hist is None:
                t = yf.Ticker(ticker)
                hist = t.history(period="5d", interval="1d")

            if hist is None or hist.empty or len(hist) < 2:
                logger.warning(f"Insufficient history data returned for ticker {ticker}")
                continue

            # Latest day & previous day closes
            latest_row = hist.iloc[-1]
            prev_row = hist.iloc[-2]
            first_row = hist.iloc[0]

            close_price = round(float(latest_row["Close"]), 2)
            prev_close = float(prev_row["Close"])
            change = round(close_price - prev_close, 2)
            daily_change_pct = round((change / prev_close) * 100, 2) if prev_close else 0.0

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
                (date, ticker, company_name, category, close_price, change, daily_change_pct, five_day_trend_pct, volume, volume_anomaly)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """
                db_client.execute(
                    query,
                    (today_str, ticker, name, category, close_price, change, daily_change_pct, five_day_trend_pct, volume, volume_anomaly)
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

                headline = f"COMPUTE MARKET ALERT: {ticker} ({name}) {direction_str} {abs(daily_change_pct):.2f}% to ${close_price:.2f} ({', '.join(reasons)})"
                summary = (
                    f"{name} ({ticker}) recorded a closing price of ${close_price:.2f} with a 24-hour move of {daily_change_pct:+.2f}% "
                    f"(${change:+.2f}) and a 5-day trend of {five_day_trend_pct:+.2f}%. Trading volume hit {volume:,} shares "
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

    # Also persist private stock entries so today's snapshot table covers all tracked entities
    if persist_to_db:
        private_query = """
        INSERT OR REPLACE INTO compute_market_snapshots
        (date, ticker, company_name, category, close_price, change, daily_change_pct, five_day_trend_pct, volume, volume_anomaly)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        for item in TRACKED_STOCKS_CONFIG:
            if item.get("is_private"):
                try:
                    db_client.execute(
                        private_query,
                        (today_str, item["ticker"], item["name"], item["category"], 0.0, 0.0, 0.0, 0.0, 0, 0)
                    )
                except Exception as p_err:
                    logger.debug(f"Private snapshot insert notice: {p_err}")

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

