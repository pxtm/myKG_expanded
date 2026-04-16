"""
deduplicate_authors.py

Deduplicates Person nodes in Neo4j by:
1. Grouping nodes whose (normalized_last_name, first_initial) match
2. Keeping the most complete name as canonical; redirecting all AUTHORED_BY
   edges from duplicates to the canonical, then deleting duplicates
3. Stripping LaTeX escape sequences from remaining .name fields
4. Removing the garbage 'others' node

Idempotent: safe to re-run.
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")

import re
import os
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, "scripts")
from graph_db import KnowledgeGraph


# ── LaTeX helpers ──────────────────────────────────────────────────────────────

def _strip_latex(s: str) -> str:
    """
    Strip LaTeX escape sequences, preserving the base letter where possible.

    Strategy (in order):
    1. \\i / \\j (dotless-letter commands) → keep the letter
    2. \\<accent> (\\' \\" \\~ \\^ \\` \\= \\.) — accent-only commands → remove
    3. Remaining \\<word> commands → remove entirely
    4. Braces → remove
    """
    # Step 1: letter-valued LaTeX commands → keep the letter
    #   \i → i, \j → j (dotless), \o → o, \O → O (Scandinavian), \ae → ae, etc.
    s = re.sub(r"\\([ijloOaA])\b", r"\1", s)
    s = re.sub(r"\\ae\b", "ae", s, flags=re.I)
    s = re.sub(r"\\oe\b", "oe", s, flags=re.I)
    s = re.sub(r"\\aa\b", "aa", s, flags=re.I)
    # Step 2: accent-only single-char commands (brace-less uses): \' \" \~ etc.
    s = re.sub(r"\\['\"`^~=.]", "", s)
    # Step 3: remaining backslash-word commands
    s = re.sub(r"\\[a-zA-Z]+", "", s)
    # Step 4: braces
    s = re.sub(r"[{}]", "", s)
    return s.strip()


def _clean_name(raw: str) -> str:
    """Human-readable name: strip LaTeX, fix ALL-CAPS."""
    name = _strip_latex(raw).strip()
    if name == name.upper() and len(name) > 2:
        name = name.title()
    return name


# ── Normalization key ──────────────────────────────────────────────────────────

def _norm_key(raw_id: str):
    """
    (normalized_last, first_initial) for grouping.

    ID format examples:
        clos-garcia,_marc
        clos-garc{\\'\\i}a,_marc   <- LaTeX variant
        clos-garcia,_m             <- initial-only
    """
    clean = _strip_latex(raw_id).lower()
    parts = clean.split(",", 1)
    last = re.sub(r"[^a-z]", "", parts[0])          # strip all non-alpha
    first_raw = parts[1].lstrip("_").strip() if len(parts) > 1 else ""
    first = re.sub(r"[^a-z]", "", first_raw)
    initial = first[0] if first else ""
    return (last, initial)


# ── Main ────────────────────────────────────────────────────────────────────────

def deduplicate(kg: KnowledgeGraph) -> None:

    # 1. Load all Person nodes ─────────────────────────────────────────────────
    res = kg.execute_query("MATCH (p:Person) RETURN p.id AS pid, p.name AS pname")
    all_persons = [(r["pid"], r["pname"] or "") for r in res if r["pid"] != "others"]
    print(f"Loaded {len(all_persons)} Person nodes (excluding 'others')")

    # 2. Group by normalized key ───────────────────────────────────────────────
    groups: dict = defaultdict(list)
    for pid, pname in all_persons:
        key = _norm_key(pid)
        if key[0]:
            groups[key].append((pid, pname))

    dup_groups = {k: v for k, v in groups.items() if len(v) > 1}
    print(f"Found {len(dup_groups)} duplicate groups\n")

    # 3. Merge each duplicate group ────────────────────────────────────────────
    merged_total = 0
    for key, candidates in sorted(dup_groups.items()):
        # Count AUTHORED_BY edges per node
        rel_counts = {}
        for pid, _ in candidates:
            cr = kg.execute_query(
                "MATCH ()-[:AUTHORED_BY]->(p:Person {id: $pid}) RETURN count(*) AS n",
                {"pid": pid},
            )
            rel_counts[pid] = cr[0]["n"] if cr else 0

        # Canonical: longest first name → clean ID (no LaTeX) → most rels
        def _score(pid_name):
            pid, _ = pid_name
            clean = _strip_latex(pid).lower()
            first_raw = clean.split(",", 1)[1].lstrip("_") if "," in clean else ""
            first_len = len(re.sub(r"[^a-z]", "", first_raw))
            no_latex = 0 if ("{" in pid or "\\" in pid) else 1  # 1=clean, 0=dirty
            return (first_len, no_latex, rel_counts.get(pid, 0))

        candidates.sort(key=_score, reverse=True)
        canonical_id, canonical_name = candidates[0]
        duplicates = candidates[1:]

        clean_display = _clean_name(canonical_name)
        print(f"  [{key[0]}, {key[1]}]  canonical -> {canonical_id!r}  ({clean_display})")

        for dup_id, dup_name in duplicates:
            print(f"    merge  <- {dup_id!r}  ({_clean_name(dup_name)})")
            # Redirect AUTHORED_BY from duplicate to canonical
            kg.execute_query(
                """
                MATCH (src)-[r:AUTHORED_BY]->(dup:Person {id: $dup_id})
                MATCH (can:Person {id: $can_id})
                MERGE (src)-[:AUTHORED_BY]->(can)
                DELETE r
                """,
                {"dup_id": dup_id, "can_id": canonical_id},
            )
            # Delete orphaned duplicate
            kg.execute_query(
                "MATCH (d:Person {id: $dup_id}) DETACH DELETE d",
                {"dup_id": dup_id},
            )
            merged_total += 1

        # Update canonical's display name to clean version
        kg.execute_query(
            "MATCH (p:Person {id: $pid}) SET p.name = $name",
            {"pid": canonical_id, "name": clean_display},
        )

    print(f"\n  Merged {merged_total} duplicate Person nodes")

    # 4. Delete 'others' garbage node ─────────────────────────────────────────
    kg.execute_query("MATCH (d:Person {id: 'others'}) DETACH DELETE d")
    print("  Deleted 'others' Person node")

    # 5. Fix ALL display names: re-derive from the node ID using correct stripping
    #    (catches both LaTeX-in-name and earlier buggy stripping artefacts)
    res = kg.execute_query("MATCH (p:Person) RETURN p.id AS pid, p.name AS pname")
    cleaned = 0
    for r in res:
        pid = r["pid"]
        raw_name = r["pname"] or ""
        # Determine canonical display name from the ID (more reliable than the .name field)
        clean_id = _strip_latex(pid)
        if "," in clean_id:
            last_part, first_part = clean_id.split(",", 1)
            first_part = first_part.lstrip("_").strip()
            # Last name: handle hyphen-compound and space-compound names
            last_clean = last_part.replace("_", " ")
            last_display = "-".join(
                " ".join(w.capitalize() for w in seg.split())
                for seg in last_clean.split("-")
            )
            # First name: replace underscores with spaces, title-case each word
            first_display = " ".join(
                w.capitalize() for w in first_part.replace("_", " ").split()
            )
            display = f"{last_display}, {first_display}"
        else:
            display = _clean_name(raw_name)
        if display != raw_name:
            kg.execute_query(
                "MATCH (p:Person {id: $pid}) SET p.name = $name",
                {"pid": pid, "name": display},
            )
            cleaned += 1
    print(f"  Updated display names on {cleaned} Person nodes")

    # 6. Final count ───────────────────────────────────────────────────────────
    res = kg.execute_query("MATCH (p:Person) RETURN count(p) AS n")
    print(f"\n  Final Person count: {res[0]['n']}")


def main():
    kg = KnowledgeGraph(
        uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        user=os.getenv("NEO4J_USER", "neo4j"),
        password=os.getenv("NEO4J_PASSWORD", "knowledge123"),
    )
    try:
        deduplicate(kg)
    finally:
        kg.close()


if __name__ == "__main__":
    main()
