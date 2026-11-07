from datetime import datetime, timezone
from typing import Optional, Dict, Any

def make_raw_intel_item(
    title: str,
    link: str,
    summary: str,
    source: str,
    source_badge: str,
    published_at: Optional[str] = None,
    image_url: Optional[str] = None
) -> Dict[str, Any]:
    """
    Produce a normalized RawIntelItem dictionary conforming to the Urivalent standard:
    {"title", "link", "summary", "source", "source_badge", "published_at", "image_url"}
    """
    if not published_at:
        published_at = datetime.now(timezone.utc).isoformat()
    
    return {
        "title": (title or "").strip(),
        "link": (link or "").strip(),
        "summary": (summary or "").strip(),
        "source": (source or "Unknown").strip(),
        "source_badge": (source_badge or "NEWS").strip().upper(),
        "published_at": str(published_at),
        "image_url": image_url.strip() if image_url else None
    }

