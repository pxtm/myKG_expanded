# CLAUDE.md -- myKG_expanded Wiki Schema

## Identity

Personal knowledge graph + wiki for **Marc Clos-Garcia** (GitHub: [pxtm](https://github.com/pxtm)).
Biomedical researcher specializing in metabolomics, multi-omics integration,
microbiome research, and biomarker discovery.

## Architecture

Three layers, inspired by [Karpathy's LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f):

| Layer | Directory | Owner | Purpose |
|-------|-----------|-------|---------|
| **Raw sources** | `raw/` | User (immutable) | PDFs, BibTeX, repo snapshots. NEVER modify. |
| **Wiki** | `wiki/` | LLM-maintained | Obsidian-compatible markdown. Summaries, concepts, synthesis. |
| **Graph** | Neo4j (`bolt://localhost:7687`) | Pipeline scripts | Structured nodes, edges, embeddings, scores. |

Supporting directories:
- `data/` -- intermediate JSON, embeddings, similarities (pipeline artifacts)
- `scripts/` -- Python pipeline and wiki management scripts
- `config/schema.yaml` -- graph node/relationship type definitions
- `visualizations/` -- interactive HTML graphs

## Source of Truth

| Data type | Owner | Sync direction |
|-----------|-------|---------------|
| Node existence, relationships, scores | Neo4j | graph -> wiki |
| Prose summaries, narrative, interpretation | Wiki | LLM edits wiki directly |
| Concept/method assignments | Neo4j | graph -> wiki |
| Cross-cutting synthesis pages | Wiki | wiki-only (no graph equivalent) |
| Wikilinks | Wiki | verified against Neo4j edges by `wiki_lint.py` |

---

## Wiki Conventions

### Page Naming

| Page type | Path pattern | Example |
|-----------|-------------|---------|
| Paper source | `wiki/sources/{bibtex_key}.md` | `wiki/sources/clos2018metabolic.md` |
| Repo source | `wiki/sources/{repo-name}.md` | `wiki/sources/fibromyalgia.md` |
| Disease/biology concept | `wiki/concepts/{slug}.md` | `wiki/concepts/colorectal-cancer.md` |
| Broad research topic | `wiki/concepts/{slug}.md` | `wiki/concepts/metabolomics.md` |
| Method/technology | `wiki/methods/{slug}.md` | `wiki/methods/uplc-ms.md` |
| Entity (cohort/person) | `wiki/entities/{slug}.md` | `wiki/entities/imi-direct.md` |
| Synthesis (analysis) | `wiki/synthesis/{topic}.md` | `wiki/synthesis/ev-metabolomics-landscape.md` |

### Slugs

Derived from display name: lowercase, spaces to hyphens, strip special characters.
- "UPLC-MS" -> `uplc-ms`
- "Type 1 Diabetes" -> `type-1-diabetes`
- "Colorectal Cancer" -> `colorectal-cancer`

### Wikilinks

Use Obsidian-style `[[page-name]]` wikilinks. The "page-name" is the filename
stem (without `.md` and without the directory prefix).
- `[[clos2018metabolic]]` links to `wiki/sources/clos2018metabolic.md`
- `[[colorectal-cancer]]` links to `wiki/concepts/colorectal-cancer.md`
- `[[uplc-ms]]` links to `wiki/methods/uplc-ms.md`

### Frontmatter

Every wiki page MUST have YAML frontmatter with at minimum:
- `type`: source | concept | method | entity | synthesis | index | log
- `neo4j_id`: the corresponding Neo4j node ID (sync key). Omit for synthesis pages.
- `tags`: list of Obsidian tags (map to concept slugs)

### Tagging

Use tags in frontmatter: `tags: [metabolomics, colorectal-cancer, biomarker-discovery]`.
Tags should correspond to concept/method slugs so Obsidian's tag pane is useful.

---

## Page Formats

### Source Page -- Paper

```yaml
---
type: source
source_type: paper
neo4j_id: "{bibtex_key}"
title: "{full title}"
authors: ["[[author-slug]]", "Other Author"]  # wikilink only for frequent authors
year: {year}
venue: "{journal/conference}"
tags: [{concept-slugs}]
---

# {Title}

## Summary
[200-400 word summary of key findings and significance]

## Key Findings
- [main results as bullet points]

## Methods
- [[method-slug]] -- brief description of how used
- Cohort/sample details

## Connections
- Related: [[other-paper]], [[other-paper]]
- Concepts: [[concept-slug]], [[concept-slug]]
- Implements: [[repo-name]] (if applicable)

## Notes
[Additional observations, limitations, personal annotations]
```

### Source Page -- Repo

```yaml
---
type: source
source_type: repo
neo4j_id: "{owner/repo}"
name: "{RepoName}"
url: "https://github.com/{owner/repo}"
language: "{primary language}"
tags: [{relevant-slugs}]
---

# {RepoName}

## Description
[What the repo does]

## Implements
- [[paper-key]] -- what paper this code supports

## Technologies
- [[method-slug]] -- methods/tools used

## Notes
[Additional context]
```

### Concept Page

```yaml
---
type: concept
category: disease|biology|topic
neo4j_id: "{concept:category:slug}"
name: "{Display Name}"
aliases: [{alternative names}]
tags: [{related-slugs}]
source_count: {N}
---

# {Display Name}

## Overview
[2-3 sentence definition + relevance to research program]

## Sources
- [[paper-key]] -- one-line summary of what this source says about the concept ({year})
- [[paper-key]] -- ...

## Key Findings Across Sources
[Synthesis: what is known from combining all sources]

## Methods Used
- [[method-slug]], [[method-slug]]

## Related Concepts
- [[concept-slug]], [[concept-slug]]

## Open Questions
[What remains unknown or contradicted]
```

### Method Page

```yaml
---
type: method
neo4j_id: "{concept:method:slug}"
name: "{Display Name}"
aliases: [{alternative names}]
tags: [method, {domain-tags}]
source_count: {N}
---

# {Display Name}

## Description
[What the method is, how it works, why it matters]

## Used In
- [[paper-key]] -- how it was applied ({year})

## Related Methods
- [[method-slug]]
```

### Entity Page

```yaml
---
type: entity
entity_type: cohort|person|institution
neo4j_id: "{node_id}"
name: "{Display Name}"
tags: [{relevant-slugs}]
---

# {Display Name}

## Description
[What/who this entity is]

## Appears In
- [[paper-key]], [[paper-key]]

## Related
- [[concept-slug]]
```

### Synthesis Page

```yaml
---
type: synthesis
title: "{Analysis Title}"
tags: [{relevant-slugs}]
date_created: {YYYY-MM-DD}
---

# {Analysis Title}

## Question
[What question prompted this synthesis]

## Analysis
[Cross-cutting analysis drawing from multiple wiki pages]

## Sources Referenced
- [[page-name]], [[page-name]]

## Conclusions
[Key takeaways]
```

---

## Special Files

### index.md

Content catalog of everything in the wiki. Organized by type (sources, concepts,
methods, entities, synthesis). Each entry: `| [[page-name]] | year | key topics |`.
The LLM reads this FIRST when answering queries to find relevant pages.

### log.md

Chronological append-only operations record. Format:
```
## [YYYY-MM-DD] {operation} | {title}
- Created N pages
- Updated: [[page-1]], [[page-2]]
```

Operations: `bootstrap`, `ingest`, `query`, `lint`, `sync`, `manual-edit`.

### overview.md

Research identity page. Synthesizes who the researcher is, their expertise areas,
key contributions, and how their work connects across domains.

---

## Workflows

### Ingest (new source)

1. User places PDF in `raw/papers/pdfs/` and adds BibTeX entry to `raw/papers/my_papers.bib`
2. Run: `python scripts/wiki_ingest.py`
3. Pipeline: extract text -> update `data/papers.json` -> add to Neo4j -> tag concepts
4. Generate wiki source page for the new paper
5. Update concept/method pages that the paper touches (add to Sources section)
6. Update `wiki/index.md` with new entry
7. Append to `wiki/log.md`
8. LLM reviews and refines generated pages (summaries, synthesis)

### Query

1. LLM reads `wiki/index.md` to find relevant pages
2. LLM reads those pages and synthesizes an answer
3. If the answer is valuable, file it as `wiki/synthesis/{topic}.md`
4. Update `wiki/index.md` with new synthesis page
5. Append to `wiki/log.md`

### Lint

1. Run: `python scripts/wiki_lint.py`
2. Review report: orphan pages, broken links, Neo4j desync, empty sections
3. Fix issues manually or with `--fix` flag
4. Append to `wiki/log.md`

### Sync

1. Run: `python scripts/wiki_sync.py --direction both`
2. Creates missing wiki pages for Neo4j nodes
3. Creates missing Neo4j nodes for wiki pages with neo4j_id
4. Reports mismatches for manual review

---

## Rules for LLM

1. **NEVER** modify files in `raw/`
2. **ALWAYS** update `wiki/index.md` after creating or modifying a wiki page
3. **ALWAYS** append to `wiki/log.md` after any operation
4. Use `[[wikilinks]]` for ALL cross-references between pages
5. Keep YAML frontmatter accurate and complete
6. Source summaries: 200-400 words
7. Concept pages: synthesize across ALL sources that mention the concept
8. When uncertain about a claim, add `[needs-verification]` inline
9. When new information contradicts existing wiki content, note the contradiction explicitly
10. Prefer updating existing pages over creating new ones
11. Entity pages: only for cohorts and authors appearing in 3+ papers

---

## Pipeline Scripts

| Script | Purpose | When to run |
|--------|---------|-------------|
| `build_graph.py` | Extract data + build Neo4j graph | Initial setup / after adding sources |
| `topic_tagger.py` | Assign broad research topics | After build_graph |
| `concept_extractor.py` | Fine-grained concept/method/disease tagging | After topic_tagger |
| `pdf_extractor.py` | Extract text from PDFs | After adding PDFs to raw/papers/pdfs/ |
| `deduplicate_authors.py` | Merge duplicate Person nodes | After build_graph |
| `visualizer.py` | Generate HTML visualizations | After any graph changes |
| `wiki_bootstrap.py` | One-time wiki generation from Neo4j | Initial setup only |
| `wiki_ingest.py` | End-to-end new source ingestion | When adding a new paper/repo |
| `wiki_sync.py` | Bidirectional wiki <-> Neo4j sync | After manual wiki edits |
| `wiki_lint.py` | Wiki health-check | Periodically |

## Neo4j Connection

```
URI: bolt://localhost:7687
User: neo4j
Password: (in .env as NEO4J_PASSWORD)
Docker: docker run -d --name neo4j-kg -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/knowledge123 neo4j:latest
```
