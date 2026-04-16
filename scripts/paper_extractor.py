"""
Academic paper data extractor.
Fetches papers from arXiv and other sources.
"""

import arxiv
import requests
from typing import List, Dict, Optional
import logging
import json
from pathlib import Path
import re

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class PaperExtractor:
    """Extract academic paper metadata."""
    
    def __init__(self):
        """Initialize paper extractor."""
        self.arxiv_client = arxiv.Client()
        logger.info("Paper extractor initialized")
    
    def search_arxiv(self, query: str, max_results: int = 50) -> List[Dict]:
        """Search for papers on arXiv."""
        try:
            search = arxiv.Search(
                query=query,
                max_results=max_results,
                sort_by=arxiv.SortCriterion.Relevance
            )
            
            papers = []
            for result in self.arxiv_client.results(search):
                paper_data = self.extract_arxiv_paper(result)
                papers.append(paper_data)
                logger.info(f"Extracted: {paper_data['title'][:50]}...")
            
            logger.info(f"Extracted {len(papers)} papers from arXiv")
            return papers
            
        except Exception as e:
            logger.error(f"arXiv search error: {e}")
            return []
    
    def extract_arxiv_paper(self, result) -> Dict:
        """Extract data from arXiv result."""
        # Extract paper ID from URL
        paper_id = result.entry_id.split('/')[-1]
        
        # Get categories/keywords
        categories = [cat for cat in result.categories]
        
        # Extract authors
        authors = [author.name for author in result.authors]
        
        return {
            'id': f"arxiv:{paper_id}",
            'title': result.title,
            'abstract': result.summary,
            'authors': authors,
            'year': result.published.year,
            'published_date': result.published.isoformat(),
            'updated_date': result.updated.isoformat(),
            'categories': categories,
            'keywords': categories,  # Use categories as initial keywords
            'url': result.entry_id,
            'pdf_url': result.pdf_url,
            'venue': 'arXiv',
            'comment': result.comment or "",
        }
    
    def get_paper_by_id(self, arxiv_id: str) -> Optional[Dict]:
        """Get a specific paper by arXiv ID."""
        try:
            search = arxiv.Search(id_list=[arxiv_id])
            result = next(self.arxiv_client.results(search))
            return self.extract_arxiv_paper(result)
        except Exception as e:
            logger.error(f"Error fetching paper {arxiv_id}: {e}")
            return None
    
    def extract_citations_from_text(self, text: str) -> List[str]:
        """
        Extract arXiv IDs from text (simple regex-based).
        Looks for patterns like arxiv:1234.5678 or arXiv:1234.5678v1
        """
        patterns = [
            r'arxiv:(\d{4}\.\d{4,5}(?:v\d+)?)',
            r'arXiv:(\d{4}\.\d{4,5}(?:v\d+)?)',
            r'arxiv\.org/abs/(\d{4}\.\d{4,5}(?:v\d+)?)',
        ]
        
        citations = []
        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            citations.extend(matches)
        
        # Remove version numbers and deduplicate
        citations = list(set([c.split('v')[0] for c in citations]))
        return citations
    
    def get_papers_by_author(self, author_name: str, max_results: int = 20) -> List[Dict]:
        """Search for papers by author name."""
        query = f"au:{author_name}"
        return self.search_arxiv(query, max_results)
    
    def parse_bibtex_file(self, bibtex_path: str) -> List[Dict]:
        """
        Parse a BibTeX file and extract paper information.
        Note: Requires bibtexparser library.
        """
        try:
            import bibtexparser
            from bibtexparser.bparser import BibTexParser
            
            with open(bibtex_path) as bibtex_file:
                parser = BibTexParser()
                bib_database = bibtexparser.load(bibtex_file, parser=parser)
            
            papers = []
            for entry in bib_database.entries:
                paper = {
                    'id': entry.get('ID', ''),
                    'title': entry.get('title', ''),
                    'abstract': entry.get('abstract', ''),
                    'authors': [a.strip() for a in entry.get('author', '').split(' and ')],
                    'year': int(entry.get('year', 0)) if entry.get('year') else None,
                    'venue': entry.get('journal', entry.get('booktitle', '')),
                    'url': entry.get('url', ''),
                    'doi': entry.get('doi', ''),
                    'keywords': entry.get('keywords', '').split(','),
                }
                papers.append(paper)
            
            logger.info(f"Parsed {len(papers)} papers from BibTeX file")
            return papers
            
        except ImportError:
            logger.error("bibtexparser not installed. Install with: pip install bibtexparser")
            return []
        except Exception as e:
            logger.error(f"Error parsing BibTeX file: {e}")
            return []
    
    def enrich_paper_with_semantic_scholar(self, paper_title: str) -> Optional[Dict]:
        """
        Enrich paper data using Semantic Scholar API.
        Gets citation count, references, and other metadata.
        """
        try:
            api_url = "https://api.semanticscholar.org/graph/v1/paper/search"
            params = {'query': paper_title, 'limit': 1, 'fields': 'title,abstract,authors,year,citationCount,references,venue'}
            
            response = requests.get(api_url, params=params)
            if response.status_code == 200:
                data = response.json()
                if data.get('data'):
                    paper = data['data'][0]
                    return {
                        'semantic_scholar_id': paper.get('paperId'),
                        'citation_count': paper.get('citationCount', 0),
                        'references': [ref.get('paperId') for ref in paper.get('references', [])[:10]],
                        'venue': paper.get('venue', ''),
                    }
        except Exception as e:
            logger.warning(f"Could not enrich paper via Semantic Scholar: {e}")
        
        return None
    
    def export_to_json(self, papers: List[Dict], output_path: str):
        """Export paper data to JSON file."""
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_file, 'w') as f:
            json.dump(papers, f, indent=2)
        
        logger.info(f"Exported {len(papers)} papers to {output_path}")


if __name__ == "__main__":
    # Example usage
    extractor = PaperExtractor()
    
    # Search for papers on a topic
    papers = extractor.search_arxiv("machine learning graph neural networks", max_results=5)
    
    if papers:
        extractor.export_to_json(papers, "data/papers.json")
        print(f"\nExtracted {len(papers)} papers")
        print("\nSample paper:")
        print(f"Title: {papers[0]['title']}")
        print(f"Authors: {', '.join(papers[0]['authors'][:3])}")
        print(f"Year: {papers[0]['year']}")
        print(f"URL: {papers[0]['url']}")
