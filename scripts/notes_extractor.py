"""
Notes extractor for personal knowledge management.
Processes markdown, text, and other note formats.
"""

import os
import re
from pathlib import Path
from typing import List, Dict, Set
import logging
import json
from datetime import datetime
import hashlib

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class NotesExtractor:
    """Extract and parse personal notes."""
    
    def __init__(self, notes_directory: str):
        """Initialize notes extractor."""
        self.notes_dir = Path(notes_directory)
        if not self.notes_dir.exists():
            raise ValueError(f"Notes directory does not exist: {notes_directory}")
        logger.info(f"Notes extractor initialized for: {notes_directory}")
    
    def extract_all_notes(self, extensions: List[str] = None) -> List[Dict]:
        """Extract all notes from directory."""
        extensions = extensions or ['.md', '.txt', '.markdown']
        notes = []
        
        for ext in extensions:
            for file_path in self.notes_dir.rglob(f"*{ext}"):
                if self._should_skip_file(file_path):
                    continue
                
                try:
                    note_data = self.extract_note(file_path)
                    notes.append(note_data)
                    logger.info(f"Extracted: {file_path.name}")
                except Exception as e:
                    logger.error(f"Error extracting {file_path}: {e}")
        
        logger.info(f"Extracted {len(notes)} notes")
        return notes
    
    def _should_skip_file(self, file_path: Path) -> bool:
        """Check if file should be skipped."""
        skip_patterns = ['.git', 'node_modules', '__pycache__', '.venv', 'venv']
        return any(pattern in str(file_path) for pattern in skip_patterns)
    
    def extract_note(self, file_path: Path) -> Dict:
        """Extract data from a single note file."""
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        
        # Generate unique ID from file path
        note_id = self._generate_note_id(file_path)
        
        # Extract title (first heading or filename)
        title = self._extract_title(content, file_path.stem)
        
        # Extract tags
        tags = self._extract_tags(content)
        
        # Extract wiki-style links [[link]]
        wiki_links = self._extract_wiki_links(content)
        
        # Extract markdown links [text](url)
        markdown_links = self._extract_markdown_links(content)
        
        # Extract metadata from frontmatter if present
        metadata = self._extract_frontmatter(content)
        
        # Get file timestamps
        created_at = datetime.fromtimestamp(file_path.stat().st_ctime).isoformat()
        updated_at = datetime.fromtimestamp(file_path.stat().st_mtime).isoformat()
        
        return {
            'id': note_id,
            'title': title,
            'content': content,
            'file_path': str(file_path),
            'tags': tags,
            'wiki_links': wiki_links,
            'markdown_links': markdown_links,
            'metadata': metadata,
            'created_at': created_at,
            'updated_at': updated_at,
            'word_count': len(content.split()),
        }
    
    def _generate_note_id(self, file_path: Path) -> str:
        """Generate a unique ID for a note."""
        # Use relative path from notes directory
        relative_path = file_path.relative_to(self.notes_dir)
        return f"note:{hashlib.md5(str(relative_path).encode()).hexdigest()[:12]}"
    
    def _extract_title(self, content: str, fallback: str) -> str:
        """Extract title from first heading or use filename."""
        # Look for first markdown heading
        heading_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
        if heading_match:
            return heading_match.group(1).strip()
        
        # Look for YAML frontmatter title
        frontmatter = self._extract_frontmatter(content)
        if frontmatter and 'title' in frontmatter:
            return frontmatter['title']
        
        return fallback
    
    def _extract_tags(self, content: str) -> List[str]:
        """Extract tags from content (supports #tag and [[tag]] formats)."""
        tags = set()
        
        # Extract hashtags
        hashtag_pattern = r'(?:^|\s)#([a-zA-Z0-9_-]+)'
        hashtags = re.findall(hashtag_pattern, content)
        tags.update(hashtags)
        
        # Extract from frontmatter
        frontmatter = self._extract_frontmatter(content)
        if frontmatter and 'tags' in frontmatter:
            fm_tags = frontmatter['tags']
            if isinstance(fm_tags, list):
                tags.update(fm_tags)
            elif isinstance(fm_tags, str):
                tags.update([t.strip() for t in fm_tags.split(',')])
        
        return list(tags)
    
    def _extract_wiki_links(self, content: str) -> List[str]:
        """Extract wiki-style links [[link]]."""
        pattern = r'\[\[([^\]]+)\]\]'
        links = re.findall(pattern, content)
        return [link.strip() for link in links]
    
    def _extract_markdown_links(self, content: str) -> List[Dict[str, str]]:
        """Extract markdown links [text](url)."""
        pattern = r'\[([^\]]+)\]\(([^\)]+)\)'
        matches = re.findall(pattern, content)
        return [{'text': text, 'url': url} for text, url in matches]
    
    def _extract_frontmatter(self, content: str) -> Dict:
        """Extract YAML frontmatter from note."""
        frontmatter_pattern = r'^---\s*\n(.*?)\n---\s*\n'
        match = re.match(frontmatter_pattern, content, re.DOTALL)
        
        if not match:
            return {}
        
        try:
            import yaml
            frontmatter_text = match.group(1)
            return yaml.safe_load(frontmatter_text) or {}
        except ImportError:
            logger.warning("PyYAML not installed, skipping frontmatter parsing")
            return {}
        except Exception as e:
            logger.warning(f"Error parsing frontmatter: {e}")
            return {}
    
    def find_bidirectional_links(self, notes: List[Dict]) -> Dict[str, List[str]]:
        """
        Find bidirectional links between notes.
        Returns a dict mapping note IDs to lists of linked note IDs.
        """
        # Build title to ID mapping
        title_to_id = {}
        for note in notes:
            title_to_id[note['title'].lower()] = note['id']
        
        # Find links
        links = {}
        for note in notes:
            note_links = []
            
            # Check wiki links
            for wiki_link in note.get('wiki_links', []):
                wiki_link_lower = wiki_link.lower()
                if wiki_link_lower in title_to_id:
                    linked_id = title_to_id[wiki_link_lower]
                    if linked_id != note['id']:  # Don't link to self
                        note_links.append(linked_id)
            
            if note_links:
                links[note['id']] = note_links
        
        return links
    
    def extract_concepts_from_notes(self, notes: List[Dict], min_frequency: int = 2) -> Dict[str, int]:
        """
        Extract frequently occurring concepts from notes.
        Simple frequency-based extraction.
        """
        concept_counts = {}
        
        # Common words to ignore
        stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
                      'of', 'with', 'by', 'from', 'is', 'are', 'was', 'were', 'be', 'been',
                      'this', 'that', 'these', 'those', 'i', 'you', 'he', 'she', 'it', 'we', 'they'}
        
        for note in notes:
            # Extract from title
            title_words = note['title'].lower().split()
            
            # Extract from tags
            for tag in note.get('tags', []):
                concept = tag.lower()
                if len(concept) > 2 and concept not in stop_words:
                    concept_counts[concept] = concept_counts.get(concept, 0) + 1
            
            # Extract capitalized phrases (likely concepts)
            content = note.get('content', '')
            capitalized_pattern = r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b'
            capitalized_phrases = re.findall(capitalized_pattern, content)
            
            for phrase in capitalized_phrases:
                if len(phrase) > 3 and phrase.lower() not in stop_words:
                    concept_counts[phrase] = concept_counts.get(phrase, 0) + 1
        
        # Filter by minimum frequency
        return {k: v for k, v in concept_counts.items() if v >= min_frequency}
    
    def export_to_json(self, notes: List[Dict], output_path: str):
        """Export notes data to JSON file."""
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_file, 'w') as f:
            json.dump(notes, f, indent=2)
        
        logger.info(f"Exported {len(notes)} notes to {output_path}")


if __name__ == "__main__":
    # Example usage
    import sys
    
    if len(sys.argv) > 1:
        notes_dir = sys.argv[1]
    else:
        notes_dir = input("Enter notes directory path: ")
    
    if not os.path.exists(notes_dir):
        print(f"Directory not found: {notes_dir}")
    else:
        extractor = NotesExtractor(notes_dir)
        notes = extractor.extract_all_notes()
        
        if notes:
            extractor.export_to_json(notes, "data/notes.json")
            print(f"\nExtracted {len(notes)} notes")
            
            # Show bidirectional links
            links = extractor.find_bidirectional_links(notes)
            print(f"Found {len(links)} notes with links")
            
            # Show common concepts
            concepts = extractor.extract_concepts_from_notes(notes)
            print(f"\nTop 10 concepts:")
            for concept, count in sorted(concepts.items(), key=lambda x: x[1], reverse=True)[:10]:
                print(f"  {concept}: {count}")
