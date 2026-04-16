"""
Topic tagger for the knowledge graph.

1. Fetches missing paper abstracts from Semantic Scholar.
2. Assigns predefined research topics to papers and repos using keyword matching.
3. Creates Concept nodes and MENTIONS relationships in Neo4j.
4. Loads user-defined repo→paper links from data/repo_paper_links.json
   and creates IMPLEMENTS relationships.

Usage:
    cd C:/Users/mcgma/Desktop/myKG
    ./venv/Scripts/activate
    cd scripts && python topic_tagger.py
"""

import json
import logging
import os
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List

import requests
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent))
from graph_db import KnowledgeGraph

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=True)

# ── Research Topic Definitions ────────────────────────────────────────────
# Each topic has a list of keywords that trigger a match.
# Keywords are matched case-insensitively against title + abstract + readme.

TOPICS: Dict[str, List[str]] = {
    "microbiome": [
        "microbiome", "microbiota", "gut microbiome", "gut bacteria",
        "microbial community", "16s rrna", "16s", "gut flora",
        "bacterial community", "microbial diversity", "dysbiosis",
        "gut microbiota", "intestinal microbiota",
    ],
    "metagenomics": [
        "metagenomics", "metagenomic", "shotgun sequencing", "metag",
        "whole genome shotgun", "wgs", "functional metagenomics",
        "metagenome", "shotgun metagenomics",
    ],
    "inflammation": [
        "inflammation", "inflammatory", "liver disease", "fibrosis",
        "hepatitis", "nash", "nafld", "steatotic", "steatohepatitis",
        "alcohol-associated", "aald", "cytokine", "immune response",
        "innate immunity", "liver fibrosis", "hepatic fibrosis",
        "liver progenitor", "mitochondri",
    ],
    "pain": [
        "pain", "fibromyalgia", "neuropathic pain", "chronic pain",
        "glutamate", "pain disorder", "analgesia", "hyperalgesia",
        "allodynia", "algesia",
    ],
    "neurodegeneration": [
        "neurodegeneration", "neurodegenerative", "dementia", "alzheimer",
        "parkinson", "cerebrospinal fluid", "csf", "cognitive decline",
        "neuroinflammation", "amyloid", "tau protein", "synuclein",
        "lewy body", "frontotemporal",
    ],
    "metabolic_diseases": [
        "diabetes", "diabetic", "obesity", "metabolic syndrome", "prediabetes",
        "t1d", "t2d", "type 1 diabetes", "type 2 diabetes", "insulin",
        "glucose", "hyperglycemia", "glycemic", "albuminuria", "metformin",
        "glucocorticoid", "steroid hormone", "cortisol", "glycosylat",
    ],
    "metabolomics": [
        "metabolomics", "metabolomic", "metabolites", "metabolome",
        "nmr spectroscopy", "uplc", "lc-ms", "gc-ms", "mass spectrometry",
        "lipidome", "lipidomics", "serum metabolome", "urinary metabolome",
        "fecal metabolomics", "plasma metabolom", "metabolic profil",
        "extracellular vesicle",
    ],
    "multi_omics": [
        "multi-omics", "multiomics", "proteomics", "transcriptomics",
        "epigenomics", "proteome", "transcriptome", "microrna", "mirna",
        "rna-seq", "whole exome", "snp", "gwas", "genome-wide",
    ],
    "integration": [
        "integration", "integrative analysis", "data fusion",
        "multi-modal", "data integration", "joint analysis",
        "network analysis", "systems biology", "pathway analysis",
        "cross-omics", "multi-source",
    ],
    "machine_learning": [
        "machine learning", "deep learning", "neural network",
        "random forest", "classification", "prediction model",
        "artificial intelligence", "feature selection",
        "dimensionality reduction", "clustering", "lasso", "svm",
        "support vector", "logistic regression", "gradient boosting",
        "random forest", "xgboost", "biomarker discovery",
    ],
}

TOPIC_DISPLAY_NAMES = {
    "microbiome": "Microbiome",
    "metagenomics": "Metagenomics",
    "inflammation": "Inflammation",
    "pain": "Pain",
    "neurodegeneration": "Neurodegeneration",
    "metabolic_diseases": "Metabolic Diseases",
    "metabolomics": "Metabolomics",
    "multi_omics": "Multi-Omics",
    "integration": "Integration",
    "machine_learning": "Machine Learning",
}


# ── Text helpers ──────────────────────────────────────────────────────────

def score_topics(text: str) -> Dict[str, int]:
    """Return {topic_id: keyword_hit_count} for non-zero matches."""
    text_lower = text.lower()
    scores = {}
    for topic_id, keywords in TOPICS.items():
        count = sum(1 for kw in keywords if kw in text_lower)
        if count > 0:
            scores[topic_id] = count
    return scores


def paper_text(paper: Dict) -> str:
    return " ".join(filter(None, [
        paper.get("title", ""),
        paper.get("abstract", ""),
        " ".join(paper.get("keywords", []) or []),
        paper.get("venue", ""),
    ]))


def repo_text(repo: Dict) -> str:
    return " ".join(filter(None, [
        repo.get("name", ""),
        repo.get("description", "") or "",
        repo.get("readme", "") or "",
        " ".join(repo.get("topics", []) or []),
    ]))


# ── Abstract fetching via PubMed E-utilities ──────────────────────────────

PUBMED_SEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_FETCH  = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


def _search_pmid(title: str) -> str:
    """Search PubMed for a title, return first PMID or ''."""
    try:
        resp = requests.get(
            PUBMED_SEARCH,
            params={"db": "pubmed", "term": f"{title}[Title]", "retmode": "json", "retmax": 1},
            timeout=12,
        )
        if resp.status_code == 200:
            ids = resp.json().get("esearchresult", {}).get("idlist", [])
            return ids[0] if ids else ""
    except Exception as exc:
        logger.warning(f"PubMed search error: {exc}")
    return ""


def _fetch_abstract_by_pmid(pmid: str) -> str:
    """Fetch XML record from PubMed and extract AbstractText."""
    try:
        resp = requests.get(
            PUBMED_FETCH,
            params={"db": "pubmed", "id": pmid, "retmode": "xml", "rettype": "abstract"},
            timeout=12,
        )
        if resp.status_code == 200:
            root = ET.fromstring(resp.text)
            parts = [el.text or "" for el in root.iter("AbstractText")]
            return " ".join(parts).strip()
    except Exception as exc:
        logger.warning(f"PubMed fetch error for PMID {pmid}: {exc}")
    return ""


def fetch_abstract(title: str) -> str:
    """Fetch abstract from PubMed by title. Returns '' on failure."""
    pmid = _search_pmid(title)
    if pmid:
        return _fetch_abstract_by_pmid(pmid)
    return ""


def enrich_abstracts(papers: List[Dict]) -> List[Dict]:
    """Fill blank abstracts using PubMed E-utilities."""
    missing = [p for p in papers if not (p.get("abstract") or "").strip()]
    logger.info(f"Fetching abstracts for {len(missing)} papers via PubMed...")
    for paper in missing:
        abstract = fetch_abstract(paper["title"])
        if abstract:
            paper["abstract"] = abstract
            logger.info(f"  OK   {paper['id']}: {abstract[:60]}...")
        else:
            logger.warning(f"  MISS {paper['id']}")
        time.sleep(0.4)   # stay within NCBI rate limits (~3 req/s)
    return papers


# ── Neo4j helpers ─────────────────────────────────────────────────────────

def create_topic_nodes(kg: KnowledgeGraph):
    """Upsert one Concept node per research topic."""
    for topic_id, display in TOPIC_DISPLAY_NAMES.items():
        kg.add_node("Concept", {
            "id": f"concept:{topic_id}",
            "name": display,
            "category": "research_topic",
            "keywords": TOPICS[topic_id],
        })
    logger.info(f"Upserted {len(TOPICS)} topic concept nodes")


def tag_papers(kg: KnowledgeGraph, papers: List[Dict]) -> int:
    count = 0
    for paper in papers:
        text = paper_text(paper)
        for topic_id, score in score_topics(text).items():
            kg.add_relationship(
                from_id=paper["id"],
                to_id=f"concept:{topic_id}",
                rel_type="MENTIONS",
                from_label="Paper",
                to_label="Concept",
                properties={"score": score, "source": "keyword_match"},
            )
            count += 1
    logger.info(f"Tagged {len(papers)} papers → {count} MENTIONS relationships")
    return count


def tag_repos(kg: KnowledgeGraph, repos: List[Dict]) -> int:
    count = 0
    for repo in repos:
        text = repo_text(repo)
        for topic_id, score in score_topics(text).items():
            kg.add_relationship(
                from_id=repo["id"],
                to_id=f"concept:{topic_id}",
                rel_type="MENTIONS",
                from_label="GithubRepo",
                to_label="Concept",
                properties={"score": score, "source": "keyword_match"},
            )
            count += 1
    logger.info(f"Tagged {len(repos)} repos -> {count} MENTIONS relationships")
    return count


def load_repo_paper_links(
    kg: KnowledgeGraph,
    links_path: str = "data/repo_paper_links.json",
) -> int:
    """Create IMPLEMENTS relationships from user-defined links file."""
    p = Path(links_path)
    if not p.exists():
        logger.info(f"No links file at {links_path}; skipping IMPLEMENTS step")
        return 0
    with open(p) as f:
        links = json.load(f)
    count = 0
    for link in links:
        kg.add_relationship(
            from_id=link["repo_id"],
            to_id=link["paper_id"],
            rel_type="IMPLEMENTS",
            from_label="GithubRepo",
            to_label="Paper",
            properties={"note": link.get("note", "")},
        )
        logger.info(f"  IMPLEMENTS: {link['repo_id']} → {link['paper_id']}")
        count += 1
    logger.info(f"Added {count} IMPLEMENTS relationships from links file")
    return count


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    data_dir = Path(__file__).parent.parent / "data"

    # 1. Load data
    with open(data_dir / "papers.json", encoding="utf-8") as f:
        papers = json.load(f)
    with open(data_dir / "github_repos.json", encoding="utf-8") as f:
        repos = json.load(f)
    logger.info(f"Loaded {len(papers)} papers, {len(repos)} repos")

    # 2. Enrich abstracts
    papers = enrich_abstracts(papers)
    with open(data_dir / "papers.json", "w", encoding="utf-8") as f:
        json.dump(papers, f, indent=2, ensure_ascii=False)
    logger.info("Saved enriched papers.json")

    # 3. Dry-run preview
    print("\n--- Topic assignments (preview) ---")
    for paper in papers:
        scores = score_topics(paper_text(paper))
        topics_found = sorted(scores, key=scores.get, reverse=True)
        print(f"  [PAPER] {paper['id']}: {topics_found}")
    for repo in repos:
        scores = score_topics(repo_text(repo))
        topics_found = sorted(scores, key=scores.get, reverse=True)
        if topics_found:
            print(f"  [REPO]  {repo['id']}: {topics_found}")
    print()

    # 4. Connect to Neo4j
    kg = KnowledgeGraph(
        uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        user=os.getenv("NEO4J_USER", "neo4j"),
        password=os.getenv("NEO4J_PASSWORD", "knowledge123"),
        config_path=str(Path(__file__).parent.parent / "config" / "schema.yaml"),
    )

    # 5. Create topic concept nodes
    create_topic_nodes(kg)

    # 6. Tag papers and repos
    tag_papers(kg, papers)
    tag_repos(kg, repos)

    # 7. Load manual repo→paper links
    load_repo_paper_links(kg, str(data_dir / "repo_paper_links.json"))

    # 8. Stats
    stats = kg.get_statistics()
    print("\n--- Graph statistics ---")
    for k, v in stats.items():
        print(f"  {k}: {v}")

    kg.close()
    logger.info("topic_tagger.py complete ✓")


if __name__ == "__main__":
    main()
