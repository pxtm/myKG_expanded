"""
Wiki page templates and helper utilities.
Maps Neo4j node IDs to wiki file paths. Renders markdown pages via Jinja2.
"""

import re
import yaml
from pathlib import Path
from typing import Dict, List, Optional
from jinja2 import Environment, BaseLoader

# ---------------------------------------------------------------------------
# Slug helpers
# ---------------------------------------------------------------------------

def slug_from_name(name: str) -> str:
    """Convert display name to a wiki-safe slug.
    'UPLC-MS' -> 'uplc-ms', 'Type 1 Diabetes' -> 'type-1-diabetes'
    """
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9\s\-]", "", s)
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-{2,}", "-", s)
    return s.strip("-")


def neo4j_id_to_wiki_path(neo4j_id: str, wiki_root: Path) -> Path:
    """Map a Neo4j node ID to its wiki file path."""
    if neo4j_id.startswith("concept:method:"):
        slug = neo4j_id.split(":", 2)[2].replace("_", "-")
        return wiki_root / "methods" / f"{slug}.md"
    elif neo4j_id.startswith("concept:disease:") or neo4j_id.startswith("concept:biology:"):
        slug = neo4j_id.split(":", 2)[2].replace("_", "-")
        return wiki_root / "concepts" / f"{slug}.md"
    elif neo4j_id.startswith("concept:cohort:"):
        slug = neo4j_id.split(":", 2)[2].replace("_", "-")
        return wiki_root / "entities" / f"{slug}.md"
    elif neo4j_id.startswith("concept:"):
        # broad research topics (concept:microbiome, concept:metabolomics, etc.)
        slug = neo4j_id.split(":", 1)[1].replace("_", "-")
        return wiki_root / "concepts" / f"{slug}.md"
    elif "/" in neo4j_id:
        # GitHub repo: 'pxtm/PROTON' -> wiki/sources/pxtm-proton.md
        owner, name = neo4j_id.split("/", 1)
        slug = f"{owner}-{name}".lower().replace("_", "-")
        return wiki_root / "sources" / f"{slug}.md"
    else:
        # paper bibtex key -> wiki/sources/{key}.md
        return wiki_root / "sources" / f"{neo4j_id}.md"


def neo4j_id_to_wikilink(neo4j_id: str) -> str:
    """Return the [[wikilink]] name for a Neo4j ID (filename stem)."""
    path = neo4j_id_to_wiki_path(neo4j_id, Path("wiki"))
    return path.stem


def wiki_path_to_wikilink(path: Path) -> str:
    """Extract [[wikilink]] name from a wiki file path."""
    return path.stem


# ---------------------------------------------------------------------------
# Frontmatter helpers
# ---------------------------------------------------------------------------

def render_frontmatter(data: dict) -> str:
    """Render a YAML frontmatter block."""
    # Use block style for readability but keep lists inline
    lines = ["---"]
    for key, value in data.items():
        if isinstance(value, list):
            items = ", ".join(
                f'"{v}"' if isinstance(v, str) and ("[[" in v or " " in v) else str(v)
                for v in value
            )
            lines.append(f"{key}: [{items}]")
        elif isinstance(value, str) and "\n" in value:
            lines.append(f'{key}: "{value}"')
        elif isinstance(value, str):
            lines.append(f'{key}: "{value}"')
        elif isinstance(value, (int, float)):
            lines.append(f"{key}: {value}")
        elif value is None:
            lines.append(f"{key}:")
        else:
            lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Jinja2 template environment
# ---------------------------------------------------------------------------

_env = Environment(loader=BaseLoader(), trim_blocks=True, lstrip_blocks=True)

# ---------------------------------------------------------------------------
# Page templates
# ---------------------------------------------------------------------------

SOURCE_PAPER_TEMPLATE = _env.from_string("""\
{{ frontmatter }}

# {{ title }}

## Summary

{{ abstract }}

## Key Findings

{% for finding in findings %}
- {{ finding }}
{% endfor %}
{% if not findings %}
- [To be filled after detailed review]
{% endif %}

## Methods

{% for m in methods %}
- [[{{ m.link }}]] -- {{ m.name }}
{% endfor %}
{% if not methods %}
- [No methods tagged yet]
{% endif %}

## Connections

{% if related_papers %}
### Related Papers
{% for r in related_papers %}
- [[{{ r.link }}]] (similarity: {{ r.score }})
{% endfor %}
{% endif %}

### Concepts
{% for c in concepts %}
- [[{{ c.link }}]]{% if c.rel_type %} ({{ c.rel_type }}){% endif %}

{% endfor %}
{% if implements %}

### Implemented By
{% for repo in implements %}
- [[{{ repo }}]]
{% endfor %}
{% endif %}

## Notes

[Additional observations to be added]
""")

SOURCE_REPO_TEMPLATE = _env.from_string("""\
{{ frontmatter }}

# {{ name }}

## Description

{{ description }}

{% if implements %}
## Implements
{% for p in implements %}
- [[{{ p.link }}]] -- {{ p.note }}
{% endfor %}
{% endif %}

## Technologies
{% for m in methods %}
- [[{{ m.link }}]] -- {{ m.name }}
{% endfor %}
{% if concepts %}

## Related Concepts
{% for c in concepts %}
- [[{{ c.link }}]]
{% endfor %}
{% endif %}

## Notes

[Additional context]
""")

CONCEPT_TEMPLATE = _env.from_string("""\
{{ frontmatter }}

# {{ name }}

## Overview

{{ overview }}

## Sources

{% for s in sources %}
- [[{{ s.link }}]] -- {{ s.summary }} ({{ s.year }})
{% endfor %}
{% if not sources %}
- [No sources tagged yet]
{% endif %}

## Key Findings Across Sources

[To be synthesized from the sources above]

{% if methods %}
## Methods Used
{% for m in methods %}
- [[{{ m }}]]
{% endfor %}
{% endif %}

## Related Concepts
{% for c in related %}
- [[{{ c }}]]
{% endfor %}

## Open Questions

- [What remains unknown or needs investigation]
""")

METHOD_TEMPLATE = _env.from_string("""\
{{ frontmatter }}

# {{ name }}

## Description

{{ description }}

## Used In

{% for s in sources %}
- [[{{ s.link }}]] -- {{ s.summary }} ({{ s.year }})
{% endfor %}
{% if not sources %}
- [No sources tagged yet]
{% endif %}

{% if related_methods %}
## Related Methods
{% for m in related_methods %}
- [[{{ m }}]]
{% endfor %}
{% endif %}
""")

ENTITY_TEMPLATE = _env.from_string("""\
{{ frontmatter }}

# {{ name }}

## Description

{{ description }}

## Appears In

{% for s in sources %}
- [[{{ s.link }}]]{% if s.year %} ({{ s.year }}){% endif %}

{% endfor %}

{% if related %}
## Related
{% for c in related %}
- [[{{ c }}]]
{% endfor %}
{% endif %}
""")

OVERVIEW_TEMPLATE = _env.from_string("""\
---
type: overview
---

# Research Overview -- Marc Clos-Garcia

## Research Identity

Biomedical researcher specializing in **metabolomics**, **multi-omics integration**,
**microbiome research**, and **biomarker discovery**. Work spans multiple disease
areas including colorectal cancer, prostate cancer, fibromyalgia, diabetes (T1D/T2D),
and liver disease.

## Expertise Areas

{% for area in areas %}
### {{ area.name }}
{{ area.description }}
**Key papers:** {% for p in area.papers %}[[{{ p }}]]{% if not loop.last %}, {% endif %}{% endfor %}

{% endfor %}

## GitHub Repositories

{% for repo in repos %}
- [[{{ repo.link }}]] -- {{ repo.description }}
{% endfor %}

## Publication Timeline

{% for year, papers in timeline.items() %}
### {{ year }}
{% for p in papers %}
- [[{{ p.link }}]] -- {{ p.title_short }}
{% endfor %}
{% endfor %}
""")

# ---------------------------------------------------------------------------
# Template registry
# ---------------------------------------------------------------------------

TEMPLATES = {
    "source_paper": SOURCE_PAPER_TEMPLATE,
    "source_repo": SOURCE_REPO_TEMPLATE,
    "concept": CONCEPT_TEMPLATE,
    "method": METHOD_TEMPLATE,
    "entity": ENTITY_TEMPLATE,
    "overview": OVERVIEW_TEMPLATE,
}


def render_page(page_type: str, context: dict) -> str:
    """Render a wiki page from a template type and context dict."""
    template = TEMPLATES[page_type]
    return template.render(**context)
