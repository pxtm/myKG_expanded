"""
Core graph database interface for the knowledge graph.
Handles connections and basic operations with Neo4j.
"""

from neo4j import GraphDatabase
from typing import Dict, List, Any, Optional
import logging
from pathlib import Path
import yaml

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class KnowledgeGraph:
    """Main interface for interacting with the knowledge graph database."""
    
    def __init__(self, uri: str, user: str, password: str, config_path: str = "config/schema.yaml"):
        """Initialize connection to Neo4j database."""
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.config = self._load_config(config_path)
        logger.info("Connected to Neo4j database")
        
    def _load_config(self, config_path: str) -> Dict:
        """Load schema configuration."""
        with open(config_path, 'r') as f:
            return yaml.safe_load(f)
    
    def close(self):
        """Close database connection."""
        self.driver.close()
        logger.info("Database connection closed")
    
    def clear_database(self):
        """Clear all nodes and relationships. Use with caution!"""
        with self.driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")
        logger.warning("Database cleared")
    
    def create_indexes(self):
        """Create indexes for better query performance."""
        indexes = [
            "CREATE INDEX paper_id IF NOT EXISTS FOR (p:Paper) ON (p.id)",
            "CREATE INDEX repo_id IF NOT EXISTS FOR (r:Repo) ON (r.id)",
            "CREATE INDEX note_id IF NOT EXISTS FOR (n:Note) ON (n.id)",
            "CREATE INDEX concept_name IF NOT EXISTS FOR (c:Concept) ON (c.name)",
            "CREATE INDEX person_name IF NOT EXISTS FOR (p:Person) ON (p.name)",
        ]
        
        with self.driver.session() as session:
            for index_query in indexes:
                session.run(index_query)
        logger.info("Indexes created")
    
    def add_node(self, node_type: str, properties: Dict[str, Any]) -> str:
        """Add a node to the graph."""
        label = node_type.title().replace("_", "")
        
        # Ensure id is present
        if 'id' not in properties:
            raise ValueError(f"Node properties must include 'id' field")
        
        query = f"""
        MERGE (n:{label} {{id: $id}})
        SET n += $properties
        RETURN n.id as id
        """
        
        with self.driver.session() as session:
            result = session.run(query, id=properties['id'], properties=properties)
            node_id = result.single()['id']
            logger.debug(f"Added {node_type} node: {node_id}")
            return node_id
    
    def add_relationship(self, from_id: str, to_id: str, rel_type: str, 
                        from_label: str, to_label: str, properties: Dict[str, Any] = None):
        """Add a relationship between two nodes."""
        properties = properties or {}
        
        query = f"""
        MATCH (a:{from_label} {{id: $from_id}})
        MATCH (b:{to_label} {{id: $to_id}})
        MERGE (a)-[r:{rel_type}]->(b)
        SET r += $properties
        RETURN type(r) as rel_type
        """
        
        with self.driver.session() as session:
            result = session.run(
                query, 
                from_id=from_id, 
                to_id=to_id, 
                properties=properties
            )
            if result.peek():
                logger.debug(f"Added {rel_type} relationship: {from_id} -> {to_id}")
            else:
                logger.warning(f"Failed to create relationship: {from_id} -> {to_id}")
    
    def get_node(self, node_id: str, node_type: str = None) -> Optional[Dict]:
        """Retrieve a node by ID."""
        if node_type:
            label = node_type.title().replace("_", "")
            query = f"MATCH (n:{label} {{id: $id}}) RETURN n"
        else:
            query = "MATCH (n {id: $id}) RETURN n"
        
        with self.driver.session() as session:
            result = session.run(query, id=node_id)
            record = result.single()
            return dict(record['n']) if record else None
    
    def get_neighbors(self, node_id: str, relationship_type: str = None, 
                     direction: str = "both") -> List[Dict]:
        """Get all neighbors of a node."""
        if direction == "outgoing":
            rel_pattern = f"-[r{':' + relationship_type if relationship_type else ''}]->"
        elif direction == "incoming":
            rel_pattern = f"<-[r{':' + relationship_type if relationship_type else ''}]-"
        else:
            rel_pattern = f"-[r{':' + relationship_type if relationship_type else ''}]-"
        
        query = f"""
        MATCH (n {{id: $id}}){rel_pattern}(neighbor)
        RETURN neighbor, type(r) as rel_type
        """
        
        with self.driver.session() as session:
            result = session.run(query, id=node_id)
            return [
                {
                    'node': dict(record['neighbor']),
                    'relationship': record['rel_type']
                }
                for record in result
            ]
    
    def find_path(self, from_id: str, to_id: str, max_depth: int = 5) -> List[Dict]:
        """Find shortest path between two nodes."""
        query = """
        MATCH path = shortestPath((a {id: $from_id})-[*..%d]-(b {id: $to_id}))
        RETURN [node in nodes(path) | {id: node.id, labels: labels(node)}] as nodes,
               [rel in relationships(path) | type(rel)] as relationships
        """ % max_depth
        
        with self.driver.session() as session:
            result = session.run(query, from_id=from_id, to_id=to_id)
            record = result.single()
            return {
                'nodes': record['nodes'],
                'relationships': record['relationships']
            } if record else None
    
    def get_statistics(self) -> Dict[str, int]:
        """Get basic statistics about the graph."""
        with self.driver.session() as session:
            # Count nodes by type
            node_counts = {}
            for node_type in self.config['node_types'].keys():
                label = node_type.title().replace("_", "")
                result = session.run(f"MATCH (n:{label}) RETURN count(n) as count")
                node_counts[node_type] = result.single()['count']
            
            # Count relationships by type
            rel_counts = {}
            for rel_config in self.config['relationship_types']:
                rel_type = rel_config['name']
                result = session.run(f"MATCH ()-[r:{rel_type}]->() RETURN count(r) as count")
                rel_counts[rel_type] = result.single()['count']
            
            return {
                'nodes': node_counts,
                'relationships': rel_counts,
                'total_nodes': sum(node_counts.values()),
                'total_relationships': sum(rel_counts.values())
            }
    
    def execute_query(self, query: str, parameters: Dict = None) -> List[Dict]:
        """Execute a custom Cypher query."""
        parameters = parameters or {}
        with self.driver.session() as session:
            result = session.run(query, **parameters)
            return [dict(record) for record in result]


if __name__ == "__main__":
    # Example usage
    from dotenv import load_dotenv
    import os
    
    load_dotenv()
    
    kg = KnowledgeGraph(
        uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        user=os.getenv("NEO4J_USER", "neo4j"),
        password=os.getenv("NEO4J_PASSWORD", "password")
    )
    
    kg.create_indexes()
    stats = kg.get_statistics()
    print("Graph Statistics:", stats)
    
    kg.close()
