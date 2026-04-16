"""
wiki_ingest.py -- End-to-end ingestion workflow for new sources.

Detects new BibTeX entries or repos, runs the pipeline, generates wiki pages,
and updates index.md + log.md.

Usage:
    python scripts/wiki_ingest.py [--source paper|repo] [--id PAPER_ID]
"""

import sys
sys.stdout.reconfigure(encoding="utf-8")

import argparse
import json
import logging
import os
import re
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from graph_db import KnowledgeGraph
from wiki_templates import neo4j_id_to_wiki_path, neo4j_id_to_wikilink, render_frontmatter, render_page, slug_from_name
from wiki_bootstrap import (
    load_graph_data,
    generate_index,
    _clean_latex,
    _short_title,
    _write_page,
    AUTHOR_MIN_PAPERS,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WIKI_ROOT = PROJECT_ROOT / "wiki"
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = PROJECT_ROOT / "raw"


def detect_new_papers():
    """Compare BibTeX entries against existing papers.json to find new ones."""
    papers_json = DATA_DIR / "papers.json"
    bib_file = RAW_DIR / "papers" / "my_papers.bib"

    if not bib_file.exists():
        logger.info("No BibTeX file found at raw/papers/my_papers.bib")
        return []

    with open(papers_json, encoding="utf-8") as f:
        existing = json.load(f)
    existing_ids = {p["id"] for p in existing}

    # Parse BibTeX to get entry keys
    import bibtexparser
    with open(bib_file, encoding="utf-8") as f:
        bib_db = bibtexparser.load(f)

    new_entries = []
    for entry in bib_db.entries:
        entry_id = entry.get("ID", "")
        if entry_id and entry_id not in existing_ids:
            new_entries.append(entry_id)

    return new_entries


def run_pipeline_for_paper(paper_id: str, kg):
    """Run extraction + tagging pipeline for a specific new paper."""
    logger.info(f"Running pipeline for: {paper_id}")

    # Re-import BibTeX (idempotent)
    from import_bibtex import parse_bibtex_file
    bib_path = RAW_DIR / "papers" / "my_papers.bib"
    all_papers = parse_bibtex_file(str(bib_path))

    # Find the new paper
    new_paper = None
    for p in all_papers:
        if p.get("id") == paper_id:
            new_paper = p
            break

    if not new_paper:
        logger.warning(f"Paper {paper_id} not found in BibTeX file")
        return None

    # Update papers.json
    papers_json = DATA_DIR / "papers.json"
    with open(papers_json, encoding="utf-8") as f:
        papers = json.load(f)

    # Check if already added
    existing_ids = {p["id"] for p in papers}
    if paper_id not in existing_ids:
        papers.append(new_paper)
        with open(papers_json, "w", encoding="utf-8") as f:
            json.dump(papers, f, indent=2, ensure_ascii=False)
        logger.info(f"  Added to papers.json")

    # Add to Neo4j
    kg.add_node("paper", {
        "id": new_paper["id"],
        "title": new_paper.get("title", ""),
        "abstract": new_paper.get("abstract", ""),
        "year": new_paper.get("year", 0),
        "venue": new_paper.get("venue", ""),
        "authors": new_paper.get("authors", []),
    })

    # Add AUTHORED_BY
    for author in new_paper.get("authors", []):
        author_id = author.lower().replace(" ", "_")
        kg.add_node("person", {"id": author_id, "name": author, "role": "author"})
        kg.add_relationship(new_paper["id"], author_id, "AUTHORED_BY", "Paper", "Person")

    logger.info(f"  Added to Neo4j graph")
    return new_paper


def generate_paper_wiki_page(paper, graph_data):
    """Generate the wiki source page for a newly ingested paper."""
    pid = paper["id"]
    path = neo4j_id_to_wiki_path(pid, WIKI_ROOT)

    concepts_raw = graph_data.get("paper_concepts", {}).get(pid, [])
    concepts = [{"link": neo4j_id_to_wikilink(c["concept_id"]), "rel_type": c["rel_type"]}
                 for c in concepts_raw if c["rel_type"] == "MENTIONS"]
    methods = [{"link": neo4j_id_to_wikilink(c["concept_id"]), "name": c["name"]}
                for c in concepts_raw if c["rel_type"] == "USES_TECHNOLOGY"]

    related = graph_data.get("paper_relations", {}).get(pid, [])
    related_papers = [{"link": neo4j_id_to_wikilink(r["id"]), "score": f'{r["score"]:.2f}'}
                      for r in related]

    tags = list(set(neo4j_id_to_wikilink(c["concept_id"]) for c in concepts_raw))

    frontmatter = render_frontmatter({
        "type": "source",
        "source_type": "paper",
        "neo4j_id": pid,
        "title": paper.get("title", pid),
        "year": paper.get("year", ""),
        "venue": paper.get("venue", ""),
        "tags": tags[:10],
    })

    abstract = paper.get("abstract", "[Abstract not available]") or "[Abstract not available]"

    content = render_page("source_paper", {
        "frontmatter": frontmatter,
        "title": paper.get("title", pid),
        "abstract": abstract,
        "findings": [],
        "methods": methods,
        "related_papers": related_papers,
        "concepts": concepts,
        "implements": [],
    })

    _write_page(path, content, force=False, dry_run=False)
    return path


def update_concept_pages(paper, graph_data, papers_by_id):
    """Add the new paper to relevant concept/method wiki pages."""
    pid = paper["id"]
    concepts_raw = graph_data.get("paper_concepts", {}).get(pid, [])
    updated = []

    for c in concepts_raw:
        cid = c["concept_id"]
        path = neo4j_id_to_wiki_path(cid, WIKI_ROOT)
        if not path.exists():
            continue

        text = path.read_text(encoding="utf-8")
        paper_link = neo4j_id_to_wikilink(pid)

        # Check if already referenced
        if f"[[{paper_link}]]" in text:
            continue

        # Add to Sources section
        source_line = f"- [[{paper_link}]] -- {_short_title(paper.get('title', ''), 50)} ({paper.get('year', '')})"

        # Insert after "## Sources" or "## Used In"
        for header in ["## Sources", "## Used In"]:
            if header in text:
                # Find the end of the source list
                idx = text.index(header) + len(header)
                # Find next section or end
                next_section = re.search(r"\n## ", text[idx + 1:])
                insert_pos = idx + 1 + next_section.start() if next_section else len(text)
                text = text[:insert_pos] + source_line + "\n" + text[insert_pos:]
                path.write_text(text, encoding="utf-8")
                updated.append(path.stem)
                break

        # Update source_count in frontmatter
        fm_match = re.search(r"source_count:\s*(\d+)", text)
        if fm_match:
            old_count = int(fm_match.group(1))
            text = text.replace(f"source_count: {old_count}", f"source_count: {old_count + 1}")
            path.write_text(text, encoding="utf-8")

    return updated


def append_log(paper_id: str, title: str, updated_pages: list):
    """Append ingest entry to log.md."""
    log_path = WIKI_ROOT / "log.md"
    text = log_path.read_text(encoding="utf-8") if log_path.exists() else "---\ntype: log\n---\n\n# Operations Log\n"

    entry_lines = [
        f"\n## [{date.today().isoformat()}] ingest | {title}",
        f"",
        f"- Created source page: [[{neo4j_id_to_wikilink(paper_id)}]]",
    ]
    if updated_pages:
        entry_lines.append(f"- Updated concept pages: {', '.join(f'[[{p}]]' for p in updated_pages)}")
    entry_lines.append(f"- Updated index.md")
    entry_lines.append("")

    log_path.write_text(text + "\n".join(entry_lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Ingest new source into wiki + graph")
    parser.add_argument("--source", choices=["paper", "repo"], default="paper")
    parser.add_argument("--id", help="Specific paper/repo ID to ingest")
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env", override=True)

    kg = KnowledgeGraph(
        uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        user=os.getenv("NEO4J_USER", "neo4j"),
        password=os.getenv("NEO4J_PASSWORD"),
    )

    if args.source == "paper":
        if args.id:
            new_ids = [args.id]
        else:
            new_ids = detect_new_papers()
            if not new_ids:
                logger.info("No new papers detected in BibTeX file.")
                kg.close()
                return
            logger.info(f"Detected {len(new_ids)} new paper(s): {new_ids}")

        for paper_id in new_ids:
            # Run extraction pipeline
            paper = run_pipeline_for_paper(paper_id, kg)
            if not paper:
                continue

            # Reload graph data
            graph_data = load_graph_data(kg)

            # Load papers for lookup
            with open(DATA_DIR / "papers.json", encoding="utf-8") as f:
                papers = json.load(f)
            papers_by_id = {p["id"]: p for p in papers}

            # Generate wiki page
            page_path = generate_paper_wiki_page(paper, graph_data)
            logger.info(f"  Created wiki page: {page_path.relative_to(PROJECT_ROOT)}")

            # Update concept pages
            updated = update_concept_pages(paper, graph_data, papers_by_id)
            if updated:
                logger.info(f"  Updated concept pages: {', '.join(updated)}")

            # Update index
            generate_index(force=True, dry_run=False)

            # Append log
            append_log(paper_id, paper.get("title", paper_id), updated)
            logger.info(f"  Updated index.md and log.md")

    kg.close()
    logger.info("\nIngestion complete!")


if __name__ == "__main__":
    main()
