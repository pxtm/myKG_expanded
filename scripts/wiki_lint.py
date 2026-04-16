"""
wiki_lint.py -- Health-check the wiki for quality issues.

Usage:
    python scripts/wiki_lint.py [--fix] [--verbose]
"""

import sys
sys.stdout.reconfigure(encoding="utf-8")

import argparse
import logging
import os
import re
from collections import defaultdict
from datetime import date
from pathlib import Path

import yaml
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from graph_db import KnowledgeGraph
from wiki_templates import neo4j_id_to_wiki_path

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WIKI_ROOT = PROJECT_ROOT / "wiki"

WIKI_SUBDIRS = ["sources", "concepts", "methods", "entities", "synthesis"]
EXEMPT_FILES = {"index", "log", "overview"}  # top-level files exempt from orphan check


def scan_all_pages():
    """Scan all wiki pages, extracting frontmatter and wikilinks.
    Uses stem as key. If duplicates across subdirs, uses subdir/stem."""
    pages = {}
    seen_stems = {}  # stem -> subdir
    for subdir in WIKI_SUBDIRS:
        dir_path = WIKI_ROOT / subdir
        if not dir_path.exists():
            continue
        for md_file in dir_path.glob("*.md"):
            stem = md_file.stem
            if stem in pages:
                # Conflict -- re-key the existing entry and this one
                old_subdir = seen_stems[stem]
                old_page = pages.pop(stem)
                pages[f"{old_subdir}/{stem}"] = old_page
                pages[f"{subdir}/{stem}"] = _parse_page(md_file)
            else:
                pages[stem] = _parse_page(md_file)
                seen_stems[stem] = subdir
    # Top-level pages
    for md_file in WIKI_ROOT.glob("*.md"):
        pages[md_file.stem] = _parse_page(md_file)
    return pages


def _parse_page(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    result = {"path": path, "frontmatter": {}, "wikilinks": [], "neo4j_id": None,
              "sections": [], "text": text}

    # Frontmatter
    fm_match = re.match(r"^---\n(.*?\n)---", text, re.DOTALL)
    if fm_match:
        try:
            fm = yaml.safe_load(fm_match.group(1))
            if isinstance(fm, dict):
                result["frontmatter"] = fm
                result["neo4j_id"] = fm.get("neo4j_id")
        except yaml.YAMLError:
            pass

    # Wikilinks
    result["wikilinks"] = re.findall(r"\[\[([^\]]+)\]\]", text)

    # Sections (## headings)
    result["sections"] = re.findall(r"^##\s+(.+)$", text, re.MULTILINE)

    return result


# ---------------------------------------------------------------------------
# Lint checks
# ---------------------------------------------------------------------------

def check_orphan_pages(pages):
    """Find pages with zero inbound wikilinks."""
    inbound = defaultdict(set)
    for stem, page in pages.items():
        for link in page["wikilinks"]:
            inbound[link].add(stem)

    orphans = []
    for stem, page in pages.items():
        if stem in EXEMPT_FILES:
            continue
        # Check if any other page links to this one
        if stem not in inbound or not inbound[stem]:
            orphans.append(stem)
    return orphans


def check_broken_links(pages):
    """Find wikilinks pointing to non-existent pages."""
    all_stems = set(pages.keys())
    broken = []
    for stem, page in pages.items():
        for link in page["wikilinks"]:
            if link not in all_stems:
                broken.append((stem, link))
    return broken


def check_missing_backlinks(pages):
    """If A links to B, check if B links back to A (for source/concept pages)."""
    missing = []
    for stem, page in pages.items():
        page_type = page["frontmatter"].get("type", "")
        if page_type not in ("source", "concept", "method"):
            continue
        for link in page["wikilinks"]:
            if link in pages and link not in EXEMPT_FILES:
                other = pages[link]
                if stem not in other["wikilinks"]:
                    missing.append((stem, link))
    return missing


def check_empty_sections(pages):
    """Find pages with section headers but no content below."""
    empty = []
    for stem, page in pages.items():
        text = page["text"]
        # Find ## headers followed immediately by another ## or end of file
        matches = re.findall(r"^(##\s+.+)\n\s*\n(##|\Z)", text, re.MULTILINE)
        for header, _ in matches:
            empty.append((stem, header.strip()))
    return empty


def check_index_completeness(pages):
    """Find pages that exist on disk but are missing from index.md."""
    if "index" not in pages:
        return ["index.md not found"]

    index_links = set(pages["index"]["wikilinks"])
    missing = []
    for stem in pages:
        if stem in EXEMPT_FILES:
            continue
        if stem not in index_links:
            missing.append(stem)
    return missing


def check_neo4j_desync(pages, kg):
    """Find nodes in Neo4j without wiki pages, and pages without Neo4j nodes."""
    # Get all non-Person nodes + frequent authors (3+ papers)
    results = kg.execute_query("""
        MATCH (n) WHERE NOT n:Person RETURN n.id AS id
    """)
    graph_ids = {r["id"] for r in results if r["id"]}
    # Add frequent authors that should have wiki pages
    author_results = kg.execute_query("""
        MATCH (a:Person)<-[:AUTHORED_BY]-(p:Paper)
        WITH a.id AS id, count(p) AS papers
        WHERE papers >= 3
        RETURN id
    """)
    graph_ids.update(r["id"] for r in author_results if r["id"])

    wiki_neo4j_ids = {page["neo4j_id"] for page in pages.values()
                      if page["neo4j_id"]}

    missing_pages = sorted(graph_ids - wiki_neo4j_ids)
    missing_nodes = sorted(wiki_neo4j_ids - graph_ids)

    return missing_pages, missing_nodes


def check_stale_source_count(pages):
    """Check if concept/method pages have accurate source_count."""
    stale = []
    for stem, page in pages.items():
        fm = page["frontmatter"]
        if fm.get("type") not in ("concept", "method"):
            continue
        declared_count = fm.get("source_count")
        if declared_count is None:
            continue
        # Count source links in the page (links that point to sources/)
        source_links = [l for l in page["wikilinks"]
                        if l in pages and pages[l]["frontmatter"].get("type") == "source"]
        actual_count = len(source_links)
        if declared_count != actual_count:
            stale.append((stem, declared_count, actual_count))
    return stale


# ---------------------------------------------------------------------------
# Fix actions
# ---------------------------------------------------------------------------

def fix_index(pages):
    """Regenerate index.md to include all pages."""
    from wiki_bootstrap import generate_index
    generate_index(force=True, dry_run=False)
    logger.info("  Fixed: regenerated index.md")


def append_log(message: str):
    """Append a lint entry to log.md."""
    log_path = WIKI_ROOT / "log.md"
    if log_path.exists():
        text = log_path.read_text(encoding="utf-8")
    else:
        text = "---\ntype: log\n---\n\n# Operations Log\n"
    entry = f"\n## [{date.today().isoformat()}] lint | Wiki health-check\n\n{message}\n"
    log_path.write_text(text + entry, encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Lint the wiki")
    parser.add_argument("--fix", action="store_true", help="Auto-fix simple issues")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")

    logger.info("Scanning wiki pages...")
    pages = scan_all_pages()
    logger.info(f"  Found {len(pages)} pages")

    issues = []

    # 1. Orphan pages
    orphans = check_orphan_pages(pages)
    if orphans:
        issues.append(f"Orphan pages ({len(orphans)}):")
        for o in orphans:
            issues.append(f"  - {o}")
            if args.verbose:
                path = pages[o]["path"]
                issues.append(f"    {path.relative_to(PROJECT_ROOT)}")

    # 2. Broken links
    broken = check_broken_links(pages)
    if broken:
        issues.append(f"Broken links ({len(broken)}):")
        for src, tgt in broken:
            issues.append(f"  - {src} -> [[{tgt}]]")

    # 3. Missing backlinks
    missing_bl = check_missing_backlinks(pages)
    if missing_bl and args.verbose:
        issues.append(f"Missing backlinks ({len(missing_bl)}):")
        for src, tgt in missing_bl[:20]:
            issues.append(f"  - {src} -> {tgt} (no backlink)")

    # 4. Empty sections
    empty = check_empty_sections(pages)
    if empty and args.verbose:
        issues.append(f"Empty sections ({len(empty)}):")
        for stem, header in empty[:20]:
            issues.append(f"  - {stem}: {header}")

    # 5. Index completeness
    missing_idx = check_index_completeness(pages)
    if missing_idx:
        issues.append(f"Missing from index ({len(missing_idx)}):")
        for m in missing_idx:
            issues.append(f"  - {m}")
        if args.fix:
            fix_index(pages)

    # 6. Neo4j desync
    try:
        kg = KnowledgeGraph(
            uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            user=os.getenv("NEO4J_USER", "neo4j"),
            password=os.getenv("NEO4J_PASSWORD"),
        )
        missing_pages, missing_nodes = check_neo4j_desync(pages, kg)
        kg.close()
        if missing_pages:
            issues.append(f"Neo4j nodes without wiki pages ({len(missing_pages)}):")
            for nid in missing_pages[:20]:
                issues.append(f"  - {nid}")
        if missing_nodes:
            issues.append(f"Wiki pages without Neo4j nodes ({len(missing_nodes)}):")
            for nid in missing_nodes[:20]:
                issues.append(f"  - {nid}")
    except Exception as e:
        issues.append(f"Neo4j sync check skipped (connection failed: {e})")

    # 7. Stale source counts
    stale = check_stale_source_count(pages)
    if stale:
        issues.append(f"Stale source_count ({len(stale)}):")
        for stem, declared, actual in stale:
            issues.append(f"  - {stem}: declared={declared}, actual={actual}")

    # Report
    logger.info("\n=== Wiki Lint Report ===")
    if issues:
        for line in issues:
            logger.info(line)
        total = sum(1 for i in issues if i.startswith("  -"))
        logger.info(f"\nTotal issues: {total}")
    else:
        logger.info("No issues found!")

    # Log the lint
    summary = f"Ran wiki_lint.py: {len(issues)} issue lines reported."
    if args.fix:
        summary += " Auto-fix applied."
    append_log(summary)


if __name__ == "__main__":
    main()
