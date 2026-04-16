# INSTRUCTIONS.md

Operational guide for `myKG_expanded`. See [CLAUDE.md](CLAUDE.md) for schema, conventions, and architecture.

---

## Prerequisites

1. **Python venv** — activate `venv/` (already present at repo root).
   ```bash
   source venv/Scripts/activate   # Windows bash
   ```
2. **Dependencies** — installed from `requirements.txt`.
3. **Neo4j** running at `bolt://localhost:7687`.
   ```bash
   docker run -d --name neo4j-kg -p 7474:7474 -p 7687:7687 \
     -e NEO4J_AUTH=neo4j/knowledge123 neo4j:latest
   ```
4. **`.env`** at repo root with:
   ```
   NEO4J_URI=bolt://localhost:7687
   NEO4J_USER=neo4j
   NEO4J_PASSWORD=knowledge123
   GITHUB_TOKEN=ghp_...           # personal access token, repo:read
   GITHUB_USERNAME=pxtm
   DATA_DIRECTORY=./data
   SIMILARITY_THRESHOLD=0.7
   MAX_REPOS=50
   ```

---

## 1. Add a New Paper

### Inputs you provide
- **PDF** — drop in [raw/papers/pdfs/](raw/papers/pdfs/). Filename should match the BibTeX key or be close to it (`ClosGarcia_JEV_PCa_2018.pdf` style is fine).
- **BibTeX entry** — append to [raw/papers/my_papers.bib](raw/papers/my_papers.bib). Entry key becomes the Neo4j `paper.id` and the wiki filename stem.

Example entry:
```bibtex
@article{smith2026newstudy,
  title={Study title},
  author={Smith, Jane and Doe, John},
  journal={Journal Name},
  year={2026},
  volume={12},
  pages={1-10}
}
```

### Run ingest
```bash
python scripts/wiki_ingest.py                       # auto-detects new BibTeX keys
python scripts/wiki_ingest.py --source paper --id smith2026newstudy   # specific paper
```

What it does:
1. Diffs `my_papers.bib` against `data/papers.json` → finds new keys.
2. Re-parses BibTeX, appends to `data/papers.json`.
3. Adds `Paper` node + `AUTHORED_BY` edges in Neo4j.
4. Generates [wiki/sources/{key}.md](wiki/sources/) from template.
5. Inserts the new paper into relevant concept/method page "Sources" sections.
6. Regenerates [wiki/index.md](wiki/index.md) and appends to [wiki/log.md](wiki/log.md).

### After ingest
- Extract full text from the PDF (enables semantic concept extraction):
  ```bash
  python scripts/pdf_extractor.py
  ```
- Re-tag concepts / methods / diseases against the new abstract + text:
  ```bash
  python scripts/topic_tagger.py
  python scripts/concept_extractor.py
  ```
- Review the generated wiki page — refine summary, findings, connections per the [paper page format](CLAUDE.md#source-page----paper).

---

## 2. Add a New GitHub Repo

Repos are pulled via the GitHub API, not filesystem drops. `raw/repos/` is reserved for manual snapshots.

### A. Repo under your tracked GitHub user (automatic)

1. Push the repo to `github.com/{GITHUB_USERNAME}` (defaults to `pxtm`).
2. Make sure it's public, or that `GITHUB_TOKEN` has `repo` scope for private repos.
3. Re-run extraction + graph build:
   ```bash
   python scripts/build_graph.py
   ```
   `GitHubExtractor` refetches up to `MAX_REPOS` repos, updates `data/github_repos.json`, and upserts `GithubRepo` nodes.

### B. Repo under a different owner (manual)

No first-class flow — add it via one of:
- **Clone into `raw/repos/{repo-name}/`** as an immutable snapshot and add a wiki page at [wiki/sources/{repo-name}.md](wiki/sources/) using the [repo page format](CLAUDE.md#source-page----repo). Then run `wiki_sync.py` to mint the Neo4j node.
- **Edit `data/github_repos.json`** directly (append the repo dict) and re-run `build_graph.py`.

### Post-repo steps
- Link the repo to the paper it implements: add `Implements` entry in the wiki page and let `wiki_sync.py` create the `IMPLEMENTS` edge, or add the edge manually:
  ```cypher
  MATCH (r:GithubRepo {id: "pxtm/fibromyalgia"}), (p:Paper {id: "clos2019fibromyalgia"})
  CREATE (r)-[:IMPLEMENTS]->(p);
  ```
- Re-run topic/concept tagging so the repo picks up tags:
  ```bash
  python scripts/topic_tagger.py
  python scripts/concept_extractor.py
  ```

---

## 3. Update the Graph

Two scopes: **incremental** (after adding one source) and **full rebuild** (periodic, after many changes or schema edits).

### Incremental update (fast)

After `wiki_ingest.py` the new node is already in Neo4j. Refresh derived layers:

```bash
python scripts/pdf_extractor.py          # if PDF added
python scripts/topic_tagger.py           # broad topics
python scripts/concept_extractor.py      # fine-grained concepts/methods/diseases
python scripts/deduplicate_authors.py    # merge duplicate Person nodes
python scripts/wiki_sync.py --direction both   # reconcile wiki ↔ Neo4j
```

### Full rebuild

Run when: schema changed, embeddings stale, multiple sources added, or graph corrupted.

```bash
# 1. (Optional) wipe the DB in Neo4j Browser:
#    MATCH (n) DETACH DELETE n;

# 2. Extract + build core graph (GitHub + papers + notes)
python scripts/build_graph.py

# 3. Enrichment
python scripts/pdf_extractor.py
python scripts/topic_tagger.py
python scripts/concept_extractor.py
python scripts/deduplicate_authors.py

# 4. Visualizations
python scripts/visualizer.py             # writes to visualizations/
```

### Sync wiki after graph changes

```bash
python scripts/wiki_sync.py --direction graph-to-wiki   # create missing wiki pages
python scripts/wiki_lint.py                             # find orphans, broken links, desync
python scripts/wiki_lint.py --fix                       # auto-fix where possible
```

### Full wiki regeneration (destructive)

Only when templates or bootstrap logic changed:
```bash
python scripts/wiki_bootstrap.py --force
```
This overwrites every generated page. Synthesis pages under [wiki/synthesis/](wiki/synthesis/) are preserved (no `neo4j_id`).

---

## Quick Reference

| Task | Command |
|------|---------|
| Add paper | drop PDF + edit `.bib` → `python scripts/wiki_ingest.py` |
| Add GitHub repo (own user) | push to GitHub → `python scripts/build_graph.py` |
| Re-tag all sources | `python scripts/topic_tagger.py && python scripts/concept_extractor.py` |
| Full rebuild | wipe Neo4j → `build_graph.py` → enrichment scripts |
| Wiki health-check | `python scripts/wiki_lint.py` |
| Reconcile wiki ↔ graph | `python scripts/wiki_sync.py --direction both` |
| Regenerate visualizations | `python scripts/visualizer.py` |

---

## Troubleshooting

- **`No BibTeX file found`** — check the entry is in [raw/papers/my_papers.bib](raw/papers/my_papers.bib), not a different file.
- **`Paper {id} not found in BibTeX file`** — the `--id` you passed doesn't match an `@article{key, ...}` key. Check casing and punctuation.
- **Neo4j connection refused** — container not running. `docker start neo4j-kg`.
- **GitHub extraction fails** — `GITHUB_TOKEN` missing, expired, or lacks `repo` scope.
- **Wiki pages out of sync with graph** — run `wiki_lint.py` first to see the report, then `wiki_sync.py --direction both`.
