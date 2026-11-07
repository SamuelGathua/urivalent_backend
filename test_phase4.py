import sys
import os
import json
from pathlib import Path

# Set up path
backend_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(backend_dir))

from database.db_client import db_client
from ingestion.fetch_all import fetch_from_sources
from engine.ai_intelligence_engine import (
    process_intelligence_pipeline,
    cluster_story_items,
    deduplicate_raw_items,
    synthesize_clusters,
    PILLARS
)
from engine.notifications_engine import run_market_notifications

def test_phase4():
    print("==================================================")
    print("URIVALENT PHASE 4: CLUSTERING & SYNTHESIS TEST")
    print("==================================================")

    # 1. Fetch live raw items using our Phase 2 & 3 pipeline
    print("\n[STEP 1] Ingesting items across multi-channel streams...")
    raw_items = fetch_from_sources(
        include_market=True,
        include_research=True,
        include_rss=True,
        include_social=True,
        include_news_apis=False, # skips unconfigured keys
        persist_snapshots=True
    )
    print(f"-> Ingested {len(raw_items)} raw intel items.")
    assert len(raw_items) > 0, "No raw items ingested!"

    # 2. Stage 1: Deduplication & TF-IDF Clustering
    print("\n[STEP 2] Running TF-IDF & Cosine Similarity Clustering...")
    deduped = deduplicate_raw_items(raw_items)
    print(f"-> Deduplicated: {len(deduped)} distinct items.")
    clusters = cluster_story_items(deduped, similarity_threshold=0.45)
    print(f"-> Formed {len(clusters)} Story Clusters.")
    assert len(clusters) > 0, "Zero clusters formed!"

    sample_c = clusters[0]
    print(f"  [OK] Top Cluster: ID={sample_c['cluster_id']}, Authority Score={sample_c['authority_score']}, "
          f"Sources Count={len(sample_c['source_urls'])}, Primary='{sample_c['primary_title'][:60]}...'")

    # 3. Stage 2: Synthesis & Pillar Taxonomy
    print("\n[STEP 3] Executing Synthesis Pass & 6-Pillar Classification...")
    synthesized = synthesize_clusters(clusters, top_n=15)
    print(f"-> Synthesized {len(synthesized)} intelligence briefing items.")
    assert len(synthesized) > 0, "Zero items synthesized!"

    for item in synthesized:
        assert item["pillar_category"] in PILLARS, f"Invalid pillar category: {item['pillar_category']}"
        assert 1 <= item["signal_score"] <= 10, f"Signal score out of range (1-10): {item['signal_score']}"
        assert item["primary_entity"], "Missing primary_entity!"
        assert item["headline"], "Missing headline!"
        assert item["what_happened"], "Missing what_happened!"
        assert item["technical_summary"], "Missing technical_summary!"
        assert item["strategic_implication"], "Missing strategic_implication!"
        assert item["hype_vs_substance"] in ("High Substance", "Moderate Substance", "Speculative / Hype"), f"Invalid hype rating: {item['hype_vs_substance']}"

    sample_s = synthesized[0]
    print(f"  [OK] Sample Synthesized Item:")
    print(f"       Pillar: {sample_s['pillar_category']}")
    print(f"       Entity: {sample_s['primary_entity']}")
    print(f"       Headline: {sample_s['headline']}")
    print(f"       Signal Score: {sample_s['signal_score']}/10 | Hype Rating: {sample_s['hype_vs_substance']}")
    print(f"       ⚙️ Engineering: {sample_s['technical_summary'][:80]}...")
    print(f"       📈 Business: {sample_s['strategic_implication'][:80]}...")

    # 4. Database Persistence Check
    print("\n[STEP 4] Persisting synthesized items to ai_intelligence_items...")
    from engine.ai_intelligence_engine import persist_synthesized_intelligence
    persisted_count = persist_synthesized_intelligence(synthesized)
    print(f"-> Persisted {persisted_count} items.")

    db_items = db_client.fetchall("SELECT * FROM ai_intelligence_items;")
    print(f"-> Database verification: {len(db_items)} rows stored in ai_intelligence_items.")
    assert len(db_items) > 0, "ai_intelligence_items table is empty!"

    # 5. Push Notification Engine Check
    print("\n[STEP 5] Testing Push Notification Engine (Tier-1 Alerts)...")
    notifications = run_market_notifications(synthesized)
    print(f"-> Generated {len(notifications)} FCM push notification payloads.")
    if notifications:
        print(f"  [OK] Sample FCM Payload: Title='{notifications[0]['notification']['title']}'")

    print("\n==================================================")
    print("ALL PHASE 4 CHECKS PASSED SUCCESSFULLY WITH ZERO ERRORS!")
    print("==================================================")

if __name__ == "__main__":
    test_phase4()

