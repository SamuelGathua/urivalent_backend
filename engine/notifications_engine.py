import json
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger("urivalent.engine.notifications")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


def format_fcm_notification_payload(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Format a single Tier-1 breakthrough item into an RFC/FCM-compatible
    push notification payload for mobile and web push clients.
    """
    headline = item.get("headline", "Frontier AI Breakthrough")
    entity = item.get("primary_entity", "AI Ecosystem")
    pillar = item.get("pillar_category", "[MODELS & RESEARCH]")
    score = item.get("signal_score", 8)
    what_happened = item.get("what_happened", "")
    cluster_id = item.get("cluster_id", "")

    # Extract primary source url
    action_url = "https://urivalent.com"
    source_urls_raw = item.get("source_urls", "[]")
    try:
        urls = json.loads(source_urls_raw) if isinstance(source_urls_raw, str) else source_urls_raw
        if urls and isinstance(urls, list) and len(urls) > 0:
            action_url = urls[0].get("link", action_url)
    except Exception:
        pass

    return {
        "notification": {
            "title": f"🚨 {entity} | {pillar} (Score: {score}/10)",
            "body": f"{headline}: {what_happened[:140]}...",
            "image": item.get("image_url")
        },
        "data": {
            "entity": str(entity),
            "pillar": str(pillar),
            "signal_score": str(score),
            "cluster_id": str(cluster_id),
            "click_action": str(action_url),
            "type": "TIER1_BREAKTHROUGH"
        },
        "android": {
            "priority": "high",
            "notification": {
                "sound": "default",
                "channel_id": "urivalent_tier1_alerts"
            }
        },
        "apns": {
            "payload": {
                "aps": {
                    "sound": "default",
                    "badge": 1
                }
            }
        }
    }


def run_market_notifications(synthesized_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Push Notification Engine Runner:
    Filters synthesized intelligence items for Tier-1 breakthroughs (signal_score >= 8).
    Formats and prepares FCM push notifications for real-time mobile/browser alerts.
    """
    logger.info(f"Evaluating {len(synthesized_items)} synthesized intelligence items for Tier-1 push alerts...")
    tier1_items = [it for it in synthesized_items if int(it.get("signal_score", 0)) >= 8]

    notifications: List[Dict[str, Any]] = []
    for item in tier1_items:
        payload = format_fcm_notification_payload(item)
        notifications.append(payload)
        logger.info(
            f"Prepared Tier-1 Push Alert: [{item.get('pillar_category')}] "
            f"Score: {item.get('signal_score')}/10 - '{item.get('headline')[:60]}...'"
        )

    logger.info(f"Generated {len(notifications)} Tier-1 breakthrough push notification payloads.")
    return notifications

