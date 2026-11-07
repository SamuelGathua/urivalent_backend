import os
import re
import json
import logging
import hashlib
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple

import requests
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from database.db_client import db_client

logger = logging.getLogger("urivalent.engine.intelligence")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

# The 6 Canonical Pillars of Urivalent
PILLARS = [
    "[SILICON & NANOTECH]",
    "[ENERGY & INFRA]",
    "[MODELS & RESEARCH]",
    "[AGENTS & TOOLING]",
    "[CAPITAL & MARKETS]",
    "[POLICY & SOVEREIGNTY]"
]

KNOWN_ENTITIES = [
    "OpenAI", "Anthropic", "Google DeepMind", "Google", "DeepMind", "Meta", "FAIR",
    "Microsoft", "NVIDIA", "TSMC", "ASML", "ARM", "Broadcom", "xAI", "Mistral",
    "Constellation Energy", "Cameco", "PyTorch", "vLLM", "Hugging Face", "arXiv",
    "EU AI Office", "NIST", "US Department of Commerce", "UK AI Safety Institute"
]

AUTHORITY_BOOST_BADGES = {"LAB", "SEC"}
AUTHORITY_BOOST_SOURCES = {"SemiAnalysis", "SEC EDGAR"}


# ==============================================================================
# STAGE 1: DEDUPLICATION & TF-IDF COSINE SIMILARITY CLUSTERING
# ==============================================================================

def normalize_text_key(text: str) -> str:
    """Lowercase and strip non-alphanumeric characters for clean string comparison."""
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def deduplicate_raw_items(raw_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deduplicate exact URLs and normalized titles across the ingested items."""
    seen_links = set()
    seen_titles = set()
    unique_items: List[Dict[str, Any]] = []

    for item in raw_items:
        link = item.get("link", "").strip()
        title_key = normalize_text_key(item.get("title", ""))

        if not link or not title_key:
            continue
        if link in seen_links or title_key in seen_titles:
            continue

        seen_links.add(link)
        seen_titles.add(title_key)
        unique_items.append(item)

    logger.info(f"Deduplication pass: {len(raw_items)} items reduced to {len(unique_items)} unique items.")
    return unique_items


def cluster_story_items(
    items: List[Dict[str, Any]],
    similarity_threshold: float = 0.45
) -> List[Dict[str, Any]]:
    """
    Groups deduplicated RawIntelItems covering the same event into Story Clusters (cluster_id).
    Uses TF-IDF (1-2 ngrams) + Cosine Similarity.
    Ranks clusters by coverage velocity + source authority boost (+3 for Official Labs, SEC, SemiAnalysis).
    """
    if not items:
        return []

    if len(items) == 1:
        single = items[0]
        cid = f"clust_{datetime.now(timezone.utc).strftime('%Y%m%d')}_{hashlib.md5(single['title'].encode()).hexdigest()[:8]}"
        return [{
            "cluster_id": cid,
            "items": [single],
            "primary_title": single["title"],
            "authority_score": 4 if single.get("source_badge") in AUTHORITY_BOOST_BADGES else 1,
            "source_urls": [{
                "title": single["title"],
                "link": single["link"],
                "source": single["source"],
                "badge": single["source_badge"]
            }]
        }]

    # Combine title + summary for semantic text matching
    corpus = [f"{it.get('title', '')} {it.get('summary', '')}" for it in items]

    try:
        vectorizer = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), max_features=2500)
        tfidf_matrix = vectorizer.fit_transform(corpus)
        sim_matrix = cosine_similarity(tfidf_matrix)
    except Exception as e:
        logger.warning(f"TF-IDF vectorizer fallback due to error: {e}. Grouping individually.")
        sim_matrix = [[1.0 if i == j else 0.0 for j in range(len(items))] for i in range(len(items))]

    n = len(items)
    visited = [False] * n
    raw_clusters: List[List[int]] = []

    for i in range(n):
        if visited[i]:
            continue
        current_cluster = [i]
        visited[i] = True

        for j in range(i + 1, n):
            if visited[j]:
                continue
            sim_score = sim_matrix[i][j]
            # Match condition: similarity >= threshold
            if sim_score >= similarity_threshold:
                current_cluster.append(j)
                visited[j] = True

        raw_clusters.append(current_cluster)

    formatted_clusters: List[Dict[str, Any]] = []
    today_prefix = datetime.now(timezone.utc).strftime("%Y%m%d")

    for c_indices in raw_clusters:
        cluster_items = [items[idx] for idx in c_indices]
        # Sort cluster items so highest authority or earliest item acts as primary
        cluster_items.sort(
            key=lambda x: (
                x.get("source_badge") in AUTHORITY_BOOST_BADGES or any(s in x.get("source", "") for s in AUTHORITY_BOOST_SOURCES),
                len(x.get("summary", ""))
            ),
            reverse=True
        )
        primary = cluster_items[0]

        # Generate deterministic cluster_id
        hash_seed = f"{primary['title']}_{today_prefix}"
        cluster_id = f"clust_{today_prefix}_{hashlib.md5(hash_seed.encode()).hexdigest()[:8]}"

        # Calculate coverage velocity & authority boost
        authority_boost = 0
        for it in cluster_items:
            badge = it.get("source_badge", "")
            source = it.get("source", "")
            if badge in AUTHORITY_BOOST_BADGES or any(s in source for s in AUTHORITY_BOOST_SOURCES):
                authority_boost += 3

        total_weight = len(cluster_items) + authority_boost

        # Compile source_urls JSON array
        sources_list = []
        for it in cluster_items:
            sources_list.append({
                "title": it.get("title", ""),
                "link": it.get("link", ""),
                "source": it.get("source", ""),
                "badge": it.get("source_badge", "")
            })

        formatted_clusters.append({
            "cluster_id": cluster_id,
            "items": cluster_items,
            "primary_title": primary.get("title", ""),
            "primary_image": primary.get("image_url"),
            "published_at": primary.get("published_at", datetime.now(timezone.utc).isoformat()),
            "authority_score": total_weight,
            "source_urls": sources_list
        })

    # Rank clusters by total authority score descending
    formatted_clusters.sort(key=lambda c: c["authority_score"], reverse=True)
    logger.info(f"Formed {len(formatted_clusters)} story clusters from {len(items)} items.")
    return formatted_clusters


# ==============================================================================
# STAGE 2: BATCHED LLM SYNTHESIS & PILLAR CLASSIFICATION
# ==============================================================================

def classify_cluster_rule_based(cluster: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deterministic fallback rule-based synthesis engine.
    Extracts primary_entity, assigns one of the 6 core pillars,
    and formats what_happened, technical_summary, strategic_implication, hype_vs_substance, and signal_score.
    """
    items = cluster["items"]
    primary = items[0]
    combined_text = f"{primary.get('title', '')} {' '.join(it.get('summary', '') for it in items)}"
    text_lower = combined_text.lower()

    # 1. Detect Primary Entity
    detected_entity = "Global AI Frontier"
    for ent in KNOWN_ENTITIES:
        if re.search(rf"\b{re.escape(ent.lower())}\b", text_lower):
            detected_entity = ent
            break

    # 2. Classify into Strictly One of 6 Core Pillars
    if any(k in text_lower for k in ["asml", "lithography", "packaging", "cowos", "wafer", "foundry", "tsmc", "nanotech", "silicon", "gpu", "tpu", "custom silicon", "chip"]):
        pillar = "[SILICON & NANOTECH]"
    elif any(k in text_lower for k in ["nuclear", "reactor", "data center", "cooling", "thermal", "hvac", "power purchase", "grid", "constellation", "cameco", "megawatt", "energy"]):
        pillar = "[ENERGY & INFRA]"
    elif any(k in text_lower for k in ["arxiv", "paper", "preprint", "architecture", "benchmark", "weights", "reasoning", "diffusion", "transformer", "math", "hugging face"]):
        pillar = "[MODELS & RESEARCH]"
    elif any(k in text_lower for k in ["framework", "sdk", "mcp", "pytorch", "vllm", "github", "inference engine", "agentic", "tooling", "library", "cuda kernel"]):
        pillar = "[AGENTS & TOOLING]"
    elif any(k in text_lower for k in ["funding", "series a", "series b", "series c", "capex", "valuation", "acquisition", "earnings", "stock", "market", "revenue", "yfinance", "shares"]):
        pillar = "[CAPITAL & MARKETS]"
    elif any(k in text_lower for k in ["regulation", "sec filing", "export control", "eu ai", "safety institute", "commerce", "tariff", "antitrust", "copyright", "policy"]):
        pillar = "[POLICY & SOVEREIGNTY]"
    else:
        pillar = "[MODELS & RESEARCH]"

    # 3. Calculate Signal Score (1 - 10)
    score = 5
    if any(it.get("source_badge") in AUTHORITY_BOOST_BADGES for it in items):
        score += 3
    if cluster["authority_score"] >= 4:
        score += 1
    if any(k in text_lower for k in ["breakthrough", "frontier", "sovereign", "multi-billion", "gigawatt", "state-of-the-art"]):
        score += 1
    score = min(10, max(1, score))

    # 4. Hype vs Substance Rating
    if any(it.get("source_badge") in ("ARXIV", "GH", "SEC") for it in items) or "code" in text_lower or "benchmark" in text_lower:
        hype_rating = "High Substance"
    elif any(k in text_lower for k in ["rumor", "plans to", "expected", "sources say", "reportedly"]):
        hype_rating = "Speculative / Hype"
    else:
        hype_rating = "Moderate Substance"

    # 5. Extract Structured Summaries
    headline = primary.get("title", "")
    # Clean badge prefixes from headline if present
    headline = re.sub(r"^(COMPUTE MARKET ALERT|SEC FILING|GITHUB VELOCITY|HUGGING FACE RADAR|ARXIV PREPRINT|HACKER NEWS|TG @\w+):?\s*", "", headline).strip()

    raw_summary = primary.get("summary", "")
    sentences = [s.strip() for s in re.split(r"(?<=[.!?]) +", raw_summary) if s.strip()]

    what_happened = sentences[0] if sentences else f"{detected_entity} announced major developments relevant to {pillar}."
    if len(what_happened) > 220:
        what_happened = what_happened[:217] + "..."

    tech_summary = sentences[1] if len(sentences) > 1 else f"Technical deployment centers on optimized {pillar.lower()} infrastructure and architectural integration."
    strategic_summary = (
        f"Signals expanding enterprise demand and defensible supply constraints for {detected_entity} within the AI ecosystem."
    )

    return {
        "cluster_id": cluster["cluster_id"],
        "pillar_category": pillar,
        "primary_entity": detected_entity,
        "headline": headline,
        "what_happened": what_happened,
        "technical_summary": tech_summary,
        "strategic_implication": strategic_summary,
        "hype_vs_substance": hype_rating,
        "signal_score": score,
        "source_urls": json.dumps(cluster["source_urls"]),
        "image_url": cluster.get("primary_image"),
        "published_at": cluster.get("published_at"),
        "is_dispatched": 0
    }


def synthesize_clusters_with_groq(clusters: List[Dict[str, Any]], groq_api_key: str) -> Optional[List[Dict[str, Any]]]:
    """Call Groq API (llama-3.3-70b-versatile) with JSON response mode."""
    try:
        from groq import Groq
        client = Groq(api_key=groq_api_key)

        prompt_payload = []
        for c in clusters:
            prompt_payload.append({
                "cluster_id": c["cluster_id"],
                "headline": c["primary_title"],
                "summaries": [it.get("summary") for it in c["items"][:3]],
                "sources": [it.get("source") for it in c["items"]]
            })

        system_prompt = (
            "You are Urivalent's chief AI intelligence research analyst. "
            "Given clusters of AI news coverage, classify each strictly into one of the 6 canonical pillars:\n"
            "- [SILICON & NANOTECH]\n"
            "- [ENERGY & INFRA]\n"
            "- [MODELS & RESEARCH]\n"
            "- [AGENTS & TOOLING]\n"
            "- [CAPITAL & MARKETS]\n"
            "- [POLICY & SOVEREIGNTY]\n\n"
            "Output JSON with a 'synthesized' list containing:\n"
            "- cluster_id: string\n"
            "- pillar_category: string (exact pillar name)\n"
            "- primary_entity: string (company/lab/org)\n"
            "- headline: string (sharp, authoritative)\n"
            "- what_happened: string (1 factual sentence)\n"
            "- technical_summary: string (The Engineering: specs, architecture, math)\n"
            "- strategic_implication: string (The Business: moat, capex, cost)\n"
            "- hype_vs_substance: 'High Substance' | 'Moderate Substance' | 'Speculative / Hype'\n"
            "- signal_score: integer between 1 and 10\n"
        )

        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(prompt_payload)}
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            timeout=30
        )

        content = response.choices[0].message.content
        data = json.loads(content)
        items = data.get("synthesized", data.get("items", []))
        if items and isinstance(items, list):
            # Match cluster metadata
            cluster_map = {c["cluster_id"]: c for c in clusters}
            enriched = []
            for it in items:
                cid = it.get("cluster_id")
                c_meta = cluster_map.get(cid, {})
                it["source_urls"] = json.dumps(c_meta.get("source_urls", []))
                it["image_url"] = c_meta.get("primary_image")
                it["published_at"] = c_meta.get("published_at", datetime.now(timezone.utc).isoformat())
                it["is_dispatched"] = 0
                enriched.append(it)
            return enriched
    except Exception as e:
        logger.warning(f"Groq LLM synthesis encountered an issue: {e}")
    return None


def synthesize_clusters_with_gemini(clusters: List[Dict[str, Any]], gemini_api_key: str) -> Optional[List[Dict[str, Any]]]:
    """Fallback: Call Google Gemini REST API."""
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_api_key}"
        prompt_payload = [
            {"cluster_id": c["cluster_id"], "headline": c["primary_title"], "summaries": [it.get("summary") for it in c["items"][:3]]}
            for c in clusters
        ]

        system_instruction = (
            "Analyze these AI news clusters. Classify each into: [SILICON & NANOTECH], [ENERGY & INFRA], [MODELS & RESEARCH], "
            "[AGENTS & TOOLING], [CAPITAL & MARKETS], [POLICY & SOVEREIGNTY]. "
            "Return valid JSON: {'synthesized': [{'cluster_id', 'pillar_category', 'primary_entity', 'headline', 'what_happened', 'technical_summary', 'strategic_implication', 'hype_vs_substance', 'signal_score'}]}."
        )

        body = {
            "contents": [{"parts": [{"text": f"{system_instruction}\n\nClusters: {json.dumps(prompt_payload)}"}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2}
        }

        resp = requests.post(url, json=body, timeout=30)
        if resp.status_code == 200:
            result = resp.json()
            text_resp = result["candidates"][0]["content"]["parts"][0]["text"]
            data = json.loads(text_resp)
            items = data.get("synthesized", data.get("items", []))
            if items and isinstance(items, list):
                cluster_map = {c["cluster_id"]: c for c in clusters}
                enriched = []
                for it in items:
                    cid = it.get("cluster_id")
                    c_meta = cluster_map.get(cid, {})
                    it["source_urls"] = json.dumps(c_meta.get("source_urls", []))
                    it["image_url"] = c_meta.get("primary_image")
                    it["published_at"] = c_meta.get("published_at", datetime.now(timezone.utc).isoformat())
                    it["is_dispatched"] = 0
                    enriched.append(it)
                return enriched
    except Exception as e:
        logger.warning(f"Gemini synthesis fallback encountered an issue: {e}")
    return None


def synthesize_clusters(clusters: List[Dict[str, Any]], top_n: int = 18) -> List[Dict[str, Any]]:
    """
    Stage 2 Master Runner:
    Takes top N clusters, passes through Groq -> Gemini -> Rule-based fallback.
    Returns list of fully synthesized intelligence dictionaries.
    """
    target_clusters = clusters[:top_n]
    if not target_clusters:
        return []

    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    synthesized: Optional[List[Dict[str, Any]]] = None

    # 1. Try Groq
    if groq_key:
        logger.info(f"Executing LLM synthesis pass with Groq (llama-3.3-70b-versatile) on {len(target_clusters)} clusters...")
        synthesized = synthesize_clusters_with_groq(target_clusters, groq_key)

    # 2. Try Gemini fallback
    if not synthesized and gemini_key:
        logger.info("Falling back to Google Gemini synthesis pass...")
        synthesized = synthesize_clusters_with_gemini(target_clusters, gemini_key)

    # 3. Rule-based deterministic synthesizer fallback
    if not synthesized:
        logger.info(f"Synthesizing {len(target_clusters)} clusters via deterministic taxonomy & heuristic rule engine...")
        synthesized = [classify_cluster_rule_based(c) for c in target_clusters]

    return synthesized


def persist_synthesized_intelligence(synthesized_items: List[Dict[str, Any]]) -> int:
    """
    Upserts synthesized intelligence items into ai_intelligence_items table.
    Ensures zero duplicate collisions on headline UNIQUE constraint.
    """
    if not synthesized_items:
        return 0

    query = """
    INSERT OR REPLACE INTO ai_intelligence_items
    (cluster_id, pillar_category, primary_entity, headline, what_happened, technical_summary, strategic_implication, hype_vs_substance, signal_score, source_urls, image_url, published_at, is_dispatched)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """

    params_list = []
    for it in synthesized_items:
        params_list.append((
            it.get("cluster_id"),
            it.get("pillar_category"),
            it.get("primary_entity"),
            it.get("headline"),
            it.get("what_happened"),
            it.get("technical_summary"),
            it.get("strategic_implication"),
            it.get("hype_vs_substance"),
            int(it.get("signal_score", 5)),
            it.get("source_urls", "[]"),
            it.get("image_url"),
            it.get("published_at", datetime.now(timezone.utc).isoformat()),
            int(it.get("is_dispatched", 0))
        ))

    db_client.executemany(query, params_list)
    logger.info(f"Successfully persisted {len(params_list)} synthesized items to ai_intelligence_items.")
    return len(params_list)


def process_intelligence_pipeline(raw_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    End-to-end execution of Phase 4:
    1. Deduplicate raw items.
    2. Cluster via TF-IDF + Cosine Similarity.
    3. Synthesize via LLM / Rule Engine.
    4. Upsert into ai_intelligence_items.
    """
    logger.info(f"Initiating AI Intelligence Engine pipeline on {len(raw_items)} raw items...")
    deduped = deduplicate_raw_items(raw_items)
    clusters = cluster_story_items(deduped)
    synthesized = synthesize_clusters(clusters)
    persist_synthesized_intelligence(synthesized)
    return synthesized

