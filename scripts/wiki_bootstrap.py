"""
wiki_bootstrap.py -- One-time generation of the wiki from existing Neo4j graph + JSON data.

Usage:
    python scripts/wiki_bootstrap.py [--dry-run] [--force]
"""

import sys
sys.stdout.reconfigure(encoding="utf-8")

import argparse
import json
import logging
import re
from collections import defaultdict
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
import os

sys.path.insert(0, str(Path(__file__).parent))
from graph_db import KnowledgeGraph
from wiki_templates import (
    neo4j_id_to_wiki_path,
    neo4j_id_to_wikilink,
    render_frontmatter,
    render_page,
    slug_from_name,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WIKI_ROOT = PROJECT_ROOT / "wiki"
DATA_DIR = PROJECT_ROOT / "data"

# Author entity page threshold
AUTHOR_MIN_PAPERS = 3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_latex(s: str) -> str:
    """Strip LaTeX escapes from author names."""
    s = re.sub(r"\\[a-zA-Z'`\"^~=.]{1,2}", "", s)
    s = re.sub(r"[{}]", "", s)
    return s.strip()


def _short_title(title: str, max_len: int = 60) -> str:
    if len(title) <= max_len:
        return title
    return title[:max_len - 3] + "..."


def _first_author_year(paper: dict) -> str:
    authors = paper.get("authors", [])
    year = paper.get("year", "")
    if authors:
        first = _clean_latex(authors[0])
        last = first.split(",")[0].strip() if "," in first else first.split()[0]
        return f"{last} {year}".strip()
    return str(year)


def _write_page(path: Path, content: str, force: bool = False, dry_run: bool = False):
    if path.exists() and not force:
        logger.info(f"  SKIP (exists): {path.relative_to(PROJECT_ROOT)}")
        return False
    if dry_run:
        logger.info(f"  WOULD CREATE: {path.relative_to(PROJECT_ROOT)}")
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    logger.info(f"  CREATED: {path.relative_to(PROJECT_ROOT)}")
    return True


# ---------------------------------------------------------------------------
# Data loading from Neo4j
# ---------------------------------------------------------------------------

def load_graph_data(kg):
    """Pull all relationship data from Neo4j needed for wiki generation."""
    data = {}

    # Paper -> Concept/Method relationships
    results = kg.execute_query("""
        MATCH (p:Paper)-[r:MENTIONS|USES_TECHNOLOGY]->(c:Concept)
        RETURN p.id AS paper_id, c.id AS concept_id, c.name AS concept_name,
               type(r) AS rel_type, c.category AS category
    """)
    paper_concepts = defaultdict(list)
    concept_papers = defaultdict(list)
    for r in results:
        paper_concepts[r["paper_id"]].append({
            "concept_id": r["concept_id"],
            "name": r["concept_name"],
            "rel_type": r["rel_type"],
            "category": r["category"],
        })
        concept_papers[r["concept_id"]].append(r["paper_id"])
    data["paper_concepts"] = dict(paper_concepts)
    data["concept_papers"] = dict(concept_papers)

    # Repo -> Concept/Method relationships
    results = kg.execute_query("""
        MATCH (r:GithubRepo)-[rel:MENTIONS|USES_TECHNOLOGY]->(c:Concept)
        RETURN r.id AS repo_id, c.id AS concept_id, c.name AS concept_name,
               type(rel) AS rel_type
    """)
    repo_concepts = defaultdict(list)
    for r in results:
        repo_concepts[r["repo_id"]].append({
            "concept_id": r["concept_id"],
            "name": r["concept_name"],
            "rel_type": r["rel_type"],
        })
        concept_papers[r["concept_id"]].append(r["repo_id"])
    data["repo_concepts"] = dict(repo_concepts)

    # Paper -> Paper (RELATES_TO)
    results = kg.execute_query("""
        MATCH (p1:Paper)-[r:RELATES_TO]->(p2:Paper)
        RETURN p1.id AS from_id, p2.id AS to_id, r.similarity_score AS score
    """)
    paper_relations = defaultdict(list)
    for r in results:
        paper_relations[r["from_id"]].append({"id": r["to_id"], "score": r["score"] or 0})
        paper_relations[r["to_id"]].append({"id": r["from_id"], "score": r["score"] or 0})
    data["paper_relations"] = dict(paper_relations)

    # Paper -> Person (AUTHORED_BY)
    results = kg.execute_query("""
        MATCH (p:Paper)-[:AUTHORED_BY]->(a:Person)
        RETURN p.id AS paper_id, a.id AS author_id, a.name AS author_name
    """)
    paper_authors = defaultdict(list)
    author_papers = defaultdict(list)
    for r in results:
        paper_authors[r["paper_id"]].append({
            "id": r["author_id"],
            "name": _clean_latex(r["author_name"] or r["author_id"]),
        })
        author_papers[r["author_id"]].append(r["paper_id"])
    data["paper_authors"] = dict(paper_authors)
    data["author_papers"] = dict(author_papers)

    # Repo -> Paper (IMPLEMENTS)
    results = kg.execute_query("""
        MATCH (r:GithubRepo)-[rel:IMPLEMENTS]->(p:Paper)
        RETURN r.id AS repo_id, p.id AS paper_id
    """)
    repo_implements = defaultdict(list)
    paper_repos = defaultdict(list)
    for r in results:
        repo_implements[r["repo_id"]].append(r["paper_id"])
        paper_repos[r["paper_id"]].append(r["repo_id"])
    data["repo_implements"] = dict(repo_implements)
    data["paper_repos"] = dict(paper_repos)

    # Paper -> Paper (CITES)
    results = kg.execute_query("""
        MATCH (p1:Paper)-[:CITES]->(p2:Paper)
        RETURN p1.id AS from_id, p2.id AS to_id
    """)
    paper_cites = defaultdict(list)
    for r in results:
        paper_cites[r["from_id"]].append(r["to_id"])
    data["paper_cites"] = dict(paper_cites)

    # All concept nodes
    results = kg.execute_query("""
        MATCH (c:Concept) RETURN c.id AS id, c.name AS name, c.category AS category
    """)
    data["concepts"] = {r["id"]: {"name": r["name"], "category": r["category"]} for r in results}

    return data


# ---------------------------------------------------------------------------
# Page generators
# ---------------------------------------------------------------------------

def generate_paper_pages(papers, graph_data, force, dry_run):
    """Generate wiki/sources/{key}.md for each paper."""
    count = 0
    for paper in papers:
        pid = paper["id"]
        path = neo4j_id_to_wiki_path(pid, WIKI_ROOT)

        # Concepts and methods from graph
        concepts_raw = graph_data.get("paper_concepts", {}).get(pid, [])
        concepts = [{"link": neo4j_id_to_wikilink(c["concept_id"]), "rel_type": c["rel_type"]}
                     for c in concepts_raw if c["rel_type"] == "MENTIONS"]
        methods = [{"link": neo4j_id_to_wikilink(c["concept_id"]), "name": c["name"]}
                    for c in concepts_raw if c["rel_type"] == "USES_TECHNOLOGY"]

        # Related papers
        related = graph_data.get("paper_relations", {}).get(pid, [])
        related_papers = [{"link": neo4j_id_to_wikilink(r["id"]), "score": f'{r["score"]:.2f}'}
                          for r in related]

        # Repos that implement this paper
        implements_repos = [neo4j_id_to_wikilink(r) for r in graph_data.get("paper_repos", {}).get(pid, [])]

        # Tags from concept slugs
        tags = list(set(
            neo4j_id_to_wikilink(c["concept_id"]) for c in concepts_raw
        ))

        # Authors -- wikilink frequent authors
        authors_raw = graph_data.get("paper_authors", {}).get(pid, [])
        frequent = {aid for aid, plist in graph_data.get("author_papers", {}).items()
                    if len(plist) >= AUTHOR_MIN_PAPERS}
        authors_display = []
        for a in authors_raw:
            if a["id"] in frequent:
                authors_display.append(f'[[{slug_from_name(a["name"])}]]')
            else:
                authors_display.append(a["name"])

        frontmatter = render_frontmatter({
            "type": "source",
            "source_type": "paper",
            "neo4j_id": pid,
            "title": paper.get("title", pid),
            "authors": authors_display[:5],  # keep frontmatter concise
            "year": paper.get("year", ""),
            "venue": paper.get("venue", ""),
            "tags": tags[:10],
        })

        abstract = paper.get("abstract", "[Abstract not available]")
        if not abstract or abstract.strip() == "":
            abstract = "[Abstract not available -- add via wiki_ingest or manually]"

        content = render_page("source_paper", {
            "frontmatter": frontmatter,
            "title": paper.get("title", pid),
            "abstract": abstract,
            "findings": [],  # to be filled by LLM later
            "methods": methods,
            "related_papers": related_papers,
            "concepts": concepts,
            "implements": implements_repos,
        })

        if _write_page(path, content, force, dry_run):
            count += 1
    return count


def generate_repo_pages(repos, graph_data, force, dry_run):
    """Generate wiki/sources/{repo-name}.md for each repo."""
    count = 0
    for repo in repos:
        rid = repo["id"]
        path = neo4j_id_to_wiki_path(rid, WIKI_ROOT)

        concepts_raw = graph_data.get("repo_concepts", {}).get(rid, [])
        methods = [{"link": neo4j_id_to_wikilink(c["concept_id"]), "name": c["name"]}
                    for c in concepts_raw if c["rel_type"] == "USES_TECHNOLOGY"]
        concepts = [{"link": neo4j_id_to_wikilink(c["concept_id"])}
                     for c in concepts_raw if c["rel_type"] == "MENTIONS"]

        implements_raw = graph_data.get("repo_implements", {}).get(rid, [])
        implements = [{"link": neo4j_id_to_wikilink(pid), "note": "Analysis code"}
                       for pid in implements_raw]

        tags = list(set(neo4j_id_to_wikilink(c["concept_id"]) for c in concepts_raw))

        frontmatter = render_frontmatter({
            "type": "source",
            "source_type": "repo",
            "neo4j_id": rid,
            "name": repo.get("name", rid.split("/")[-1]),
            "url": repo.get("url", f"https://github.com/{rid}"),
            "language": repo.get("language", ""),
            "tags": tags,
        })

        content = render_page("source_repo", {
            "frontmatter": frontmatter,
            "name": repo.get("name", rid.split("/")[-1]),
            "description": repo.get("description", "[No description]"),
            "implements": implements,
            "methods": methods,
            "concepts": concepts,
        })

        if _write_page(path, content, force, dry_run):
            count += 1
    return count


def generate_concept_pages(graph_data, papers_by_id, force, dry_run):
    """Generate concept pages (disease, biology, broad topic)."""
    count = 0
    concepts = graph_data["concepts"]
    concept_papers = graph_data.get("concept_papers", {})

    for cid, cinfo in concepts.items():
        category = cinfo.get("category", "")
        # Method pages handled separately
        if category == "method":
            continue

        path = neo4j_id_to_wiki_path(cid, WIKI_ROOT)
        name = cinfo["name"] or cid.split(":")[-1].replace("_", " ").title()

        # Sources that mention this concept
        source_ids = list(set(concept_papers.get(cid, [])))
        sources = []
        for sid in source_ids:
            p = papers_by_id.get(sid)
            if p:
                sources.append({
                    "link": neo4j_id_to_wikilink(sid),
                    "summary": _short_title(p.get("title", ""), 50),
                    "year": p.get("year", ""),
                })
            else:
                # repo
                sources.append({
                    "link": neo4j_id_to_wikilink(sid),
                    "summary": "GitHub repository",
                    "year": "",
                })

        # Related concepts: other concepts that share papers with this one
        related_slugs = set()
        for sid in source_ids:
            for c in graph_data.get("paper_concepts", {}).get(sid, []):
                if c["concept_id"] != cid:
                    related_slugs.add(neo4j_id_to_wikilink(c["concept_id"]))
        # Limit to top 8
        related = sorted(related_slugs)[:8]

        # Methods used across sources
        methods_set = set()
        for sid in source_ids:
            for c in graph_data.get("paper_concepts", {}).get(sid, []):
                if c["rel_type"] == "USES_TECHNOLOGY":
                    methods_set.add(neo4j_id_to_wikilink(c["concept_id"]))

        overview = f"{name} is a recurring theme across {len(sources)} source(s) in this knowledge base."
        if category == "disease":
            overview = f"{name} is a disease/condition studied across {len(sources)} paper(s) in this research program."
        elif category == "biology":
            overview = f"{name} is a biological concept central to {len(sources)} paper(s) in this research program."

        aliases = []
        frontmatter = render_frontmatter({
            "type": "concept",
            "category": category or "topic",
            "neo4j_id": cid,
            "name": name,
            "aliases": aliases,
            "tags": [slug_from_name(name)],
            "source_count": len(sources),
        })

        content = render_page("concept", {
            "frontmatter": frontmatter,
            "name": name,
            "overview": overview,
            "sources": sources,
            "methods": sorted(methods_set)[:10],
            "related": related,
        })

        if _write_page(path, content, force, dry_run):
            count += 1
    return count


def generate_method_pages(graph_data, papers_by_id, force, dry_run):
    """Generate wiki/methods/{slug}.md for each method concept."""
    count = 0
    concepts = graph_data["concepts"]
    concept_papers = graph_data.get("concept_papers", {})

    for cid, cinfo in concepts.items():
        if cinfo.get("category") != "method":
            continue

        path = neo4j_id_to_wiki_path(cid, WIKI_ROOT)
        name = cinfo["name"] or cid.split(":")[-1].replace("_", " ").title()

        source_ids = list(set(concept_papers.get(cid, [])))
        sources = []
        for sid in source_ids:
            p = papers_by_id.get(sid)
            if p:
                sources.append({
                    "link": neo4j_id_to_wikilink(sid),
                    "summary": _short_title(p.get("title", ""), 50),
                    "year": p.get("year", ""),
                })
            else:
                sources.append({
                    "link": neo4j_id_to_wikilink(sid),
                    "summary": "GitHub repository",
                    "year": "",
                })

        # Related methods: other methods used by the same papers
        related_set = set()
        for sid in source_ids:
            for c in graph_data.get("paper_concepts", {}).get(sid, []):
                if c["rel_type"] == "USES_TECHNOLOGY" and c["concept_id"] != cid:
                    related_set.add(neo4j_id_to_wikilink(c["concept_id"]))

        description = f"{name} is an analytical method/technology used in {len(sources)} source(s)."

        frontmatter = render_frontmatter({
            "type": "method",
            "neo4j_id": cid,
            "name": name,
            "aliases": [],
            "tags": ["method", slug_from_name(name)],
            "source_count": len(sources),
        })

        content = render_page("method", {
            "frontmatter": frontmatter,
            "name": name,
            "description": description,
            "sources": sources,
            "related_methods": sorted(related_set)[:8],
        })

        if _write_page(path, content, force, dry_run):
            count += 1
    return count


def generate_entity_pages(graph_data, papers_by_id, force, dry_run):
    """Generate entity pages for cohorts and frequent authors."""
    count = 0
    concepts = graph_data["concepts"]

    # Cohort entities
    for cid, cinfo in concepts.items():
        if not cid.startswith("concept:cohort:"):
            continue
        path = neo4j_id_to_wiki_path(cid, WIKI_ROOT)
        name = cinfo["name"] or cid.split(":")[-1].replace("_", " ").title()

        source_ids = list(set(graph_data.get("concept_papers", {}).get(cid, [])))
        sources = []
        for sid in source_ids:
            p = papers_by_id.get(sid)
            sources.append({
                "link": neo4j_id_to_wikilink(sid),
                "year": p.get("year", "") if p else "",
            })

        related = []
        for sid in source_ids:
            for c in graph_data.get("paper_concepts", {}).get(sid, []):
                if c["concept_id"] != cid:
                    link = neo4j_id_to_wikilink(c["concept_id"])
                    if link not in related:
                        related.append(link)

        frontmatter = render_frontmatter({
            "type": "entity",
            "entity_type": "cohort",
            "neo4j_id": cid,
            "name": name,
            "tags": [slug_from_name(name)],
        })

        content = render_page("entity", {
            "frontmatter": frontmatter,
            "name": name,
            "description": f"{name} is a research cohort/study referenced in {len(sources)} paper(s).",
            "sources": sources,
            "related": related[:8],
        })

        if _write_page(path, content, force, dry_run):
            count += 1

    # Frequent author entities
    author_papers = graph_data.get("author_papers", {})
    for aid, paper_ids in author_papers.items():
        if len(paper_ids) < AUTHOR_MIN_PAPERS:
            continue
        # Find clean name
        name = _clean_latex(aid.replace("_", " ").replace(",", ", "))
        # Try to get a better name from the graph data
        for pid in paper_ids:
            for a in graph_data.get("paper_authors", {}).get(pid, []):
                if a["id"] == aid:
                    name = a["name"]
                    break

        slug = slug_from_name(name)
        path = WIKI_ROOT / "entities" / f"{slug}.md"

        sources = []
        for pid in paper_ids:
            p = papers_by_id.get(pid)
            if p:
                sources.append({
                    "link": neo4j_id_to_wikilink(pid),
                    "year": p.get("year", ""),
                })

        frontmatter = render_frontmatter({
            "type": "entity",
            "entity_type": "person",
            "neo4j_id": aid,
            "name": name,
            "tags": ["author"],
        })

        content = render_page("entity", {
            "frontmatter": frontmatter,
            "name": name,
            "description": f"Co-author appearing in {len(paper_ids)} paper(s) in this knowledge base.",
            "sources": sources,
            "related": [],
        })

        if _write_page(path, content, force, dry_run):
            count += 1

    return count


def generate_overview(papers, repos, graph_data, force, dry_run):
    """Generate wiki/overview.md -- research identity page."""
    path = WIKI_ROOT / "overview.md"

    # Build expertise areas from concept frequency
    concept_papers = graph_data.get("concept_papers", {})
    concepts = graph_data["concepts"]

    # Group concepts by category, sorted by paper count
    areas = []
    area_defs = [
        ("Core Methodologies", "method"),
        ("Diseases & Conditions", "disease"),
        ("Biological Themes", "biology"),
        ("Research Topics", None),  # broad topics
    ]
    papers_by_id = {p["id"]: p for p in papers}

    for area_name, cat in area_defs:
        relevant = []
        for cid, cinfo in concepts.items():
            c_cat = cinfo.get("category", "")
            if cat and c_cat != cat:
                continue
            if not cat and c_cat in ("method", "disease", "biology"):
                continue  # broad topics only
            paper_count = len(set(concept_papers.get(cid, [])))
            if paper_count > 0:
                relevant.append((cinfo["name"], paper_count, cid))
        relevant.sort(key=lambda x: -x[1])
        if relevant:
            top_names = [f"{n} ({c})" for n, c, _ in relevant[:6]]
            # Papers for this area = union of all papers touching top concepts
            area_paper_ids = set()
            for _, _, cid in relevant[:6]:
                area_paper_ids.update(concept_papers.get(cid, []))
            area_paper_links = [neo4j_id_to_wikilink(pid) for pid in area_paper_ids
                                if pid in papers_by_id][:5]
            areas.append({
                "name": area_name,
                "description": f"Key areas: {', '.join(top_names)}.",
                "papers": area_paper_links,
            })

    # Repos
    repo_entries = []
    for repo in repos:
        repo_entries.append({
            "link": neo4j_id_to_wikilink(repo["id"]),
            "description": repo.get("description", ""),
        })

    # Timeline
    timeline = defaultdict(list)
    for p in sorted(papers, key=lambda x: x.get("year", 0), reverse=True):
        year = p.get("year", "Unknown")
        timeline[year].append({
            "link": neo4j_id_to_wikilink(p["id"]),
            "title_short": _short_title(p.get("title", p["id"]), 50),
        })

    content = render_page("overview", {
        "areas": areas,
        "repos": repo_entries,
        "timeline": dict(timeline),
    })

    if _write_page(path, content, force, dry_run):
        return 1
    return 0


def generate_index(force, dry_run):
    """Generate wiki/index.md from all existing wiki pages."""
    path = WIKI_ROOT / "index.md"

    sections = {
        "sources": [],
        "concepts": [],
        "methods": [],
        "entities": [],
        "synthesis": [],
    }

    for subdir, section_key in [
        ("sources", "sources"),
        ("concepts", "concepts"),
        ("methods", "methods"),
        ("entities", "entities"),
        ("synthesis", "synthesis"),
    ]:
        dir_path = WIKI_ROOT / subdir
        if not dir_path.exists():
            continue
        for md_file in sorted(dir_path.glob("*.md")):
            link = md_file.stem
            # Read frontmatter for one-line summary
            try:
                text = md_file.read_text(encoding="utf-8")
                # Extract title from frontmatter or first heading
                title_match = re.search(r'^title:\s*"?(.+?)"?\s*$', text, re.MULTILINE)
                name_match = re.search(r'^name:\s*"?(.+?)"?\s*$', text, re.MULTILINE)
                year_match = re.search(r'^year:\s*(\d+)', text, re.MULTILINE)
                display = title_match.group(1) if title_match else (name_match.group(1) if name_match else link)
                year = year_match.group(1) if year_match else ""
                sections[section_key].append((link, _short_title(display, 60), year))
            except Exception:
                sections[section_key].append((link, link, ""))

    lines = [
        "---",
        'type: "index"',
        f'last_updated: "{date.today().isoformat()}"',
        "---",
        "",
        "# Wiki Index",
        "",
    ]

    # Sources
    lines.append("## Sources")
    lines.append("")
    papers = [s for s in sections["sources"]]
    if papers:
        lines.append("| Page | Title | Year |")
        lines.append("|------|-------|------|")
        for link, title, year in papers:
            lines.append(f"| [[{link}]] | {title} | {year} |")
    lines.append("")

    # Concepts
    lines.append("## Concepts")
    lines.append("")
    for link, title, _ in sections["concepts"]:
        lines.append(f"- [[{link}]] -- {title}")
    lines.append("")

    # Methods
    lines.append("## Methods")
    lines.append("")
    for link, title, _ in sections["methods"]:
        lines.append(f"- [[{link}]] -- {title}")
    lines.append("")

    # Entities
    lines.append("## Entities")
    lines.append("")
    for link, title, _ in sections["entities"]:
        lines.append(f"- [[{link}]] -- {title}")
    lines.append("")

    # Synthesis
    lines.append("## Synthesis")
    lines.append("")
    if sections["synthesis"]:
        for link, title, _ in sections["synthesis"]:
            lines.append(f"- [[{link}]] -- {title}")
    else:
        lines.append("*No synthesis pages yet. Ask questions and file valuable answers here.*")
    lines.append("")

    content = "\n".join(lines)
    if _write_page(path, content, force=True, dry_run=dry_run):  # always overwrite index
        return 1
    return 0


def generate_log(total_pages, dry_run):
    """Generate initial wiki/log.md."""
    path = WIKI_ROOT / "log.md"
    content = f"""---
type: "log"
---

# Operations Log

## [{date.today().isoformat()}] bootstrap | Initial wiki generated from Neo4j graph

- Generated {total_pages} wiki pages from existing Neo4j knowledge graph
- Source: 17 papers, 5 repos, 59 concepts, 110 person nodes
- Pipeline: wiki_bootstrap.py
"""
    if dry_run:
        logger.info(f"  WOULD CREATE: {path.relative_to(PROJECT_ROOT)}")
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    logger.info(f"  CREATED: {path.relative_to(PROJECT_ROOT)}")
    return 1


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Bootstrap wiki from Neo4j graph")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be created")
    parser.add_argument("--force", action="store_true", help="Overwrite existing pages")
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env", override=True)

    # Load JSON data
    with open(DATA_DIR / "papers.json", encoding="utf-8") as f:
        papers = json.load(f)
    with open(DATA_DIR / "github_repos.json", encoding="utf-8") as f:
        repos = json.load(f)

    papers_by_id = {p["id"]: p for p in papers}

    logger.info("Connecting to Neo4j...")
    kg = KnowledgeGraph(
        uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        user=os.getenv("NEO4J_USER", "neo4j"),
        password=os.getenv("NEO4J_PASSWORD"),
    )

    logger.info("Loading graph data...")
    graph_data = load_graph_data(kg)
    kg.close()

    total = 0
    mode = "DRY RUN" if args.dry_run else "GENERATING"

    logger.info(f"\n=== {mode}: Paper source pages ===")
    total += generate_paper_pages(papers, graph_data, args.force, args.dry_run)

    logger.info(f"\n=== {mode}: Repo source pages ===")
    total += generate_repo_pages(repos, graph_data, args.force, args.dry_run)

    logger.info(f"\n=== {mode}: Concept pages (disease/biology/topic) ===")
    total += generate_concept_pages(graph_data, papers_by_id, args.force, args.dry_run)

    logger.info(f"\n=== {mode}: Method pages ===")
    total += generate_method_pages(graph_data, papers_by_id, args.force, args.dry_run)

    logger.info(f"\n=== {mode}: Entity pages (cohorts + frequent authors) ===")
    total += generate_entity_pages(graph_data, papers_by_id, args.force, args.dry_run)

    logger.info(f"\n=== {mode}: Overview page ===")
    total += generate_overview(papers, repos, graph_data, args.force, args.dry_run)

    logger.info(f"\n=== {mode}: Index ===")
    total += generate_index(args.force, args.dry_run)

    logger.info(f"\n=== {mode}: Log ===")
    total += generate_log(total, args.dry_run)

    logger.info(f"\n{'Would create' if args.dry_run else 'Created'} {total} wiki pages total.")


if __name__ == "__main__":
    main()
