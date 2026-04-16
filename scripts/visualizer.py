"""
Interactive visualization of the knowledge graph.
Creates HTML visualizations using pyvis.
"""

from collections import defaultdict
from pyvis.network import Network
import networkx as nx
from typing import Dict, List, Optional
import logging
from pathlib import Path
import yaml

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Edge styles: color + per-edge vis.js spring length ────────────────────────
EDGE_STYLES = {
    "AUTHORED_BY":      {"color": "#AA96DA", "length": 80},    # purple, short → authors near papers
    "MENTIONS":         {"color": "#F38181", "length": 160},    # red-pink
    "USES_TECHNOLOGY":  {"color": "#45B7D1", "length": 160},    # blue
    "RELATES_TO":       {"color": "#96CEB4", "length": 280},    # green, long → papers spread out
    "CITES":            {"color": "#FFEAA7", "length": 220},    # yellow
    "IMPLEMENTS":       {"color": "#4ECDC4", "length": 180},    # teal
}
_DEFAULT_EDGE = {"color": "#AAAAAA", "length": 150}

# ── Base node sizes by type (boosted by degree in visualize_from_neo4j) ───────
_BASE_SIZES = {
    "paper":       18,
    "github_repo": 16,
    "concept":     11,
    "person":       7,
    "note":        10,
    "unknown":      8,
}

# ── Legend HTML injected into the saved file ──────────────────────────────────
_LEGEND_HTML = """
<div id="kg-legend" style="position:fixed;top:10px;right:10px;
  background:rgba(255,255,255,0.93);padding:10px 14px;border-radius:8px;
  font-family:Arial,sans-serif;font-size:12px;line-height:1.9;
  box-shadow:0 2px 10px rgba(0,0,0,0.22);z-index:9999;min-width:220px">
  <b style="font-size:13px;display:block;margin-bottom:2px">Nodes</b>
  <span style="color:#FF6B6B;font-size:16px">&#9679;</span> Paper &nbsp;
  <span style="color:#4ECDC4;font-size:16px">&#9679;</span> Repo &nbsp;
  <span style="color:#AA96DA;font-size:16px">&#9679;</span> Person &nbsp;
  <span style="color:#F38181;font-size:16px">&#9679;</span> Concept
  <br><b style="font-size:13px;display:block;margin-top:4px;margin-bottom:2px">Edges</b>
  <span style="color:#AA96DA;font-weight:bold">&#9135;</span> AUTHORED_BY &nbsp;
  <span style="color:#F38181;font-weight:bold">&#9135;</span> MENTIONS<br>
  <span style="color:#45B7D1;font-weight:bold">&#9135;</span> USES_TECHNOLOGY &nbsp;
  <span style="color:#4ECDC4;font-weight:bold">&#9135;</span> IMPLEMENTS<br>
  <span style="color:#96CEB4;font-weight:bold">&#9135;</span> RELATES_TO &nbsp;
  <span style="color:#ccc;font-weight:bold">&#9135;</span> CITES
</div>
"""


class GraphVisualizer:
    """Visualize the knowledge graph interactively."""
    
    def __init__(self, config_path: str = "config/schema.yaml"):
        """Initialize visualizer with schema config."""
        with open(config_path, 'r') as f:
            self.config = yaml.safe_load(f)
        
        self.node_colors = {
            node_type: props['color'] 
            for node_type, props in self.config['node_types'].items()
        }
    
    def create_visualization(self, nodes: List[Dict], relationships: List[Dict],
                           output_path: str = "visualizations/knowledge_graph.html",
                           height: str = "750px", width: str = "100%",
                           notebook: bool = False):
        """
        Create an interactive HTML visualization.
        
        Args:
            nodes: List of node dicts with 'id', 'label', and 'type'
            relationships: List of relationship dicts with 'from', 'to', and 'type'
            output_path: Where to save the HTML file
            height: Height of the visualization
            width: Width of the visualization
            notebook: Whether to display in Jupyter notebook
        """
        net = Network(height=height, width=width, notebook=notebook, directed=True)
        
        # Configure physics — BarnesHut with higher gravity keeps the graph compact
        net.set_options("""
        {
          "physics": {
            "barnesHut": {
              "gravitationalConstant": -6000,
              "centralGravity": 0.25,
              "springLength": 180,
              "springConstant": 0.04,
              "damping": 0.12,
              "avoidOverlap": 0.5
            },
            "maxVelocity": 60,
            "solver": "barnesHut",
            "timestep": 0.4,
            "stabilization": {"iterations": 250, "updateInterval": 25}
          },
          "interaction": {
            "hover": true,
            "tooltipDelay": 100
          },
          "edges": {
            "smooth": {"type": "continuous"}
          }
        }
        """)
        
        # Add nodes
        for node in nodes:
            node_id = node['id']
            label = node.get('label', node_id)
            node_type = node.get('type', 'unknown')
            
            # Get color from schema
            color = self.node_colors.get(node_type, '#CCCCCC')
            
            # Create title (hover tooltip)
            title = self._create_node_tooltip(node)
            
            # Determine size based on connections or importance
            size = node.get('size', 10)
            
            net.add_node(
                node_id,
                label=label,
                title=title,
                color=color,
                size=size,
                shape='dot'
            )
        
        # Add edges
        for rel in relationships:
            from_id = rel['from']
            to_id = rel['to']
            rel_type = rel.get('type', 'RELATES_TO')

            # Create edge title
            title = rel_type
            if 'similarity_score' in rel:
                title += f" (similarity: {rel['similarity_score']:.2f})"

            style = EDGE_STYLES.get(rel_type, _DEFAULT_EDGE)
            net.add_edge(
                from_id,
                to_id,
                title=title,
                label=rel_type if rel.get('show_label', False) else '',
                arrows='to',
                color=style["color"],
                length=style["length"],
            )

        # Save to file
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        net.save_graph(str(output_file))

        # Inject legend into the HTML
        html = output_file.read_text(encoding="utf-8")
        html = html.replace("</body>", _LEGEND_HTML + "</body>")
        output_file.write_text(html, encoding="utf-8")

        logger.info(f"Visualization saved to {output_path}")
        return output_file
    
    def _create_node_tooltip(self, node: Dict) -> str:
        """Create HTML tooltip for a node."""
        lines = [f"<b>{node.get('label', node['id'])}</b>"]
        
        if 'type' in node:
            lines.append(f"Type: {node['type']}")
        
        # Add type-specific info
        if 'authors' in node:
            authors = node['authors'][:3]
            lines.append(f"Authors: {', '.join(authors)}")
        
        if 'year' in node:
            lines.append(f"Year: {node['year']}")
        
        if 'language' in node:
            lines.append(f"Language: {node['language']}")
        
        if 'stars' in node:
            lines.append(f"Stars: {node['stars']}")
        
        if 'tags' in node:
            tags = node['tags'][:5]
            if tags:
                lines.append(f"Tags: {', '.join(tags)}")
        
        return '<br>'.join(lines)
    
    # Map Neo4j labels to schema type keys
    _LABEL_TO_TYPE = {
        'Paper': 'paper',
        'GithubRepo': 'github_repo',
        'Note': 'note',
        'Concept': 'concept',
        'Person': 'person',
    }

    def _node_type_from_labels(self, labels) -> str:
        """Return schema type key from a Neo4j node's labels set."""
        for lbl in labels:
            if lbl in self._LABEL_TO_TYPE:
                return self._LABEL_TO_TYPE[lbl]
        return 'unknown'

    def visualize_from_neo4j(self, kg, query: Optional[str] = None,
                            output_path: str = "visualizations/knowledge_graph.html"):
        """
        Create visualization directly from Neo4j database.

        When no custom query is provided, uses two separate queries (nodes then
        relationships) to avoid LIMIT fragility and to enable degree-based sizing.
        """
        if query is not None:
            # Caller supplied a custom query — use single-query path
            results = kg.execute_query(query)
            nodes: Dict = {}
            relationships = []
            for record in results:
                if 'n' in record and record['n']:
                    neo_node = record['n']
                    node_data = dict(neo_node)
                    nid = node_data['id']
                    if nid not in nodes:
                        nodes[nid] = {
                            'id': nid,
                            'label': node_data.get('title') or node_data.get('name') or nid[:20],
                            'type': self._node_type_from_labels(neo_node.labels),
                            **node_data
                        }
                if 'm' in record and record['m']:
                    neo_node = record['m']
                    node_data = dict(neo_node)
                    nid = node_data['id']
                    if nid not in nodes:
                        nodes[nid] = {
                            'id': nid,
                            'label': node_data.get('title') or node_data.get('name') or nid[:20],
                            'type': self._node_type_from_labels(neo_node.labels),
                            **node_data
                        }
                if 'r' in record and record['r']:
                    rel_data = record['r']
                    relationships.append({
                        'from': dict(record['n'])['id'],
                        'to': dict(record['m'])['id'],
                        'type': rel_data.type,
                        **dict(rel_data)
                    })
            return self.create_visualization(list(nodes.values()), relationships, output_path)

        # ── Two-query approach: unlimited nodes, then unlimited relationships ──
        nodes_result = kg.execute_query("MATCH (n) RETURN n")
        rels_result = kg.execute_query(
            "MATCH (n)-[r]->(m) "
            "RETURN n.id AS src, type(r) AS rtype, m.id AS tgt, properties(r) AS props"
        )

        nodes: Dict = {}
        for record in nodes_result:
            neo_node = record['n']
            node_data = dict(neo_node)
            nid = node_data['id']
            nodes[nid] = {
                'id': nid,
                'label': node_data.get('title') or node_data.get('name') or nid[:20],
                'type': self._node_type_from_labels(neo_node.labels),
                **node_data
            }

        relationships = []
        for record in rels_result:
            relationships.append({
                'from': record['src'],
                'to': record['tgt'],
                'type': record['rtype'],
                **(record['props'] or {}),
            })

        # ── Degree-based node sizing ───────────────────────────────────────────
        degree: dict = defaultdict(int)
        for rel in relationships:
            degree[rel['from']] += 1
            degree[rel['to']] += 1

        for node in nodes.values():
            base = _BASE_SIZES.get(node['type'], 8)
            boost = min(degree.get(node['id'], 0) * 1.5, 20)
            node['size'] = base + boost

        return self.create_visualization(list(nodes.values()), relationships, output_path)
    
    def visualize_subgraph(self, kg, center_node_id: str, depth: int = 2,
                          output_path: str = "visualizations/subgraph.html"):
        """
        Visualize a subgraph around a specific node.
        
        Args:
            kg: KnowledgeGraph instance
            center_node_id: ID of the node to center on
            depth: How many hops to include
            output_path: Where to save the HTML file
        """
        query = f"""
        MATCH path = (center {{id: $center_id}})-[*1..{depth}]-(connected)
        WITH center, connected, relationships(path) as rels
        RETURN center, connected, rels
        """
        
        results = kg.execute_query(query, {'center_id': center_node_id})
        
        nodes = {}
        relationships = []
        
        for record in results:
            # Add center node
            neo_center = record['center']
            center_data = dict(neo_center)
            center_id = center_data['id']
            if center_id not in nodes:
                nodes[center_id] = {
                    'id': center_id,
                    'label': center_data.get('title') or center_data.get('name') or center_id[:20],
                    'type': self._node_type_from_labels(neo_center.labels),
                    'size': 20,  # Make center node larger
                    **center_data
                }

            # Add connected node
            neo_connected = record['connected']
            connected_data = dict(neo_connected)
            connected_id = connected_data['id']
            if connected_id not in nodes:
                nodes[connected_id] = {
                    'id': connected_id,
                    'label': connected_data.get('title') or connected_data.get('name') or connected_id[:20],
                    'type': self._node_type_from_labels(neo_connected.labels),
                    **connected_data
                }
            
            # Add relationships
            for rel in record['rels']:
                relationships.append({
                    'from': rel.start_node['id'],
                    'to': rel.end_node['id'],
                    'type': rel.type,
                })
        
        return self.create_visualization(
            list(nodes.values()),
            relationships,
            output_path
        )
    
    def _get_node_type_from_id(self, node_id: str) -> str:
        """Determine node type from ID."""
        if node_id.startswith('arxiv:'):
            return 'paper'
        elif node_id.startswith('note:'):
            return 'note'
        elif '/' in node_id:
            return 'github_repo'
        elif node_id.startswith('concept:'):
            return 'concept'
        return 'unknown'
    
    # ── Concept category colors for expertise map ─────────────────────────────
    _CONCEPT_CAT_COLORS = {
        "disease": "#FF6B6B",   # warm red
        "method":  "#45B7D1",   # blue
        "biology": "#96CEB4",   # mint green
        "cohort":  "#FFA07A",   # light salmon
        "topic":   "#C39BD3",   # soft purple (broad topics: concept:microbiome)
    }

    def _concept_color(self, concept_id: str) -> str:
        parts = concept_id.split(":")
        cat = parts[1] if len(parts) >= 3 else "topic"
        return self._CONCEPT_CAT_COLORS.get(cat, "#C39BD3")

    def _paper_short_label(self, node_data: Dict) -> str:
        """'Clos 2018' style short label."""
        authors = node_data.get("authors") or []
        year = node_data.get("year") or ""
        if isinstance(authors, list) and authors:
            first = authors[0]
            last = first.split(",")[0].strip() if "," in first else first.split()[0]
            return f"{last} {year}".strip()
        return str(node_data.get("id", "?"))[:12]

    def generate_expertise_map(self, kg, output_path: str = "visualizations/expertise_map.html"):
        """
        Concept-centric expertise map: no Person nodes, concepts are large and
        colored by category (disease/method/biology/cohort/topic), papers shown
        with short 'AuthorYear' labels.
        """
        nodes_result = kg.execute_query(
            "MATCH (n) WHERE n:Paper OR n:GithubRepo OR n:Concept RETURN n"
        )
        rels_result = kg.execute_query(
            "MATCH (n)-[r]->(m) "
            "WHERE (n:Paper OR n:GithubRepo OR n:Concept) "
            "AND (m:Paper OR m:GithubRepo OR m:Concept) "
            "RETURN n.id AS src, type(r) AS rtype, m.id AS tgt, properties(r) AS props"
        )

        nodes: Dict = {}
        for record in nodes_result:
            neo_node = record['n']
            node_data = dict(neo_node)
            nid = node_data['id']
            nodes[nid] = {
                'id': nid,
                'type': self._node_type_from_labels(neo_node.labels),
                '_nd': node_data,
            }

        relationships = []
        for record in rels_result:
            relationships.append({
                'from': record['src'],
                'to': record['tgt'],
                'type': record['rtype'],
                **(record['props'] or {}),
            })

        degree: dict = defaultdict(int)
        for rel in relationships:
            degree[rel['from']] += 1
            degree[rel['to']] += 1

        net = Network(height="820px", width="100%", directed=True)
        net.set_options("""
        {
          "physics": {
            "barnesHut": {
              "gravitationalConstant": -8000,
              "centralGravity": 0.45,
              "springLength": 130,
              "springConstant": 0.06,
              "damping": 0.15,
              "avoidOverlap": 0.8
            },
            "maxVelocity": 60,
            "solver": "barnesHut",
            "timestep": 0.4,
            "stabilization": {"iterations": 300, "updateInterval": 25}
          },
          "interaction": {"hover": true, "tooltipDelay": 100},
          "edges": {"smooth": {"type": "continuous"}}
        }
        """)

        for nid, node in nodes.items():
            nd = node['_nd']
            nt = node['type']
            deg = degree.get(nid, 0)

            if nt == 'concept':
                color  = self._concept_color(nid)
                size   = 22 + min(deg * 2.5, 28)
                label  = nd.get('name') or nid.split(':')[-1].replace('_', ' ').title()
                font   = {'size': 14}
                mass   = 3
            elif nt == 'paper':
                color  = "#FFF0A0"
                size   = 11 + min(deg * 1.2, 12)
                label  = self._paper_short_label(nd)
                font   = {'size': 10}
                mass   = 1
            elif nt == 'github_repo':
                color  = "#4ECDC4"
                size   = 15 + min(deg * 1.5, 12)
                label  = nid.split('/')[-1]
                font   = {'size': 11}
                mass   = 1.5
            else:
                color, size, label, font, mass = "#CCCCCC", 8, nid[:15], {'size': 9}, 1

            title = self._create_node_tooltip({**nd, 'type': nt})
            net.add_node(nid, label=label, title=title, color=color,
                         size=size, font=font, mass=mass, shape='dot')

        # Edge styles with shorter spring lengths (keep papers near concepts)
        map_edge_styles = {
            "MENTIONS":        {"color": "#F38181", "length": 120},
            "USES_TECHNOLOGY": {"color": "#45B7D1", "length": 120},
            "RELATES_TO":      {"color": "#96CEB4", "length": 200},
            "CITES":           {"color": "#FFEAA7", "length": 180},
            "IMPLEMENTS":      {"color": "#4ECDC4", "length": 130},
        }
        for rel in relationships:
            rel_type = rel.get('type', 'RELATES_TO')
            style = map_edge_styles.get(rel_type, {"color": "#AAAAAA", "length": 150})
            title_txt = rel_type
            if 'similarity_score' in rel:
                title_txt += f" ({rel['similarity_score']:.2f})"
            net.add_edge(rel['from'], rel['to'], title=title_txt,
                         arrows='to', color=style["color"], length=style["length"])

        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        net.save_graph(str(output_file))

        legend = """
<div id="em-legend" style="position:fixed;top:10px;right:10px;
  background:rgba(255,255,255,0.93);padding:10px 14px;border-radius:8px;
  font-family:Arial,sans-serif;font-size:12px;line-height:2.0;
  box-shadow:0 2px 10px rgba(0,0,0,0.22);z-index:9999;min-width:210px">
  <b style="font-size:13px">Concepts (size = # connections)</b><br>
  <span style="color:#FF6B6B;font-size:17px">&#9679;</span> Disease &nbsp;
  <span style="color:#45B7D1;font-size:17px">&#9679;</span> Method &nbsp;
  <span style="color:#96CEB4;font-size:17px">&#9679;</span> Biology<br>
  <span style="color:#FFA07A;font-size:17px">&#9679;</span> Cohort &nbsp;
  <span style="color:#C39BD3;font-size:17px">&#9679;</span> Topic<br>
  <b style="font-size:13px;margin-top:4px;display:block">Other nodes</b>
  <span style="color:#FFF0A0;font-size:17px;text-shadow:0 0 2px #aaa">&#9679;</span> Paper <em>(Author Year)</em><br>
  <span style="color:#4ECDC4;font-size:17px">&#9679;</span> GitHub Repo<br>
  <b style="font-size:13px;margin-top:4px;display:block">Edges</b>
  <span style="color:#F38181;font-weight:bold">&#9135;</span> MENTIONS &nbsp;
  <span style="color:#45B7D1;font-weight:bold">&#9135;</span> USES_TECH<br>
  <span style="color:#96CEB4;font-weight:bold">&#9135;</span> RELATES_TO &nbsp;
  <span style="color:#4ECDC4;font-weight:bold">&#9135;</span> IMPLEMENTS
</div>
"""
        html = output_file.read_text(encoding="utf-8")
        html = html.replace("</body>", legend + "</body>")
        output_file.write_text(html, encoding="utf-8")

        logger.info(f"Expertise map saved to {output_path}")
        return output_file

    def create_networkx_graph(self, nodes: List[Dict], relationships: List[Dict]) -> nx.DiGraph:
        """
        Create a NetworkX graph for advanced analysis.
        
        Returns:
            NetworkX directed graph
        """
        G = nx.DiGraph()
        
        # Add nodes
        for node in nodes:
            G.add_node(node['id'], **node)
        
        # Add edges
        for rel in relationships:
            G.add_edge(rel['from'], rel['to'], **rel)
        
        return G
    
    def compute_graph_metrics(self, G: nx.DiGraph) -> Dict:
        """
        Compute various graph metrics.
        
        Returns:
            Dictionary of metrics
        """
        metrics = {
            'num_nodes': G.number_of_nodes(),
            'num_edges': G.number_of_edges(),
            'density': nx.density(G),
        }
        
        # Compute centrality measures
        if G.number_of_nodes() > 0:
            metrics['degree_centrality'] = nx.degree_centrality(G)
            metrics['betweenness_centrality'] = nx.betweenness_centrality(G)
            
            # Find most central nodes
            top_degree = sorted(metrics['degree_centrality'].items(), 
                              key=lambda x: x[1], reverse=True)[:10]
            metrics['top_degree_nodes'] = top_degree
            
            top_between = sorted(metrics['betweenness_centrality'].items(),
                               key=lambda x: x[1], reverse=True)[:10]
            metrics['top_betweenness_nodes'] = top_between
        
        return metrics


if __name__ == "__main__":
    # Example usage
    from graph_db import KnowledgeGraph
    from dotenv import load_dotenv
    import os
    
    load_dotenv()
    
    kg = KnowledgeGraph(
        uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        user=os.getenv("NEO4J_USER", "neo4j"),
        password=os.getenv("NEO4J_PASSWORD")
    )
    
    visualizer = GraphVisualizer()

    print("Creating full graph visualization...")
    output_file = visualizer.visualize_from_neo4j(kg)
    print(f"Visualization saved to: {output_file}")

    print("Creating expertise map visualization...")
    map_file = visualizer.generate_expertise_map(kg)
    print(f"Expertise map saved to: {map_file}")

    kg.close()
