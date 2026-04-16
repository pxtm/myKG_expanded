"""
Standalone BibTeX import script.
Import papers from BibTeX file(s) into the knowledge graph.

Usage:
    python import_bibtex.py path/to/papers.bib
    python import_bibtex.py file1.bib file2.bib file3.bib
"""

import sys
import json
from pathlib import Path
from paper_extractor import PaperExtractor
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()


def import_bibtex_files(bibtex_files, output_path="data/papers.json", enrich=False):
    """
    Import papers from one or more BibTeX files.
    
    Args:
        bibtex_files: List of paths to BibTeX files
        output_path: Where to save the JSON output
        enrich: Whether to enrich with Semantic Scholar data
    """
    extractor = PaperExtractor()
    all_papers = []
    
    console.print(f"\n[bold cyan]Importing from {len(bibtex_files)} BibTeX file(s)[/bold cyan]\n")
    
    for bib_file in bibtex_files:
        bib_path = Path(bib_file)
        
        if not bib_path.exists():
            console.print(f"[red]✗ File not found: {bib_file}[/red]")
            continue
        
        console.print(f"[yellow]Reading {bib_path.name}...[/yellow]")
        
        try:
            papers = extractor.parse_bibtex_file(str(bib_path))
            all_papers.extend(papers)
            console.print(f"[green]✓ Imported {len(papers)} papers from {bib_path.name}[/green]")
        except Exception as e:
            console.print(f"[red]✗ Error reading {bib_path.name}: {e}[/red]")
    
    if not all_papers:
        console.print("[red]No papers imported![/red]")
        return
    
    # Remove duplicates based on citation key (id)
    unique_papers = {}
    for paper in all_papers:
        paper_id = paper.get('id', '')
        if paper_id and paper_id not in unique_papers:
            unique_papers[paper_id] = paper
    
    all_papers = list(unique_papers.values())
    console.print(f"\n[cyan]Total unique papers: {len(all_papers)}[/cyan]")
    
    # Optional: Enrich with Semantic Scholar
    if enrich and all_papers:
        console.print(f"\n[yellow]Enriching papers with Semantic Scholar data...[/yellow]")
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console
        ) as progress:
            task = progress.add_task("Enriching papers...", total=len(all_papers))
            
            enriched_count = 0
            for paper in all_papers:
                if 'title' in paper:
                    enriched_data = extractor.enrich_paper_with_semantic_scholar(paper['title'])
                    if enriched_data:
                        paper.update(enriched_data)
                        enriched_count += 1
                progress.advance(task)
        
        console.print(f"[green]✓ Enriched {enriched_count} papers[/green]")
    
    # Display statistics
    console.print("\n[bold cyan]Paper Statistics:[/bold cyan]")
    
    # Count papers by year
    years = {}
    for paper in all_papers:
        year = paper.get('year', 'Unknown')
        years[year] = years.get(year, 0) + 1
    
    console.print(f"  Papers by year:")
    for year in sorted(years.keys(), reverse=True):
        console.print(f"    {year}: {years[year]} papers")
    
    # Count papers with abstracts
    with_abstracts = sum(1 for p in all_papers if p.get('abstract'))
    console.print(f"\n  Papers with abstracts: {with_abstracts}/{len(all_papers)}")
    
    # Count papers with DOIs
    with_dois = sum(1 for p in all_papers if p.get('doi'))
    console.print(f"  Papers with DOIs: {with_dois}/{len(all_papers)}")
    
    # Save to JSON
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(all_papers, f, indent=2, ensure_ascii=False)
    
    console.print(f"\n[bold green]✓ Saved {len(all_papers)} papers to {output_path}[/bold green]")
    
    # Show sample
    if all_papers:
        console.print("\n[bold cyan]Sample paper:[/bold cyan]")
        sample = all_papers[0]
        console.print(f"  Title: {sample.get('title', 'N/A')}")
        console.print(f"  Authors: {', '.join(sample.get('authors', [])[:3])}")
        console.print(f"  Year: {sample.get('year', 'N/A')}")
        console.print(f"  Venue: {sample.get('venue', 'N/A')}")
    
    console.print("\n[bold cyan]Next steps:[/bold cyan]")
    console.print("1. Run the builder to add papers to the graph:")
    console.print("   [white]python build_graph.py[/white]")
    console.print("2. Select option 2 (Build graph) or 4 (Full pipeline)")


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        console.print("[bold red]Error: No BibTeX file specified[/bold red]")
        console.print("\n[yellow]Usage:[/yellow]")
        console.print("  python import_bibtex.py [cyan]path/to/papers.bib[/cyan]")
        console.print("  python import_bibtex.py [cyan]file1.bib file2.bib[/cyan]")
        console.print("\n[yellow]Examples:[/yellow]")
        console.print("  python import_bibtex.py ../data/my_papers.bib")
        console.print("  python import_bibtex.py my_papers.bib collaborations.bib")
        console.print("\n[yellow]Options:[/yellow]")
        console.print("  Add [cyan]--enrich[/cyan] to fetch citation counts from Semantic Scholar")
        console.print("  Example: python import_bibtex.py papers.bib --enrich")
        sys.exit(1)
    
    # Parse arguments
    bibtex_files = []
    enrich = False
    
    for arg in sys.argv[1:]:
        if arg == '--enrich':
            enrich = True
        elif not arg.startswith('--'):
            bibtex_files.append(arg)
    
    if not bibtex_files:
        console.print("[bold red]Error: No BibTeX files specified[/bold red]")
        sys.exit(1)
    
    # Import papers
    import_bibtex_files(bibtex_files, enrich=enrich)


if __name__ == "__main__":
    main()
