An "academic" CV compressed into a visual output. The repo contains all the code and instructions on how to replicate it by yourself.

# INSTRUCTIONS

Operational guide for `myKG_expanded`. See [CLAUDE.md](CLAUDE.md) for schema, conventions, and architecture.
All detailed instructions are in `INSTRUCTIONS.md`

---

## Prerequisites

1. **Python venv** — activate `venv/` (already present at repo root).
2. **Dependencies** — installed from `requirements.txt`.
3. **Neo4j** running at localhost.
4. **`.env`** at repo root with:

---

## 1. Add a New Paper

### Inputs you provide
- **PDF** — drop in [raw/papers/pdfs/](raw/papers/pdfs/). Filename should match the BibTeX key or be close to it (`ClosGarcia_JEV_PCa_2018.pdf` style is fine).
- **BibTeX entry** — append to [raw/papers/my_papers.bib](raw/papers/my_papers.bib). Entry key becomes the Neo4j `paper.id` and the wiki filename stem.

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
- Re-tag concepts / methods / diseases against the new abstract + text:
- Review the generated wiki page — refine summary, findings, connections per the [paper page format](CLAUDE.md#source-page----paper).

---

## 2. Add a New GitHub Repo

Repos are pulled via the GitHub API, not filesystem drops. `raw/repos/` is reserved for manual snapshots.

### Post-repo steps
- Link the repo to the paper it implements: add `Implements` entry in the wiki page and let `wiki_sync.py` create the `IMPLEMENTS` edge, or add the edge manually:
- Re-run topic/concept tagging so the repo picks up tags

---

## 3. Update the Graph

Two scopes: **incremental** (after adding one source) and **full rebuild** (periodic, after many changes or schema edits).

### Incremental update (fast)

After `wiki_ingest.py` the new node is already in Neo4j. Refresh derived layers.

### Full rebuild

Run when: schema changed, embeddings stale, multiple sources added, or graph corrupted.

### Sync wiki after graph changes


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

