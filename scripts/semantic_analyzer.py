"""
Semantic analyzer for finding connections between nodes using embeddings.
Uses sentence transformers to compute similarity between items.
"""

from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np
from typing import List, Dict, Tuple
import logging
import json
from pathlib import Path
import pickle

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SemanticAnalyzer:
    """Find semantic connections using embeddings."""
    
    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2"):
        """Initialize semantic analyzer with embedding model."""
        logger.info(f"Loading embedding model: {model_name}")
        self.model = SentenceTransformer(model_name)
        self.embeddings_cache = {}
        logger.info("Semantic analyzer initialized")
    
    def embed_text(self, text: str) -> np.ndarray:
        """Generate embedding for text."""
        return self.model.encode(text, convert_to_numpy=True)
    
    def embed_items(self, items: List[Dict], text_field: str = 'content') -> Dict[str, np.ndarray]:
        """
        Generate embeddings for a list of items.
        
        Args:
            items: List of dicts with id and text fields
            text_field: Field name containing text to embed
        
        Returns:
            Dict mapping item IDs to embeddings
        """
        embeddings = {}
        texts = []
        ids = []
        
        for item in items:
            item_id = item['id']
            text = self._get_item_text(item, text_field)
            
            if text:
                texts.append(text)
                ids.append(item_id)
        
        logger.info(f"Generating embeddings for {len(texts)} items...")
        batch_embeddings = self.model.encode(texts, convert_to_numpy=True, show_progress_bar=True)
        
        for item_id, embedding in zip(ids, batch_embeddings):
            embeddings[item_id] = embedding
            self.embeddings_cache[item_id] = embedding
        
        return embeddings
    
    def _get_item_text(self, item: Dict, text_field: str) -> str:
        """Extract text to embed from an item."""
        # For papers: use title + abstract
        if 'abstract' in item:
            return f"{item.get('title', '')} {item.get('abstract', '')}"
        
        # For repos: use name + description + readme excerpt
        if 'description' in item and 'readme' in item:
            return f"{item.get('name', '')} {item.get('description', '')} {item.get('readme', '')[:500]}"
        
        # For notes: use title + content excerpt
        if text_field in item:
            content = item[text_field]
            if len(content) > 1000:
                content = content[:1000]
            return f"{item.get('title', '')} {content}"
        
        # Fallback
        return item.get('title', '') or item.get('name', '') or str(item.get('id', ''))
    
    def find_similar_items(self, query_id: str, embeddings: Dict[str, np.ndarray], 
                          threshold: float = 0.7, top_k: int = 10) -> List[Tuple[str, float]]:
        """
        Find items similar to the query item.
        
        Returns:
            List of (item_id, similarity_score) tuples
        """
        if query_id not in embeddings:
            logger.warning(f"Query ID {query_id} not found in embeddings")
            return []
        
        query_embedding = embeddings[query_id]
        similarities = []
        
        for item_id, embedding in embeddings.items():
            if item_id == query_id:
                continue
            
            similarity = cosine_similarity(
                query_embedding.reshape(1, -1),
                embedding.reshape(1, -1)
            )[0][0]
            
            if similarity >= threshold:
                similarities.append((item_id, float(similarity)))
        
        # Sort by similarity (highest first)
        similarities.sort(key=lambda x: x[1], reverse=True)
        return similarities[:top_k]
    
    def find_all_similarities(self, embeddings: Dict[str, np.ndarray], 
                            threshold: float = 0.7) -> List[Tuple[str, str, float]]:
        """
        Find all pairs of similar items above threshold.
        
        Returns:
            List of (id1, id2, similarity) tuples
        """
        ids = list(embeddings.keys())
        embeddings_matrix = np.array([embeddings[id_] for id_ in ids])
        
        logger.info(f"Computing similarity matrix for {len(ids)} items...")
        similarity_matrix = cosine_similarity(embeddings_matrix)
        
        pairs = []
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                similarity = similarity_matrix[i][j]
                if similarity >= threshold:
                    pairs.append((ids[i], ids[j], float(similarity)))
        
        logger.info(f"Found {len(pairs)} similar pairs above threshold {threshold}")
        return pairs
    
    def cluster_items(self, embeddings: Dict[str, np.ndarray], n_clusters: int = 5) -> Dict[str, int]:
        """
        Cluster items based on their embeddings.
        
        Returns:
            Dict mapping item IDs to cluster IDs
        """
        from sklearn.cluster import KMeans
        
        ids = list(embeddings.keys())
        embeddings_matrix = np.array([embeddings[id_] for id_ in ids])
        
        logger.info(f"Clustering {len(ids)} items into {n_clusters} clusters...")
        kmeans = KMeans(n_clusters=n_clusters, random_state=42)
        cluster_labels = kmeans.fit_predict(embeddings_matrix)
        
        clusters = {id_: int(label) for id_, label in zip(ids, cluster_labels)}
        
        # Log cluster sizes
        cluster_sizes = {}
        for cluster_id in clusters.values():
            cluster_sizes[cluster_id] = cluster_sizes.get(cluster_id, 0) + 1
        logger.info(f"Cluster sizes: {cluster_sizes}")
        
        return clusters
    
    def extract_keywords(self, text: str, top_k: int = 10) -> List[str]:
        """
        Extract keywords from text using embedding similarity.
        Simple implementation - can be enhanced with more sophisticated methods.
        """
        # Split into sentences
        sentences = [s.strip() for s in text.split('.') if len(s.strip()) > 10]
        
        if not sentences:
            return []
        
        # Get embeddings
        embeddings = self.model.encode(sentences, convert_to_numpy=True)
        
        # Compute centroid
        centroid = np.mean(embeddings, axis=0)
        
        # Find sentences closest to centroid
        similarities = cosine_similarity(embeddings, centroid.reshape(1, -1))
        top_indices = np.argsort(similarities.flatten())[-top_k:][::-1]
        
        # Extract key phrases from top sentences
        keywords = []
        for idx in top_indices:
            sentence = sentences[idx]
            # Simple noun phrase extraction (you could enhance this)
            words = sentence.split()
            for i, word in enumerate(words):
                if word[0].isupper() and len(word) > 3:
                    keywords.append(word)
        
        return list(set(keywords))[:top_k]
    
    def save_embeddings(self, embeddings: Dict[str, np.ndarray], filepath: str):
        """Save embeddings to disk."""
        output_path = Path(filepath)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'wb') as f:
            pickle.dump(embeddings, f)
        
        logger.info(f"Saved embeddings for {len(embeddings)} items to {filepath}")
    
    def load_embeddings(self, filepath: str) -> Dict[str, np.ndarray]:
        """Load embeddings from disk."""
        with open(filepath, 'rb') as f:
            embeddings = pickle.load(f)
        
        self.embeddings_cache.update(embeddings)
        logger.info(f"Loaded embeddings for {len(embeddings)} items from {filepath}")
        return embeddings
    
    def export_similarities_to_json(self, similarities: List[Tuple[str, str, float]], 
                                   output_path: str):
        """Export similarity pairs to JSON."""
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        data = [
            {'from': id1, 'to': id2, 'similarity': score}
            for id1, id2, score in similarities
        ]
        
        with open(output_file, 'w') as f:
            json.dump(data, f, indent=2)
        
        logger.info(f"Exported {len(similarities)} similarity pairs to {output_path}")


if __name__ == "__main__":
    # Example usage
    analyzer = SemanticAnalyzer()
    
    # Example: Create sample items
    sample_items = [
        {
            'id': 'item1',
            'title': 'Machine Learning with Neural Networks',
            'content': 'Deep learning uses neural networks with multiple layers...'
        },
        {
            'id': 'item2',
            'title': 'Introduction to Graph Theory',
            'content': 'Graph theory studies networks and connections between nodes...'
        },
        {
            'id': 'item3',
            'title': 'Graph Neural Networks',
            'content': 'GNNs apply deep learning to graph-structured data...'
        }
    ]
    
    # Generate embeddings
    embeddings = analyzer.embed_items(sample_items, 'content')
    
    # Find similar items
    similar_to_item1 = analyzer.find_similar_items('item1', embeddings, threshold=0.3)
    print(f"\nItems similar to item1:")
    for item_id, score in similar_to_item1:
        print(f"  {item_id}: {score:.3f}")
    
    # Find all similarities
    all_similarities = analyzer.find_all_similarities(embeddings, threshold=0.3)
    print(f"\nAll similarity pairs:")
    for id1, id2, score in all_similarities:
        print(f"  {id1} <-> {id2}: {score:.3f}")
