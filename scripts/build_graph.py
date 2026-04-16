"""
Main knowledge graph builder.
Orchestrates data extraction, embedding generation, and graph construction.
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import logging
from typing import Dict, List
import json
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent))

from graph_db import KnowledgeGraph
from github_extractor import GitHubExtractor
from paper_extractor import PaperExtractor
from notes_extractor import NotesExtractor
from semantic_analyzer import SemanticAnalyzer

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

console = Console()


class KnowledgeGraphBuilder:
    """Main class for building the knowledge graph."""
    
    def __init__(self, config_path: str = ".env"):
        """Initialize the builder with configuration."""
        load_dotenv(config_path)
        
        self.neo4j_uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        self.neo4j_user = os.getenv("NEO4J_USER", "neo4j")
        self.neo4j_password = os.getenv("NEO4J_PASSWORD")
        
        self.github_token = os.getenv("GITHUB_TOKEN")
        self.github_username = os.getenv("GITHUB_USERNAME")
        
        self.notes_dir = os.getenv("NOTES_DIRECTORY")
        self.data_dir = Path(os.getenv("DATA_DIRECTORY", "./data"))
        self.data_dir.mkdir(exist_ok=True)
        
        self.similarity_threshold = float(os.getenv("SIMILARITY_THRESHOLD", "0.7"))
        
        console.print("[bold green]Knowledge Graph Builder Initialized[/bold green]")
    
    def extract_data(self, skip_github: bool = False, skip_papers: bool = False, 
                    skip_notes: bool = False):
        """Extract data from all sources."""
        console.print("\n[bold cyan]Phase 1: Data Extraction[/bold cyan]")
        
        # Extract GitHub repositories
        if not skip_github and self.github_token:
            with console.status("[bold green]Extracting GitHub repositories..."):
                try:
                    github = GitHubExtractor(self.github_token, self.github_username)
                    repos = github.get_user_repos(max_repos=int(os.getenv("MAX_REPOS", "50")))
                    github.export_to_json(repos, self.data_dir / "github_repos.json")
                    console.print(f"✓ Extracted {len(repos)} GitHub repositories")
                except Exception as e:
                    console.print(f"[red]✗ GitHub extraction failed: {e}[/red]")
        else:
            console.print("⊘ Skipping GitHub extraction")
        
        # Extract papers
        if not skip_papers:
            try:
                papers = PaperExtractor()
                
                # Ask user for import method
                console.print("\n[bold cyan]Paper Import Options:[/bold cyan]")
                console.print("1. Import from BibTeX file(s)")
                console.print("2. Search arXiv")
                console.print("3. Skip")
                
                choice = input("\nSelect option (1-3): ").strip()
                
                if choice == "1":
                    # BibTeX import
                    bib_path = input("Enter path to BibTeX file (or press Enter for data/papers.bib): ").strip()
                    if not bib_path:
                        bib_path = str(self.data_dir / "papers.bib")
                    
                    if Path(bib_path).exists():
                        console.print(f"[yellow]Importing from {bib_path}...[/yellow]")
                        paper_list = papers.parse_bibtex_file(bib_path)
                        papers.export_to_json(paper_list, self.data_dir / "papers.json")
                        console.print(f"✓ Imported {len(paper_list)} papers from BibTeX")
                    else:
                        console.print(f"[red]✗ File not found: {bib_path}[/red]")
                        console.print("[yellow]Tip: Place your .bib file in the data/ folder[/yellow]")
                
                elif choice == "2":
                    # arXiv search
                    search_query = input("\nEnter paper search query: ").strip()
                    if search_query:
                        console.print(f"[yellow]Searching arXiv for '{search_query}'...[/yellow]")
                        paper_list = papers.search_arxiv(search_query, max_results=int(os.getenv("MAX_PAPERS", "20")))
                        papers.export_to_json(paper_list, self.data_dir / "papers.json")
                        console.print(f"✓ Extracted {len(paper_list)} papers")
                    else:
                        console.print("⊘ No search query provided")
                
                else:
                    console.print("⊘ Skipping paper extraction")
                    
            except Exception as e:
                console.print(f"[red]✗ Paper extraction failed: {e}[/red]")
        else:
            console.print("⊘ Skipping paper extraction")
        
        # Extract notes
        if not skip_notes and self.notes_dir and os.path.exists(self.notes_dir):
            with console.status("[bold green]Extracting notes..."):
                try:
                    notes = NotesExtractor(self.notes_dir)
                    note_list = notes.extract_all_notes()
                    notes.export_to_json(note_list, self.data_dir / "notes.json")
                    console.print(f"✓ Extracted {len(note_list)} notes")
                except Exception as e:
                    console.print(f"[red]✗ Notes extraction failed: {e}[/red]")
        else:
            console.print("⊘ Skipping notes extraction (no directory configured)")
    
    def build_graph(self):
        """Build the knowledge graph from extracted data."""
        console.print("\n[bold cyan]Phase 2: Building Graph[/bold cyan]")
        
        # Connect to database
        kg = KnowledgeGraph(self.neo4j_uri, self.neo4j_user, self.neo4j_password)
        kg.create_indexes()
        
        # Load extracted data
        repos = self._load_json(self.data_dir / "github_repos.json")
        papers = self._load_json(self.data_dir / "papers.json")
        notes = self._load_json(self.data_dir / "notes.json")
        
        # Add repos to graph
        if repos:
            with console.status(f"[bold green]Adding {len(repos)} repositories..."):
                for repo in repos:
                    try:
                        kg.add_node('github_repo', repo)
                    except Exception as e:
                        logger.error(f"Error adding repo {repo.get('id')}: {e}")
            console.print(f"✓ Added {len(repos)} repositories")
        
        # Add papers to graph
        if papers:
            with console.status(f"[bold green]Adding {len(papers)} papers..."):
                for paper in papers:
                    try:
                        kg.add_node('paper', paper)
                        
                        # Add authors
                        for author in paper.get('authors', []):
                            author_id = author.lower().replace(' ', '_')
                            kg.add_node('person', {'id': author_id, 'name': author, 'role': 'author'})
                            kg.add_relationship(paper['id'], author_id, 'AUTHORED_BY', 'Paper', 'Person')
                    except Exception as e:
                        logger.error(f"Error adding paper {paper.get('id')}: {e}")
            console.print(f"✓ Added {len(papers)} papers")
        
        # Add notes to graph
        if notes:
            with console.status(f"[bold green]Adding {len(notes)} notes..."):
                for note in notes:
                    try:
                        kg.add_node('note', note)
                        
                        # Add concepts from tags
                        for tag in note.get('tags', []):
                            concept_id = f"concept:{tag.lower()}"
                            kg.add_node('concept', {
                                'id': concept_id,
                                'name': tag,
                                'category': 'tag'
                            })
                            kg.add_relationship(note['id'], concept_id, 'MENTIONS', 'Note', 'Concept')
                    except Exception as e:
                        logger.error(f"Error adding note {note.get('id')}: {e}")
            console.print(f"✓ Added {len(notes)} notes")
        
        # Add note links
        if notes:
            notes_extractor = NotesExtractor(self.notes_dir) if self.notes_dir else None
            if notes_extractor:
                links = notes_extractor.find_bidirectional_links(notes)
                link_count = 0
                for from_id, to_ids in links.items():
                    for to_id in to_ids:
                        try:
                            kg.add_relationship(from_id, to_id, 'REFERENCES', 'Note', 'Note')
                            link_count += 1
                        except Exception as e:
                            logger.error(f"Error adding link {from_id} -> {to_id}: {e}")
                console.print(f"✓ Added {link_count} note links")
        
        kg.close()
        console.print("[bold green]✓ Graph construction complete[/bold green]")
    
    def compute_similarities(self):
        """Compute semantic similarities between items."""
        console.print("\n[bold cyan]Phase 3: Computing Similarities[/bold cyan]")
        
        analyzer = SemanticAnalyzer()
        
        # Load data
        repos = self._load_json(self.data_dir / "github_repos.json")
        papers = self._load_json(self.data_dir / "papers.json")
        notes = self._load_json(self.data_dir / "notes.json")
        
        all_items = []
        if repos:
            all_items.extend(repos)
        if papers:
            all_items.extend(papers)
        if notes:
            all_items.extend(notes)
        
        if not all_items:
            console.print("[yellow]⚠ No items to analyze[/yellow]")
            return
        
        # Generate embeddings
        with console.status(f"[bold green]Generating embeddings for {len(all_items)} items..."):
            embeddings = analyzer.embed_items(all_items)
            analyzer.save_embeddings(embeddings, self.data_dir / "embeddings.pkl")
        console.print(f"✓ Generated {len(embeddings)} embeddings")
        
        # Find similarities
        with console.status("[bold green]Computing similarities..."):
            similarities = analyzer.find_all_similarities(embeddings, threshold=self.similarity_threshold)
            analyzer.export_similarities_to_json(similarities, self.data_dir / "similarities.json")
        console.print(f"✓ Found {len(similarities)} similar pairs")
        
        # Add to graph
        kg = KnowledgeGraph(self.neo4j_uri, self.neo4j_user, self.neo4j_password)
        
        with console.status("[bold green]Adding similarity relationships to graph..."):
            for id1, id2, score in similarities:
                try:
                    # Determine node types
                    type1 = self._get_node_type(id1)
                    type2 = self._get_node_type(id2)
                    
                    if type1 and type2:
                        kg.add_relationship(
                            id1, id2, 'RELATES_TO',
                            type1, type2,
                            {'similarity_score': score, 'method': 'embedding'}
                        )
                except Exception as e:
                    logger.error(f"Error adding similarity {id1} <-> {id2}: {e}")
        
        kg.close()
        console.print("[bold green]✓ Similarity computation complete[/bold green]")
    
    def _get_node_type(self, node_id: str) -> str:
        """Determine node type from ID."""
        if node_id.startswith('arxiv:'):
            return 'Paper'
        elif node_id.startswith('note:'):
            return 'Note'
        elif '/' in node_id:
            return 'Repo'
        elif node_id.startswith('concept:'):
            return 'Concept'
        return None
    
    def _load_json(self, filepath: Path) -> List[Dict]:
        """Load JSON data file."""
        if not filepath.exists():
            return []
        
        try:
            with open(filepath, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading {filepath}: {e}")
            return []
    
    def show_statistics(self):
        """Display graph statistics."""
        console.print("\n[bold cyan]Knowledge Graph Statistics[/bold cyan]")
        
        kg = KnowledgeGraph(self.neo4j_uri, self.neo4j_user, self.neo4j_password)
        stats = kg.get_statistics()
        kg.close()
        
        # Create nodes table
        nodes_table = Table(title="Nodes")
        nodes_table.add_column("Type", style="cyan")
        nodes_table.add_column("Count", style="magenta", justify="right")
        
        for node_type, count in stats['nodes'].items():
            nodes_table.add_row(node_type, str(count))
        
        console.print(nodes_table)
        
        # Create relationships table
        rels_table = Table(title="Relationships")
        rels_table.add_column("Type", style="cyan")
        rels_table.add_column("Count", style="magenta", justify="right")
        
        for rel_type, count in stats['relationships'].items():
            rels_table.add_row(rel_type, str(count))
        
        console.print(rels_table)
        
        console.print(f"\n[bold]Total Nodes:[/bold] {stats['total_nodes']}")
        console.print(f"[bold]Total Relationships:[/bold] {stats['total_relationships']}")


def main():
    """Main entry point."""
    console.print("[bold blue]╔══════════════════════════════════════════╗[/bold blue]")
    console.print("[bold blue]║   Personal Knowledge Graph Builder      ║[/bold blue]")
    console.print("[bold blue]╚══════════════════════════════════════════╝[/bold blue]\n")
    
    builder = KnowledgeGraphBuilder()
    
    # Interactive menu
    while True:
        console.print("\n[bold cyan]What would you like to do?[/bold cyan]")
        console.print("1. Extract data from sources")
        console.print("2. Build knowledge graph")
        console.print("3. Compute semantic similarities")
        console.print("4. Full pipeline (1 + 2 + 3)")
        console.print("5. Show statistics")
        console.print("6. Exit")
        
        choice = input("\nEnter choice (1-6): ").strip()
        
        if choice == '1':
            builder.extract_data()
        elif choice == '2':
            builder.build_graph()
        elif choice == '3':
            builder.compute_similarities()
        elif choice == '4':
            builder.extract_data()
            builder.build_graph()
            builder.compute_similarities()
            builder.show_statistics()
        elif choice == '5':
            builder.show_statistics()
        elif choice == '6':
            console.print("\n[bold green]Goodbye! 👋[/bold green]")
            break
        else:
            console.print("[red]Invalid choice. Please try again.[/red]")


if __name__ == "__main__":
    main()
