"""
wiki_sync.py -- Bidirectional sync between wiki markdown and Neo4j graph.

Usage:
    python scripts/wiki_sync.py [--direction graph-to-wiki|wiki-to-graph|both] [--dry-run]
"""

import sys
sys.stdout.reconfigure(encoding="utf-8")

import argparse
import logging
import os
import re
from collections import defaultdict
from pathlib import Path

import yaml
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from graph_db import KnowledgeGraph
from wiki_templates import neo4j_id_to_wiki_path, render_frontmatter, render_page

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WIKI_ROOT = PROJECT_ROOT / "wiki"

WIKI_SUBDIRS = ["sources", "concepts", "methods", "entities", "synthesis"]


# ---------------------------------------------------------------------------
# Wiki scanning
# ---------------------------------------------------------------------------

def parse_wiki_page(path: Path) -> dict:
    """Extract YAML frontmatter and wikilinks from a markdown file."""
    text = path.read_text(encoding="utf-8")
    result = {"path": path, "frontmatter": {}, "wikilinks": [], "neo4j_id": None}

    # Parse YAML frontmatter
    fm_match = re.match(r"^---\n(.*?\n)---", text, re.DOTALL)
    if fm_match:
        try:
            fm = yaml.safe_load(fm_match.group(1))
            if isinstance(fm, dict):
                result["frontmatter"] = fm
                result["neo4j_id"] = fm.get("neo4j_id")
        except yaml.YAMLError:
            pass

    # Extract wikilinks
    wikilinks = re.findall(r"\[\[([^\]]+)\]\]", text)
    result["wikilinks"] = list(set(wikilinks))

    return result


def scan_wiki() -> dict:
    """Walk wiki/ recursively, parse all .md files. Returns {neo4j_id: page_info}."""
    pages = {}
    all_pages = {}  # by stem

    for subdir in WIKI_SUBDIRS:
        dir_path = WIKI_ROOT / subdir
        if not dir_path.exists():
            continue
        for md_file in dir_path.glob("*.md"):
            info = parse_wiki_page(md_file)
            all_pages[md_file.stem] = info
            if info["neo4j_id"]:
                pages[info["neo4j_id"]] = info

    # Also parse top-level pages (index, log, overview)
    for md_file in WIKI_ROOT.glob("*.md"):
        info = parse_wiki_page(md_file)
        all_pages[md_file.stem] = info

    return {"by_neo4j_id": pages, "by_stem": all_pages}


def scan_graph(kg) -> dict:
    """Query Neo4j for all nodes. Returns {id: {labels, properties}}."""
    results = kg.execute_query("""
        MATCH (n)
        RETURN n.id AS id, labels(n) AS labels, properties(n) AS props
    """)
    nodes = {}
    for r in results:
        nid = r["id"]
        if nid:
            nodes[nid] = {
                "id": nid,
                "labels": r["labels"],
                "props": r["props"],
            }
    return nodes


# ---------------------------------------------------------------------------
# Diff computation
# ---------------------------------------------------------------------------

def compute_diff(wiki_state, graph_nodes):
    """Compare wiki pages vs graph nodes. Returns sync report."""
    wiki_ids = set(wiki_state["by_neo4j_id"].keys())
    graph_ids = set(graph_nodes.keys())

    # Exclude Person nodes with fewer than 3 papers from expected wiki pages
    # (they don't get wiki pages by design)
    expected_graph_ids = set()
    for nid, node in graph_nodes.items():
        labels = node.get("labels", [])
        if "Person" in labels:
            continue  # Person nodes handled separately (only frequent authors)
        expected_graph_ids.add(nid)

    missing_wiki_pages = expected_graph_ids - wiki_ids  # in graph, no wiki page
    missing_graph_nodes = wiki_ids - graph_ids  # in wiki, no graph node

    return {
        "missing_wiki_pages": sorted(missing_wiki_pages),
        "missing_graph_nodes": sorted(missing_graph_nodes),
        "synced": sorted(wiki_ids & graph_ids),
    }


# ---------------------------------------------------------------------------
# Sync actions
# ---------------------------------------------------------------------------

def sync_graph_to_wiki(diff, graph_nodes, dry_run=False):
    """Create stub wiki pages for Neo4j nodes that lack them."""
    created = 0
    for nid in diff["missing_wiki_pages"]:
        node = graph_nodes[nid]
        path = neo4j_id_to_wiki_path(nid, WIKI_ROOT)
        labels = node.get("labels", [])
        props = node.get("props", {})

        if dry_run:
            logger.info(f"  WOULD CREATE stub: {path.relative_to(PROJECT_ROOT)} (from {labels})")
            created += 1
            continue

        # Determine page type
        name = props.get("name") or props.get("title") or nid
        if "Concept" in labels:
            category = props.get("category", "topic")
            if category == "method":
                page_type = "method"
                fm = render_frontmatter({"type": "method", "neo4j_id": nid, "name": name,
                                         "tags": ["method"], "source_count": 0})
                content = render_page("method", {"frontmatter": fm, "name": name,
                                                  "description": f"[Stub -- needs content]",
                                                  "sources": [], "related_methods": []})
            else:
                page_type = "concept"
                fm = render_frontmatter({"type": "concept", "category": category,
                                         "neo4j_id": nid, "name": name, "tags": [],
                                         "source_count": 0})
                content = render_page("concept", {"frontmatter": fm, "name": name,
                                                   "overview": "[Stub -- needs content]",
                                                   "sources": [], "methods": [], "related": []})
        elif "Paper" in labels:
            fm = render_frontmatter({"type": "source", "source_type": "paper",
                                     "neo4j_id": nid, "title": name, "tags": []})
            content = render_page("source_paper", {"frontmatter": fm, "title": name,
                                                    "abstract": "[Stub]", "findings": [],
                                                    "methods": [], "related_papers": [],
                                                    "concepts": [], "implements": []})
        elif "GithubRepo" in labels:
            fm = render_frontmatter({"type": "source", "source_type": "repo",
                                     "neo4j_id": nid, "name": name, "tags": []})
            content = render_page("source_repo", {"frontmatter": fm, "name": name,
                                                   "description": "[Stub]", "implements": [],
                                                   "methods": [], "concepts": []})
        else:
            continue  # Skip unknown node types

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        logger.info(f"  CREATED stub: {path.relative_to(PROJECT_ROOT)}")
        created += 1

    return created


def sync_wiki_to_graph(diff, wiki_state, kg, dry_run=False):
    """Create Neo4j nodes for wiki pages that don't have corresponding graph nodes."""
    created = 0
    for nid in diff["missing_graph_nodes"]:
        page = wiki_state["by_neo4j_id"][nid]
        fm = page["frontmatter"]
        page_type = fm.get("type", "")
        name = fm.get("name") or fm.get("title") or nid

        if dry_run:
            logger.info(f"  WOULD CREATE node: {nid} (from {page['path'].name})")
            created += 1
            continue

        if page_type == "source" and fm.get("source_type") == "paper":
            kg.add_node("paper", {"id": nid, "title": name})
        elif page_type == "source" and fm.get("source_type") == "repo":
            kg.add_node("github_repo", {"id": nid, "name": name})
        elif page_type in ("concept", "method"):
            kg.add_node("concept", {"id": nid, "name": name, "category": fm.get("category", "")})
        elif page_type == "entity":
            kg.add_node("person", {"id": nid, "name": name})
        else:
            logger.info(f"  SKIP (unknown type '{page_type}'): {nid}")
            continue

        logger.info(f"  CREATED node: {nid}")
        created += 1

    return created


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Sync wiki <-> Neo4j")
    parser.add_argument("--direction", choices=["graph-to-wiki", "wiki-to-graph", "both"],
                        default="both")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")

    logger.info("Scanning wiki pages...")
    wiki_state = scan_wiki()
    logger.info(f"  Found {len(wiki_state['by_neo4j_id'])} pages with neo4j_id")

    logger.info("Connecting to Neo4j...")
    kg = KnowledgeGraph(
        uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        user=os.getenv("NEO4J_USER", "neo4j"),
        password=os.getenv("NEO4J_PASSWORD"),
    )

    logger.info("Scanning graph nodes...")
    graph_nodes = scan_graph(kg)
    logger.info(f"  Found {len(graph_nodes)} nodes")

    diff = compute_diff(wiki_state, graph_nodes)

    logger.info(f"\n=== Sync Report ===")
    logger.info(f"  In sync: {len(diff['synced'])} nodes")
    logger.info(f"  Missing wiki pages: {len(diff['missing_wiki_pages'])}")
    logger.info(f"  Missing graph nodes: {len(diff['missing_graph_nodes'])}")

    total_changes = 0

    if args.direction in ("graph-to-wiki", "both") and diff["missing_wiki_pages"]:
        logger.info(f"\n--- Graph -> Wiki ---")
        total_changes += sync_graph_to_wiki(diff, graph_nodes, args.dry_run)

    if args.direction in ("wiki-to-graph", "both") and diff["missing_graph_nodes"]:
        logger.info(f"\n--- Wiki -> Graph ---")
        total_changes += sync_wiki_to_graph(diff, wiki_state, kg, args.dry_run)

    kg.close()

    if total_changes == 0:
        logger.info("\nEverything is in sync!")
    else:
        action = "Would make" if args.dry_run else "Made"
        logger.info(f"\n{action} {total_changes} change(s).")


if __name__ == "__main__":
    main()
