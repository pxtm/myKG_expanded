"""
render_svg.py -- Render a static SVG snapshot of a graph.

Two input modes:
  1. Pyvis HTML (default) -- parse nodes/edges from any pyvis-generated HTML
     and render a matching static SVG. Preserves colors, sizes, labels.
  2. Neo4j (--from-neo4j) -- pull the live graph from the database.

SVG output is safe for GitHub READMEs (no scripts, no foreignObject) and
scales cleanly at any zoom.

Usage:
    # Pyvis HTML -- most common
    python scripts/render_svg.py visualizations/knowledge_graph.html
    python scripts/render_svg.py visualizations/expertise_map.html \
        --layout kamada --label-top 30

    # Explicit output path
    python scripts/render_svg.py visualizations/knowledge_graph.html \
        --out visualizations/preview.svg

    # Pull fresh from Neo4j instead
    python scripts/render_svg.py --from-neo4j --out visualizations/knowledge_graph.svg
"""

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import List, Tuple

import matplotlib
matplotlib.use("svg")
import matplotlib.pyplot as plt
import networkx as nx

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Fallback palette used when a node has no color (Neo4j mode, or malformed HTML)
TYPE_COLORS = {
    "paper":       "#FF6B6B",
    "github_repo": "#4ECDC4",
    "repo":        "#4ECDC4",
    "person":      "#AA96DA",
    "concept":     "#F38181",
    "note":        "#FFEAA7",
}
DEFAULT_COLOR = "#AAAAAA"
EDGE_COLOR = "#3a3f4b"


# ---------------------------------------------------------------------------
# Input: pyvis HTML
# ---------------------------------------------------------------------------

_NODES_RE = re.compile(
    r"nodes\s*=\s*new\s+vis\.DataSet\(\s*(\[.*?\])\s*\)\s*;",
    re.DOTALL,
)
_EDGES_RE = re.compile(
    r"edges\s*=\s*new\s+vis\.DataSet\(\s*(\[.*?\])\s*\)\s*;",
    re.DOTALL,
)
_TYPE_RE = re.compile(r"Type:\s*([a-zA-Z_]+)")


def _extract_type(title: str) -> str:
    """Pyvis titles include 'Type: paper' lines -- pull that out."""
    if not title:
        return "unknown"
    m = _TYPE_RE.search(title)
    return m.group(1).lower() if m else "unknown"


def parse_pyvis_html(path: Path) -> Tuple[List[dict], List[dict]]:
    text = path.read_text(encoding="utf-8")
    nm = _NODES_RE.search(text)
    em = _EDGES_RE.search(text)
    if not nm or not em:
        raise ValueError(
            f"{path.name}: could not locate pyvis `nodes`/`edges` vis.DataSet "
            "declarations. Is this a pyvis-generated HTML file?"
        )
    nodes_raw = json.loads(nm.group(1))
    edges_raw = json.loads(em.group(1))

    nodes = []
    for n in nodes_raw:
        nodes.append({
            "id":    n.get("id"),
            "label": n.get("label") or n.get("id") or "",
            "color": n.get("color") or DEFAULT_COLOR,
            "size":  float(n.get("size", 12)),
            "type":  _extract_type(n.get("title", "")),
        })
    edges = []
    for e in edges_raw:
        edges.append({
            "src":   e.get("from"),
            "dst":   e.get("to"),
            "color": e.get("color") or EDGE_COLOR,
        })
    return nodes, edges


# ---------------------------------------------------------------------------
# Input: Neo4j
# ---------------------------------------------------------------------------

def fetch_from_neo4j() -> Tuple[List[dict], List[dict]]:
    from dotenv import load_dotenv
    sys.path.insert(0, str(Path(__file__).parent))
    from graph_db import KnowledgeGraph

    load_dotenv(PROJECT_ROOT / ".env", override=True)
    kg = KnowledgeGraph(
        uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        user=os.getenv("NEO4J_USER", "neo4j"),
        password=os.getenv("NEO4J_PASSWORD"),
    )
    try:
        nodes_q = """
        MATCH (n)
        RETURN labels(n)[0] AS label, n.id AS id,
               coalesce(n.name, n.title, n.id) AS name
        """
        edges_q = """
        MATCH (a)-[r]->(b)
        WHERE a.id IS NOT NULL AND b.id IS NOT NULL
        RETURN a.id AS src, b.id AS dst
        """
        raw_nodes = kg.execute_query(nodes_q)
        raw_edges = kg.execute_query(edges_q)
    finally:
        kg.close()

    nodes = []
    for n in raw_nodes:
        if not n.get("id"):
            continue
        t = (n.get("label") or "unknown").lower()
        nodes.append({
            "id":    n["id"],
            "label": n.get("name") or n["id"],
            "color": TYPE_COLORS.get(t, DEFAULT_COLOR),
            "size":  18 if t == "paper" else (16 if "repo" in t else 10),
            "type":  t,
        })
    edges = [{"src": e["src"], "dst": e["dst"], "color": EDGE_COLOR}
             for e in raw_edges]
    return nodes, edges


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def build_nx(nodes, edges, min_degree: int) -> nx.Graph:
    g = nx.Graph()
    for n in nodes:
        if n["id"] is None:
            continue
        g.add_node(n["id"], **n)
    for e in edges:
        if e["src"] in g and e["dst"] in g:
            g.add_edge(e["src"], e["dst"], color=e["color"])
    if min_degree > 0:
        keep = [n for n, d in g.degree() if d >= min_degree]
        g = g.subgraph(keep).copy()
    return g


def pick_layout(g: nx.Graph, name: str):
    if name == "spring":
        return nx.spring_layout(g, k=0.45, iterations=80, seed=42)
    if name == "kamada":
        return nx.kamada_kawai_layout(g)
    if name == "circular":
        return nx.circular_layout(g)
    raise ValueError(f"unknown layout: {name}")


def render(g: nx.Graph, pos, out_path: Path, label_top: int, title: str = ""):
    fig, ax = plt.subplots(figsize=(14, 10), facecolor="#0f1115")
    ax.set_facecolor("#0f1115")
    ax.set_axis_off()
    if title:
        ax.set_title(title, color="#e7ecf3", fontsize=12, pad=14, loc="left")

    nx.draw_networkx_edges(
        g, pos, ax=ax, edge_color=EDGE_COLOR, width=0.6, alpha=0.55
    )

    # Group nodes by color so vis.js-style palette is preserved
    by_color = {}
    for n, d in g.nodes(data=True):
        by_color.setdefault(d["color"], []).append((n, d["size"]))
    for color, items in by_color.items():
        nodelist = [n for n, _ in items]
        sizes = [s * 15 for _, s in items]  # scale vis.js size → matplotlib
        nx.draw_networkx_nodes(
            g, pos, nodelist=nodelist, ax=ax,
            node_color=color, node_size=sizes,
            edgecolors="#0f1115", linewidths=0.8, alpha=0.95,
        )

    # Label only the top-N by degree
    top = sorted(g.degree, key=lambda x: x[1], reverse=True)[:label_top]
    labels = {n: (g.nodes[n]["label"] or n)[:36] for n, _ in top}
    nx.draw_networkx_labels(
        g, pos, labels=labels, ax=ax,
        font_size=7, font_color="#e7ecf3",
    )

    # Legend: distinct (color, type) pairs actually present
    seen = {}
    for _, d in g.nodes(data=True):
        seen.setdefault(d["color"], d["type"])
    handles = [
        plt.Line2D([0], [0], marker="o", linestyle="", markersize=9,
                   markerfacecolor=c, markeredgecolor="#0f1115", label=t)
        for c, t in seen.items()
    ]
    leg = ax.legend(
        handles=handles, loc="lower left", frameon=True,
        facecolor="#161922", edgecolor="#262b36", fontsize=9,
    )
    for text in leg.get_texts():
        text.set_color("#e7ecf3")

    fig.tight_layout(pad=0.5)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, format="svg",
                facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    logger.info(
        f"Wrote {out_path} ({g.number_of_nodes()} nodes, "
        f"{g.number_of_edges()} edges)"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Render a pyvis HTML graph (or live Neo4j graph) as SVG.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "html", nargs="?", type=Path,
        help="Path to a pyvis-generated HTML file. Omit with --from-neo4j.",
    )
    parser.add_argument("--from-neo4j", action="store_true",
                        help="Pull live graph from Neo4j instead of parsing HTML.")
    parser.add_argument("--out", type=Path,
                        help="Output SVG path (default: same dir/basename as input, .svg).")
    parser.add_argument("--layout", choices=["spring", "kamada", "circular"],
                        default="kamada")
    parser.add_argument("--min-degree", type=int, default=0,
                        help="Drop nodes with degree < N (default 0 = keep all).")
    parser.add_argument("--label-top", type=int, default=25,
                        help="Label the top-N nodes by degree.")
    parser.add_argument("--title", default="",
                        help="Optional title rendered in the SVG.")
    args = parser.parse_args()

    if args.from_neo4j:
        if args.html:
            parser.error("Pass either an HTML path OR --from-neo4j, not both.")
        nodes, edges = fetch_from_neo4j()
        default_out = PROJECT_ROOT / "visualizations" / "knowledge_graph.svg"
    else:
        if not args.html:
            parser.error("Provide an HTML path, or use --from-neo4j.")
        if not args.html.exists():
            parser.error(f"File not found: {args.html}")
        nodes, edges = parse_pyvis_html(args.html)
        default_out = args.html.with_suffix(".svg")

    out_path = args.out or default_out

    g = build_nx(nodes, edges, args.min_degree)
    if g.number_of_nodes() == 0:
        logger.error("Graph is empty after filtering — nothing to render.")
        sys.exit(1)

    pos = pick_layout(g, args.layout)
    render(g, pos, out_path, args.label_top, title=args.title)


if __name__ == "__main__":
    main()
