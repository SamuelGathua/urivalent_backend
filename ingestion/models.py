import email.utils
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Union


def normalize_datetime_str(val: Optional[Union[str, int, float, datetime]]) -> str:
    """
    Robustly parse and normalize any date format (RFC 2822, ISO 8601, Unix timestamp)
    into a standardized ISO 8601 UTC string (YYYY-MM-DDTHH:MM:SS+00:00).
    """
    if not val:
        return datetime.now(timezone.utc).isoformat()
    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=timezone.utc)
        return val.astimezone(timezone.utc).isoformat()
    if isinstance(val, (int, float)):
        try:
            return datetime.fromtimestamp(val, tz=timezone.utc).isoformat()
        except Exception:
            return datetime.now(timezone.utc).isoformat()

    val_str = str(val).strip()
    # 1. Try ISO 8601 format
    try:
        clean_iso = val_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        pass

    # 2. Try RFC 2822 / 822 format (RSS & Email standard)
    try:
        dt = email.utils.parsedate_to_datetime(val_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        pass

    # 3. Fallback to dateutil parser if available
    try:
        from dateutil import parser
        dt = parser.parse(val_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        pass

    return datetime.now(timezone.utc).isoformat()


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
    Guarantees published_at is strictly in ISO 8601 UTC.
    """
    iso_published_at = normalize_datetime_str(published_at)

    return {
        "title": (title or "").strip(),
        "link": (link or "").strip(),
        "summary": (summary or "").strip(),
        "source": (source or "Unknown").strip(),
        "source_badge": (source_badge or "NEWS").strip().upper(),
        "published_at": iso_published_at,
        "image_url": image_url.strip() if image_url else None
    }

