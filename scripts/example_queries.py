"""
Example queries for exploring the knowledge graph.
Demonstrates various useful Cypher queries.
"""

from graph_db import KnowledgeGraph
from typing import List, Dict
import json
from pathlib import Path


class QueryExamples:
    """Collection of useful queries for exploring the knowledge graph."""
    
    def __init__(self, kg: KnowledgeGraph):
        """Initialize with a KnowledgeGraph instance."""
        self.kg = kg
    
    def get_all_concepts(self) -> List[Dict]:
        """Get all concepts with their frequencies."""
        query = """
        MATCH (c:Concept)<-[r:MENTIONS]-(n)
        WITH c, count(r) as mentions
        RETURN c.name as concept, mentions
        ORDER BY mentions DESC
        """
        return self.kg.execute_query(query)
    
    def find_papers_related_to_repos(self) -> List[Dict]:
        """Find papers that are semantically related to GitHub repositories."""
        query = """
        MATCH (r:Repo)-[rel:RELATES_TO]->(p:Paper)
        WHERE rel.similarity_score > 0.7
        RETURN r.name as repo, p.title as paper, rel.similarity_score as similarity
        ORDER BY similarity DESC
        LIMIT 20
        """
        return self.kg.execute_query(query)
    
    def find_notes_connected_to_papers(self) -> List[Dict]:
        """Find notes that reference or are similar to papers."""
        query = """
        MATCH (n:Note)-[r]->(p:Paper)
        RETURN n.title as note, p.title as paper, type(r) as relationship
        """
        return self.kg.execute_query(query)
    
    def find_bridge_concepts(self) -> List[Dict]:
        """
        Find concepts that connect different types of work
        (papers, repos, notes).
        """
        query = """
        MATCH (c:Concept)<-[:MENTIONS]-(n)
        WITH c, 
             count(DISTINCT CASE WHEN n:Paper THEN n END) as papers,
             count(DISTINCT CASE WHEN n:Repo THEN n END) as repos,
             count(DISTINCT CASE WHEN n:Note THEN n END) as notes
        WHERE papers > 0 AND repos > 0 AND notes > 0
        RETURN c.name as concept, papers, repos, notes,
               (papers + repos + notes) as total
        ORDER BY total DESC
        """
        return self.kg.execute_query(query)
    
    def find_research_threads(self) -> List[Dict]:
        """
        Find threads of connected work (papers citing papers,
        repos implementing papers, notes referencing both).
        """
        query = """
        MATCH path = (p1:Paper)-[:CITES]->(p2:Paper)
        MATCH (r:Repo)-[:IMPLEMENTS]->(p1)
        OPTIONAL MATCH (n:Note)-[:REFERENCES]->(p1)
        RETURN p1.title as paper1, p2.title as paper2, 
               r.name as implementing_repo,
               collect(DISTINCT n.title) as related_notes
        LIMIT 10
        """
        return self.kg.execute_query(query)
    
    def find_most_connected_items(self, limit: int = 10) -> List[Dict]:
        """Find the most connected nodes in the graph."""
        query = """
        MATCH (n)
        WITH n, size((n)--()) as connections
        WHERE connections > 0
        RETURN 
            n.id as id,
            coalesce(n.title, n.name) as label,
            labels(n)[0] as type,
            connections
        ORDER BY connections DESC
        LIMIT $limit
        """
        return self.kg.execute_query(query, {'limit': limit})
    
    def find_isolated_items(self) -> List[Dict]:
        """Find items with no connections."""
        query = """
        MATCH (n)
        WHERE NOT (n)--()
        RETURN 
            n.id as id,
            coalesce(n.title, n.name) as label,
            labels(n)[0] as type
        """
        return self.kg.execute_query(query)
    
    def find_shortest_path(self, from_id: str, to_id: str) -> Dict:
        """Find shortest path between two items."""
        query = """
        MATCH path = shortestPath((a {id: $from_id})-[*]-(b {id: $to_id}))
        RETURN 
            [node in nodes(path) | {
                id: node.id, 
                label: coalesce(node.title, node.name),
                type: labels(node)[0]
            }] as nodes,
            [rel in relationships(path) | type(rel)] as relationships,
            length(path) as path_length
        """
        results = self.kg.execute_query(query, {'from_id': from_id, 'to_id': to_id})
        return results[0] if results else None
    
    def find_papers_by_topic(self, topic: str) -> List[Dict]:
        """Find papers related to a specific topic."""
        query = """
        MATCH (p:Paper)
        WHERE p.title CONTAINS $topic OR p.abstract CONTAINS $topic
           OR any(keyword IN p.keywords WHERE keyword CONTAINS $topic)
        RETURN p.title as title, p.year as year, p.url as url,
               p.authors as authors
        ORDER BY p.year DESC
        """
        return self.kg.execute_query(query, {'topic': topic})
    
    def find_repos_by_language(self, language: str) -> List[Dict]:
        """Find repositories using a specific programming language."""
        query = """
        MATCH (r:Repo)
        WHERE r.language = $language
        RETURN r.name as name, r.description as description,
               r.stars as stars, r.url as url
        ORDER BY r.stars DESC
        """
        return self.kg.execute_query(query, {'language': language})
    
    def find_collaborative_network(self) -> List[Dict]:
        """Find collaboration networks through co-authorship."""
        query = """
        MATCH (p1:Person)<-[:AUTHORED_BY]-(paper:Paper)-[:AUTHORED_BY]->(p2:Person)
        WHERE p1.id < p2.id
        WITH p1, p2, count(paper) as collaborations
        WHERE collaborations > 1
        RETURN p1.name as person1, p2.name as person2, collaborations
        ORDER BY collaborations DESC
        """
        return self.kg.execute_query(query)
    
    def find_trending_concepts(self, year: int = None) -> List[Dict]:
        """Find concepts that appear frequently in recent work."""
        if year:
            query = """
            MATCH (c:Concept)<-[:MENTIONS]-(n)
            WHERE (n:Paper AND n.year >= $year) OR
                  (n:Repo AND datetime(n.updated_at).year >= $year) OR
                  (n:Note AND datetime(n.updated_at).year >= $year)
            WITH c, count(n) as mentions
            RETURN c.name as concept, mentions
            ORDER BY mentions DESC
            LIMIT 20
            """
            return self.kg.execute_query(query, {'year': year})
        else:
            return self.get_all_concepts()[:20]
    
    def find_knowledge_gaps(self) -> List[Dict]:
        """
        Find concepts mentioned in papers but not implemented in repos,
        or mentioned in notes but not researched in papers.
        """
        query = """
        MATCH (c:Concept)<-[:MENTIONS]-(p:Paper)
        WHERE NOT (c)<-[:MENTIONS]-(:Repo)
        WITH c, count(p) as paper_mentions
        RETURN c.name as concept, paper_mentions, 
               'No implementation' as gap_type
        ORDER BY paper_mentions DESC
        LIMIT 10
        
        UNION
        
        MATCH (c:Concept)<-[:MENTIONS]-(n:Note)
        WHERE NOT (c)<-[:MENTIONS]-(:Paper)
        WITH c, count(n) as note_mentions
        RETURN c.name as concept, note_mentions as paper_mentions,
               'Not researched' as gap_type
        ORDER BY note_mentions DESC
        LIMIT 10
        """
        return self.kg.execute_query(query)
    
    def get_item_neighborhood(self, item_id: str, depth: int = 2) -> List[Dict]:
        """Get all items within N hops of a given item."""
        query = """
        MATCH path = (center {id: $item_id})-[*1..%d]-(neighbor)
        RETURN DISTINCT
            neighbor.id as id,
            coalesce(neighbor.title, neighbor.name) as label,
            labels(neighbor)[0] as type,
            length(path) as distance
        ORDER BY distance, label
        """ % depth
        return self.kg.execute_query(query, {'item_id': item_id})
    
    def export_query_results(self, query_name: str, results: List[Dict], 
                           output_dir: str = "queries/results"):
        """Export query results to JSON file."""
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        output_file = output_path / f"{query_name}.json"
        with open(output_file, 'w') as f:
            json.dump(results, f, indent=2)
        
        print(f"Results exported to {output_file}")


def run_example_queries():
    """Run all example queries and display results."""
    from dotenv import load_dotenv
    import os
    
    load_dotenv()
    
    kg = KnowledgeGraph(
        uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        user=os.getenv("NEO4J_USER", "neo4j"),
        password=os.getenv("NEO4J_PASSWORD")
    )
    
    queries = QueryExamples(kg)
    
    print("\n" + "="*60)
    print("KNOWLEDGE GRAPH QUERIES")
    print("="*60)
    
    # Most connected items
    print("\n📊 Most Connected Items:")
    results = queries.find_most_connected_items(5)
    for item in results:
        print(f"  • {item['label']} ({item['type']}): {item['connections']} connections")
    
    # Bridge concepts
    print("\n🌉 Bridge Concepts (connecting papers, repos, and notes):")
    results = queries.find_bridge_concepts()
    for item in results[:5]:
        print(f"  • {item['concept']}: {item['papers']} papers, {item['repos']} repos, {item['notes']} notes")
    
    # Related papers and repos
    print("\n🔗 Papers Related to Repositories:")
    results = queries.find_papers_related_to_repos()
    for item in results[:5]:
        print(f"  • {item['repo']} ↔ {item['paper']} (similarity: {item['similarity']:.2f})")
    
    # Knowledge gaps
    print("\n❓ Knowledge Gaps:")
    results = queries.find_knowledge_gaps()
    for item in results[:5]:
        print(f"  • {item['concept']}: {item['gap_type']}")
    
    # Top concepts
    print("\n🏷️  Top Concepts:")
    results = queries.get_all_concepts()
    for item in results[:10]:
        print(f"  • {item['concept']}: {item['mentions']} mentions")
    
    kg.close()


if __name__ == "__main__":
    run_example_queries()
