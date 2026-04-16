"""
PDF full-text extractor for the knowledge graph.

For each PDF in data/pdfs/:
  1. Extracts text per section (Abstract, Introduction, Methods, Results,
     Discussion, Conclusion, References).
  2. Matches the PDF to an existing paper record in papers.json by title
     similarity.
  3. Enriches the paper record with full_text, sections, and page_count.
  4. Re-runs topic tagging on full text -> more MENTIONS relationships in Neo4j.
  5. Detects cross-citations between papers already in the collection
     -> creates CITES relationships in Neo4j.

Usage:
    cd C:/Users/mcgma/Desktop/myKG
    ./venv/Scripts/python.exe scripts/pdf_extractor.py
"""

import json
import logging
import os
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import fitz  # pymupdf
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent))
from graph_db import KnowledgeGraph
from topic_tagger import TOPICS, TOPIC_DISPLAY_NAMES, score_topics

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=True)

PDF_DIR  = Path(__file__).parent.parent / "data" / "pdfs"
DATA_DIR = Path(__file__).parent.parent / "data"

# Section headers to detect (case-insensitive, stripped lines)
SECTION_PATTERNS = [
    (r"^abstract$",                  "abstract"),
    (r"^introduction$",              "introduction"),
    (r"^(materials?(\s+and\s+)?)?methods?$", "methods"),
    (r"^(experimental\s+)?procedure", "methods"),
    (r"^results?(\s+and\s+discussion)?$", "results"),
    (r"^discussion$",                "discussion"),
    (r"^conclusion",                 "conclusion"),
    (r"^references?$",               "references"),
    (r"^bibliography$",              "references"),
    (r"^supplementary",              "supplementary"),
]

# Extended patterns for thesis/dissertation chapter structure
THESIS_SECTION_PATTERNS = [
    (r"^chapter\s+\d+\s*[:\-\u2013\u2014]?\s*.*introduction", "introduction"),
    (r"^chapter\s+\d+\s*[:\-\u2013\u2014]?\s*.*method",       "methods"),
    (r"^chapter\s+\d+\s*[:\-\u2013\u2014]?\s*.*result",       "results"),
    (r"^chapter\s+\d+\s*[:\-\u2013\u2014]?\s*.*discussion",   "discussion"),
    (r"^chapter\s+\d+\s*[:\-\u2013\u2014]?\s*.*conclusion",   "conclusion"),
    (r"^chapter\s+\d+\s*[:\-\u2013\u2014]?\s*.*integrat",     "integration_chapter"),
    (r"^chapter\s+\d+\s*[:\-\u2013\u2014]?\s*.*omics",        "omics_chapter"),
    (r"^chapter\s+\d+\s*[:\-\u2013\u2014]?",                  "chapter"),
    (r"^general\s+introduction$",   "introduction"),
    (r"^general\s+discussion$",     "discussion"),
    (r"^general\s+conclusion",      "conclusion"),
    (r"^summary$",                  "abstract"),
    (r"^synopsis$",                 "abstract"),
    (r"^publications?$",            "publications"),
    (r"^appendix",                  "appendix"),
    (r"^acknowledgement",           "acknowledgements"),
    (r"^table\s+of\s+contents",     "toc"),
    (r"^curriculum\s+vitae",        "cv"),
    *SECTION_PATTERNS,  # keep all standard paper patterns too
]

# Filename stem substrings that map directly to a paper ID (bypasses fuzzy match)
MANUAL_MATCHES: Dict[str, str] = {
    "thesis": "clos2019multi",
}


def _detect_document_type(full_text: str, page_count: int) -> str:
    """Return 'thesis' for PhD dissertations, 'paper' for regular articles."""
    if page_count < 80:
        return "paper"
    has_chapters = bool(re.search(r'\bchapter\s+\d+\b', full_text[:3000], re.IGNORECASE))
    has_thesis_marker = bool(re.search(
        r'\bthesis\b|\bdissertation\b|\bphd\b|\bdoctoral\b',
        full_text[:5000], re.IGNORECASE
    ))
    return "thesis" if (has_chapters and has_thesis_marker) else "paper"


# ── Text extraction ───────────────────────────────────────────────────────

def extract_pdf_text(pdf_path: Path) -> Tuple[str, Dict[str, str], int]:
    """
    Extract full text and section-split text from a PDF.
    Auto-detects thesis structure and applies appropriate section patterns.

    Returns:
        full_text   - all pages joined
        sections    - dict of section_name -> text
        page_count  - number of pages
    """
    doc = fitz.open(str(pdf_path))
    pages_text = [page.get_text("text") for page in doc]
    page_count = len(doc)
    doc.close()

    full_text = "\n".join(pages_text)
    doc_type  = _detect_document_type(full_text, page_count)

    if doc_type == "thesis":
        logger.info("  Document type: THESIS — using chapter patterns")
        sections = _split_into_sections(
            full_text, patterns=THESIS_SECTION_PATTERNS, max_header_len=120
        )
    else:
        sections = _split_into_sections(full_text)

    return full_text, sections, page_count


def _is_section_header(
    line: str,
    patterns: Optional[List] = None,
    max_len: int = 60,
) -> Optional[str]:
    """Return section key if line matches a known header, else None."""
    stripped = line.strip().rstrip(".")
    if not stripped or len(stripped) > max_len:
        return None
    active = patterns if patterns is not None else SECTION_PATTERNS
    for pattern, key in active:
        if re.match(pattern, stripped, re.IGNORECASE):
            return key
    return None


def _split_into_sections(
    text: str,
    patterns: Optional[List] = None,
    max_header_len: int = 60,
) -> Dict[str, str]:
    """Split full text into named sections."""
    sections: Dict[str, List[str]] = {"preamble": []}
    current = "preamble"

    for line in text.splitlines():
        key = _is_section_header(line, patterns, max_header_len)
        if key:
            current = key
            if key not in sections:
                sections[key] = []
        else:
            sections.setdefault(current, []).append(line)

    # Join and clean each section
    return {k: " ".join(v).strip() for k, v in sections.items() if v}


def _extract_title_from_pdf(pdf_path: Path) -> str:
    """
    Try to get the paper title from PDF metadata, then from first-page text.
    Returns a list of candidate strings to try against paper records.
    """
    doc = fitz.open(str(pdf_path))
    meta_title = (doc.metadata or {}).get("title", "").strip()
    first_page_text = doc[0].get_text("text") if len(doc) > 0 else ""
    doc.close()

    # Clean meta title: skip manuscript IDs (short, contains digits/dashes)
    if meta_title and len(meta_title) > 20 and not re.match(r"^[\w\-]+\s+\d+", meta_title):
        return meta_title

    # Join first-page lines into candidate title windows (handles line-broken titles)
    lines = [l.strip() for l in first_page_text.splitlines() if l.strip()]
    # Skip header labels like "ORIGINAL ARTICLE", page numbers, journal names
    skip_patterns = re.compile(
        r"^(original|research|letter|article|doi|www\.|http|vol\.|\d+$|©|copyright)", re.I
    )
    content_lines = [l for l in lines if len(l) > 15 and not skip_patterns.match(l)]

    # Also skip spaced-out uppercase headers like "O R I G I N A L A R T I C L E"
    content_lines = [
        l for l in content_lines
        if not re.match(r"^([A-Z] ){3,}", l)   # e.g. "O R I G I N A L"
    ]

    # Sliding window: try joining 1–4 consecutive lines to reconstruct split titles
    candidates = []
    for start in range(min(6, len(content_lines))):
        for end in range(start + 1, min(start + 5, len(content_lines) + 1)):
            candidates.append(" ".join(content_lines[start:end]))

    return candidates  # return all candidates so matcher can score each


# ── Paper matching ────────────────────────────────────────────────────────

def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def match_pdf_to_paper(
    pdf_path: Path, papers: List[Dict], threshold: float = 0.50,
) -> Optional[Dict]:
    """
    Find the best matching paper record for a PDF.
    Tries all sliding-window title candidates, filename stem, and first-page text.
    """
    # Check manual matches first (e.g. thesis keyword in filename)
    stem_lower = pdf_path.stem.lower()
    for keyword, paper_id in MANUAL_MATCHES.items():
        if keyword in stem_lower:
            match = next((p for p in papers if p["id"] == paper_id), None)
            if match:
                logger.info(f"  Manual match via keyword '{keyword}' -> {paper_id}")
                return match

    title_result = _extract_title_from_pdf(pdf_path)
    # May be a string or list of candidate strings
    if isinstance(title_result, str):
        pdf_titles = [title_result]
    else:
        pdf_titles = title_result

    stem = pdf_path.stem.replace("_", " ").replace("-", " ")
    pdf_titles.append(stem)

    best_score = 0.0
    best_paper = papers[0]

    for paper in papers:
        paper_title = paper.get("title", "")
        for candidate in pdf_titles:
            score = _similarity(candidate, paper_title)
            if score > best_score:
                best_score = score
                best_paper = paper

    if best_score >= threshold:
        return best_paper

    logger.warning(
        f"  No match for {pdf_path.name} "
        f"(best={best_score:.2f} -> '{best_paper['id']}')"
    )
    return None


# ── Citation detection ────────────────────────────────────────────────────

def find_internal_citations(
    references_text: str, papers: List[Dict], threshold: float = 0.70
) -> List[str]:
    """
    Given the references section text, return IDs of papers in the collection
    that are cited (fuzzy title match).
    """
    cited_ids = []
    for paper in papers:
        title = paper.get("title", "")
        if title and _similarity(title, references_text) < threshold:
            # Also do a substring-style check for long titles
            words = title.lower().split()
            # Require at least 6 consecutive distinctive words to appear
            chunk = " ".join(words[:8])
            if chunk and chunk in references_text.lower():
                cited_ids.append(paper["id"])
    return cited_ids


# ── Neo4j helpers ─────────────────────────────────────────────────────────

def upsert_full_text_topics(
    kg: KnowledgeGraph, paper: Dict, sections: Dict[str, str]
) -> int:
    """
    Score the full paper text against topics and create/update MENTIONS rels.
    Returns number of new relationships created.
    """
    # Weighted text: give methods/results more weight by repetition
    rich_text = " ".join(filter(None, [
        sections.get("abstract", "") * 3,   # abstract 3×
        sections.get("introduction", ""),
        sections.get("methods", "") * 2,
        sections.get("results", "") * 2,
        sections.get("discussion", ""),
        sections.get("conclusion", ""),
        paper.get("abstract", ""),           # already fetched from PubMed
        paper.get("title", "") * 5,          # title most important
    ]))

    scores = score_topics(rich_text)
    count = 0
    for topic_id, score in scores.items():
        kg.add_relationship(
            from_id=paper["id"],
            to_id=f"concept:{topic_id}",
            rel_type="MENTIONS",
            from_label="Paper",
            to_label="Concept",
            properties={"score": score, "source": "full_text"},
        )
        count += 1
    return count


def create_cites_relationships(
    kg: KnowledgeGraph, citing_id: str, cited_ids: List[str]
) -> int:
    """Create CITES relationships from citing paper to cited papers."""
    count = 0
    for cited_id in cited_ids:
        if cited_id == citing_id:
            continue
        kg.add_relationship(
            from_id=citing_id,
            to_id=cited_id,
            rel_type="CITES",
            from_label="Paper",
            to_label="Paper",
        )
        logger.info(f"    CITES: {citing_id} -> {cited_id}")
        count += 1
    return count


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    # Load existing paper records
    papers_path = DATA_DIR / "papers.json"
    with open(papers_path, encoding="utf-8") as f:
        papers = json.load(f)
    logger.info(f"Loaded {len(papers)} paper records")

    pdf_files = sorted(PDF_DIR.glob("*.pdf"))
    logger.info(f"Found {len(pdf_files)} PDFs in {PDF_DIR}")

    # Connect to Neo4j
    kg = KnowledgeGraph(
        uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        user=os.getenv("NEO4J_USER", "neo4j"),
        password=os.getenv("NEO4J_PASSWORD", "knowledge123"),
        config_path=str(Path(__file__).parent.parent / "config" / "schema.yaml"),
    )

    total_mentions = 0
    total_cites    = 0
    matched        = 0

    for pdf_path in pdf_files:
        logger.info(f"\nProcessing: {pdf_path.name}")

        # 1. Match to paper record
        paper = match_pdf_to_paper(pdf_path, papers)
        if paper is None:
            continue
        matched += 1
        logger.info(f"  Matched -> {paper['id']}: {paper['title'][:60]}")

        # 2. Extract text
        full_text, sections, page_count = extract_pdf_text(pdf_path)
        logger.info(
            f"  Pages: {page_count} | "
            f"Sections found: {list(sections.keys())}"
        )

        # 3. Enrich paper record
        paper["full_text_path"] = str(pdf_path)
        paper["page_count"]     = page_count
        paper["sections"]       = list(sections.keys())
        if not paper.get("abstract") and sections.get("abstract"):
            paper["abstract"] = sections["abstract"][:2000]

        # Store section word counts (not raw text in JSON — keep it lean)
        paper["section_lengths"] = {
            k: len(v.split()) for k, v in sections.items()
        }

        # 4. Update Neo4j node with new properties
        kg.add_node("Paper", {
            "id":            paper["id"],
            "page_count":    page_count,
            "has_full_text": True,
            "sections":      list(sections.keys()),
        })

        # 5. Topic tagging from full text
        n_mentions = upsert_full_text_topics(kg, paper, sections)
        total_mentions += n_mentions
        logger.info(f"  Topics tagged: {n_mentions}")

        # 6. Internal citation detection
        refs_text = sections.get("references", "")
        if refs_text:
            cited = find_internal_citations(refs_text, papers)
            cited = [c for c in cited if c != paper["id"]]
            if cited:
                logger.info(f"  Internal citations found: {cited}")
                n_cites = create_cites_relationships(kg, paper["id"], cited)
                total_cites += n_cites
            else:
                logger.info("  No internal citations detected")

    # Save enriched papers.json
    with open(papers_path, "w", encoding="utf-8") as f:
        json.dump(papers, f, indent=2, ensure_ascii=False)
    logger.info(f"\nSaved enriched papers.json")

    # Summary
    stats = kg.get_statistics()
    print(f"\n--- Results ---")
    print(f"  PDFs processed : {len(pdf_files)}")
    print(f"  Matched        : {matched}")
    print(f"  New MENTIONS   : {total_mentions}")
    print(f"  CITES created  : {total_cites}")
    print(f"\n--- Graph statistics ---")
    for k, v in stats.items():
        print(f"  {k}: {v}")

    kg.close()
    logger.info("pdf_extractor.py complete")


if __name__ == "__main__":
    main()
