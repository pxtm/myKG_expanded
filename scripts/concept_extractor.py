"""
Concept extractor for the knowledge graph.

Five phases:
  1. Disease/condition concept nodes  -> MENTIONS         (Paper/Repo -> Concept)
  2. Method/technology concept nodes  -> USES_TECHNOLOGY  (Paper/Repo -> Concept)
  3. Biological concept nodes         -> MENTIONS         (Paper/Repo -> Concept)
  4. Repo tagging (all three concept categories above applied to repos)
  5. Paper-paper semantic similarity  -> RELATES_TO       (Paper -> Paper)

Concept lists are derived from the actual content of the papers and repos in
this collection. Add new entries freely — re-runs are fully idempotent (MERGE).

Usage:
    cd C:/Users/mcgma/Desktop/myKG
    ./venv/Scripts/python.exe scripts/concept_extractor.py
"""

import json
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple

from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).parent))
from graph_db import KnowledgeGraph
from semantic_analyzer import SemanticAnalyzer

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=True)

SIMILARITY_THRESHOLD = 0.60   # cosine similarity for RELATES_TO edges

# ── Disease / Condition Concepts ──────────────────────────────────────────
DISEASES: Dict[str, Dict] = {
    # Cancers
    "Colorectal Cancer": {
        "id": "concept:disease:colorectal_cancer",
        "keywords": [
            "colorectal cancer", "colon cancer", "rectal cancer", "crc",
            "colorectal carcinoma", "advanced adenoma", "colorectal neoplasia",
            "colorectal polyp", "colonic adenoma",
        ],
    },
    "Prostate Cancer": {
        "id": "concept:disease:prostate_cancer",
        "keywords": [
            "prostate cancer", "pca", "prostate carcinoma",
            "benign prostate hyperplasia", "bph", "prostate pathology",
        ],
    },
    "Endometrial Pathology": {
        "id": "concept:disease:endometrial",
        "keywords": [
            "endometrium", "endometrial", "endometriosis", "uterine",
            "implantative endometrium", "embryo implantation",
            "endometrial receptivity",
        ],
    },
    # Metabolic & Endocrine
    "Type 1 Diabetes": {
        "id": "concept:disease:type1_diabetes",
        "keywords": [
            "type 1 diabetes", "t1d", "type i diabetes",
            "autoimmune diabetes", "insulin-dependent diabetes",
        ],
    },
    "Type 2 Diabetes": {
        "id": "concept:disease:type2_diabetes",
        "keywords": [
            "type 2 diabetes", "t2d", "type ii diabetes", "t2dm",
            "non-insulin-dependent diabetes",
        ],
    },
    "Prediabetes": {
        "id": "concept:disease:prediabetes",
        "keywords": [
            "prediabetes", "prediabetic", "impaired fasting glucose",
            "impaired glucose tolerance", "igt", "ifg",
        ],
    },
    "Diabetic Kidney Disease": {
        "id": "concept:disease:diabetic_kidney_disease",
        "keywords": [
            "diabetic kidney disease", "dkd", "diabetic nephropathy",
            "albuminuria", "renal dysfunction", "end-stage renal",
        ],
    },
    "Obesity & Metabolic Syndrome": {
        "id": "concept:disease:obesity_mets",
        "keywords": [
            "obesity", "obese", "overweight", "adiposity",
            "metabolic syndrome", "insulin resistance",
            "dyslipidemia", "hyperinsulinemia",
        ],
    },
    # Liver
    "Alcohol-Associated Liver Disease": {
        "id": "concept:disease:alcohol_liver_disease",
        "keywords": [
            "alcohol-associated liver disease", "aald", "alcoholic liver",
            "alcoholic hepatitis", "alcohol-related liver",
        ],
    },
    "Non-Alcoholic Fatty Liver": {
        "id": "concept:disease:nafld",
        "keywords": [
            "nash", "nafld", "non-alcoholic fatty liver", "steatohepatitis",
            "steatotic hepatocytes", "hepatic steatosis", "fatty liver",
            "hepatic lipid accumulation",
        ],
    },
    "Liver Fibrosis": {
        "id": "concept:disease:liver_fibrosis",
        "keywords": [
            "liver fibrosis", "hepatic fibrosis", "fibrosis",
            "liver cirrhosis", "hepatic stellate",
        ],
    },
    # Neurological
    "Dementia & Neurodegeneration": {
        "id": "concept:disease:dementia",
        "keywords": [
            "dementia", "alzheimer", "cognitive decline",
            "cognitive impairment", "mild cognitive impairment", "mci",
            "neurodegeneration", "neurodegenerative",
        ],
    },
    # Pain / Musculoskeletal
    "Fibromyalgia": {
        "id": "concept:disease:fibromyalgia",
        "keywords": [
            "fibromyalgia", "fibromyalgia syndrome", "fms",
            "widespread pain", "chronic pain", "tender point",
        ],
    },
    # Infectious
    "COVID-19": {
        "id": "concept:disease:covid19",
        "keywords": ["covid-19", "covid19", "sars-cov-2", "coronavirus", "pandemic"],
    },
}

# ── Method / Technology Concepts ──────────────────────────────────────────
METHODS: Dict[str, Dict] = {
    # Mass spectrometry & metabolomics platforms
    "UPLC-MS": {
        "id": "concept:method:uplcms",
        "keywords": [
            "uplc", "uplc-ms", "uplc-tof", "ultra-performance liquid chromatography",
            "uhplc", "uplc-ms/ms",
        ],
    },
    "LC-MS/MS": {
        "id": "concept:method:lcmsms",
        "keywords": [
            "lc-ms/ms", "lc-ms", "liquid chromatography-mass spectrometry",
        ],
    },
    "GC-MS": {
        "id": "concept:method:gcms",
        "keywords": [
            "gc-ms", "gas chromatography-mass spectrometry", "gcms", "gc-qtof",
        ],
    },
    "NMR Spectroscopy": {
        "id": "concept:method:nmr",
        "keywords": ["nmr", "nuclear magnetic resonance", "1h nmr", "proton nmr"],
    },
    "Olink Proteomics": {
        "id": "concept:method:olink",
        "keywords": [
            "olink", "proximity extension assay", "pea",
            "olink explore", "olink target",
        ],
    },
    # Sequencing
    "16S rRNA Sequencing": {
        "id": "concept:method:16s_rrna",
        "keywords": ["16s rrna", "16s", "amplicon sequencing", "v3-v4", "v4 region"],
    },
    "Shotgun Metagenomics": {
        "id": "concept:method:shotgun_metagenomics",
        "keywords": [
            "shotgun metagenomics", "shotgun sequencing", "whole metagenome",
            "wgs", "whole genome shotgun", "metagenomic sequencing",
        ],
    },
    "Oxford Nanopore": {
        "id": "concept:method:nanopore",
        "keywords": [
            "nanopore", "oxford nanopore", "ont sequencing",
            "long-read sequencing", "guppy", "basecalling",
        ],
    },
    "RNA-seq": {
        "id": "concept:method:rnaseq",
        "keywords": [
            "rna-seq", "rna sequencing", "rnaseq",
            "transcriptome sequencing", "mrna sequencing",
        ],
    },
    "miRNA Profiling": {
        "id": "concept:method:mirna_profiling",
        "keywords": [
            "microrna profiling", "mirna profiling", "small rna",
            "mirna sequencing", "mirna array", "mirna panel",
        ],
    },
    # Proteomics
    "Proteomics": {
        "id": "concept:method:proteomics",
        "keywords": [
            "proteomics", "proteomic", "protein profiling",
            "tandem mass tag", "tmt", "label-free quantification",
            "data-independent acquisition", "dia",
        ],
    },
    # EV methods
    "EV Isolation": {
        "id": "concept:method:ev_isolation",
        "keywords": [
            "extracellular vesicle isolation", "exosome isolation",
            "ultracentrifugation", "size exclusion chromatography",
            "nanoparticle tracking", "nanosight", "ev purification",
        ],
    },
    # Statistics / ML
    "Multivariate Statistics": {
        "id": "concept:method:multivariate",
        "keywords": [
            "pca", "pls-da", "principal component", "partial least squares",
            "multivariate analysis", "discriminant analysis", "opls-da",
        ],
    },
    "Random Forest": {
        "id": "concept:method:random_forest",
        "keywords": ["random forest", "random forests", "rf classifier"],
    },
    "LASSO / Elastic Net": {
        "id": "concept:method:lasso",
        "keywords": ["lasso", "elastic net", "penalized regression", "regularization", "glmnet"],
    },
    "GWAS": {
        "id": "concept:method:gwas",
        "keywords": [
            "gwas", "genome-wide association", "snp",
            "single nucleotide polymorphism",
        ],
    },
    "Mendelian Randomization": {
        "id": "concept:method:mendelian_randomization",
        "keywords": [
            "mendelian randomization", "mendelian randomisation",
            "instrumental variable", "iv analysis",
        ],
    },
    "Multi-Omics Integration": {
        "id": "concept:method:multi_omics_integration",
        "keywords": [
            "multi-omics integration", "multiomics integration",
            "data integration", "integrative analysis",
            "cross-omics", "multi-modal integration",
        ],
    },
    "Functional Pathway Analysis": {
        "id": "concept:method:pathway_analysis",
        "keywords": [
            "pathway analysis", "kegg", "functional profiling",
            "gene ontology", "metabolic pathway", "enrichment analysis",
        ],
    },
    "Longitudinal Study": {
        "id": "concept:method:longitudinal",
        "keywords": [
            "longitudinal", "follow-up", "prospective cohort",
            "4-year", "time point", "repeated measures",
        ],
    },
    "Randomized Controlled Trial": {
        "id": "concept:method:rct",
        "keywords": [
            "randomized controlled trial", "rct", "randomised",
            "placebo", "crossover trial", "clinical trial",
        ],
    },
}

# ── Biological Concept Nodes ──────────────────────────────────────────────
BIO_CONCEPTS: Dict[str, Dict] = {
    # Core biological themes
    "Extracellular Vesicles": {
        "id": "concept:biology:extracellular_vesicles",
        "keywords": [
            "extracellular vesicle", "extracellular vesicles", "exosome",
            "microvesicle", "vesicle-associated", "ev-associated", "ev-mediated",
        ],
    },
    "Gut Microbiota": {
        "id": "concept:biology:gut_microbiota",
        "keywords": [
            "gut microbiota", "gut microbiome", "intestinal microbiota",
            "gut bacteria", "gut flora", "fecal microbiota", "fecal microbiome",
        ],
    },
    "Gut-Brain Axis": {
        "id": "concept:biology:gut_brain_axis",
        "keywords": [
            "gut-brain axis", "gut brain axis", "microbiome-gut-brain",
            "microbiota-brain", "intestinal-brain",
        ],
    },
    "Biomarker Discovery": {
        "id": "concept:biology:biomarker_discovery",
        "keywords": [
            "biomarker", "biomarkers", "diagnostic biomarker",
            "non-invasive biomarker", "circulating biomarker",
            "candidate marker", "molecular signature",
        ],
    },
    "Lipid Metabolism": {
        "id": "concept:biology:lipid_metabolism",
        "keywords": [
            "lipid metabolism", "lipid profiling", "fatty acid", "glycerolipid",
            "lipidomics", "sphingolipid", "phospholipid", "triglyceride", "cholesterol",
        ],
    },
    "Glutamate Metabolism": {
        "id": "concept:biology:glutamate_metabolism",
        "keywords": [
            "glutamate", "glutamine", "glutamate metabolism",
            "gaba", "neurotransmitter", "excitatory amino acid",
        ],
    },
    "Steroid Hormones": {
        "id": "concept:biology:steroid_hormones",
        "keywords": [
            "steroid hormone", "glucocorticoid", "cortisol", "prednisolone",
            "testosterone", "estradiol", "estrone", "progesterone",
            "androgen", "estrogen", "sex hormone",
        ],
    },
    "Mitochondrial Function": {
        "id": "concept:biology:mitochondrial_function",
        "keywords": [
            "mitochondria", "mitochondrial", "mcj",
            "mitochondrial activity", "oxidative phosphorylation",
            "mitochondrial dysfunction", "atp production",
        ],
    },
    "Insulin Signaling": {
        "id": "concept:biology:insulin_signaling",
        "keywords": [
            "insulin signaling", "insulin resistance", "insulin sensitivity",
            "glucose uptake", "glut4", "irs", "pi3k", "akt", "insulin pathway",
        ],
    },
    "Microbiota Dysbiosis": {
        "id": "concept:biology:dysbiosis",
        "keywords": [
            "dysbiosis", "microbial dysbiosis", "gut dysbiosis",
            "altered microbiota", "microbiota imbalance", "reduced diversity",
        ],
    },
    "Cell-Cell Crosstalk": {
        "id": "concept:biology:cell_crosstalk",
        "keywords": [
            "cell-cell crosstalk", "intercellular communication",
            "paracrine", "autocrine", "tgf-beta", "erbb2", "signaling crosstalk",
        ],
    },
    "Inflammation Pathways": {
        "id": "concept:biology:inflammation_pathways",
        "keywords": [
            "inflammatory pathway", "nf-kb", "cytokine", "interleukin",
            "tnf", "immune activation", "inflammasome",
            "inflammatory marker", "c-reactive protein",
        ],
    },
    "miRNA Regulation": {
        "id": "concept:biology:mirna_regulation",
        "keywords": [
            "microrna", "mirna", "mir-", "small rna",
            "rna interference", "post-transcriptional", "mirna expression",
        ],
    },
    # Biological fluids / sample types
    "Fecal Biomarkers": {
        "id": "concept:biology:fecal_biomarkers",
        "keywords": [
            "fecal", "faecal", "stool", "fecal sample",
            "stool sample", "fecal metabolomics", "fecal microbiota",
        ],
    },
    "Urine Biomarkers": {
        "id": "concept:biology:urine_biomarkers",
        "keywords": [
            "urine", "urinary", "urine sample",
            "urine metabolomics", "urinary metabolome", "urine extracellular",
        ],
    },
    "Blood & Serum Biomarkers": {
        "id": "concept:biology:blood_biomarkers",
        "keywords": [
            "serum", "plasma", "blood sample", "circulating",
            "serum metabolome", "plasma metabolome", "circulating proteome",
        ],
    },
    "Cerebrospinal Fluid": {
        "id": "concept:biology:csf",
        "keywords": [
            "cerebrospinal fluid", "csf", "spinal fluid",
            "csf biomarker", "lumbar puncture",
        ],
    },
    # Named cohorts
    "IMI-DIRECT Cohort": {
        "id": "concept:cohort:imi_direct",
        "keywords": ["imi-direct", "imi direct", "direct study", "imidirect"],
    },
    "PROTON Study": {
        "id": "concept:cohort:proton",
        "keywords": ["proton", "proton study", "proton cohort", "t1d proton"],
    },
}

# ── Detection helpers ─────────────────────────────────────────────────────

def _paper_search_text(paper: Dict) -> str:
    return " ".join(filter(None, [
        paper.get("title", ""),
        paper.get("abstract", ""),
        " ".join(paper.get("keywords", []) or []),
        paper.get("venue", ""),
    ])).lower()


def _repo_search_text(repo: Dict) -> str:
    return " ".join(filter(None, [
        repo.get("name", ""),
        repo.get("description", "") or "",
        repo.get("readme", "") or "",
        " ".join(repo.get("topics", []) or []),
    ])).lower()


def _detect(text_lower: str, term_defs: Dict) -> List[Tuple[str, str, int]]:
    """Return [(concept_id, display_name, hit_count)] for all matching concepts."""
    hits = []
    for name, defn in term_defs.items():
        count = sum(1 for kw in defn["keywords"] if kw in text_lower)
        if count > 0:
            hits.append((defn["id"], name, count))
    return hits


# ── Neo4j helpers ─────────────────────────────────────────────────────────

def upsert_concepts(kg: KnowledgeGraph, term_defs: Dict, category: str):
    """Create/update Concept nodes for all entries in a term dict."""
    for name, defn in term_defs.items():
        kg.add_node("Concept", {
            "id":       defn["id"],
            "name":     name,
            "category": category,
            "keywords": defn["keywords"],
        })
    logger.info(f"  Upserted {len(term_defs)} {category} concept nodes")


def tag_items(
    kg: KnowledgeGraph,
    items: List[Dict],
    label: str,
    text_fn,
    term_defs: Dict,
    rel_type: str,
    category: str,
) -> int:
    """Detect terms in each item's text and create relationships."""
    count = 0
    for item in items:
        text = text_fn(item)
        for cid, name, score in _detect(text, term_defs):
            kg.add_relationship(
                from_id=item["id"],
                to_id=cid,
                rel_type=rel_type,
                from_label=label,
                to_label="Concept",
                properties={"score": score, "source": "concept_extractor", "category": category},
            )
            count += 1
    return count


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    data_dir = Path(__file__).parent.parent / "data"

    with open(data_dir / "papers.json", encoding="utf-8") as f:
        papers = json.load(f)
    with open(data_dir / "github_repos.json", encoding="utf-8") as f:
        repos = json.load(f)
    logger.info(f"Loaded {len(papers)} papers, {len(repos)} repos")

    kg = KnowledgeGraph(
        uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        user=os.getenv("NEO4J_USER", "neo4j"),
        password=os.getenv("NEO4J_PASSWORD", "knowledge123"),
        config_path=str(Path(__file__).parent.parent / "config" / "schema.yaml"),
    )

    total = 0

    # ── Phase 1: Disease nodes ───────────────────────────────────────────
    logger.info("\n--- Phase 1: Disease concepts ---")
    upsert_concepts(kg, DISEASES, "disease")
    n = tag_items(kg, papers, "Paper",      _paper_search_text, DISEASES, "MENTIONS",         "disease")
    total += n; logger.info(f"  Paper MENTIONS (disease): {n}")
    n = tag_items(kg, repos,  "GithubRepo", _repo_search_text,  DISEASES, "MENTIONS",         "disease")
    total += n; logger.info(f"  Repo  MENTIONS (disease): {n}")

    # ── Phase 2: Method nodes ────────────────────────────────────────────
    logger.info("\n--- Phase 2: Method concepts ---")
    upsert_concepts(kg, METHODS, "method")
    n = tag_items(kg, papers, "Paper",      _paper_search_text, METHODS, "USES_TECHNOLOGY",   "method")
    total += n; logger.info(f"  Paper USES_TECHNOLOGY (method): {n}")
    n = tag_items(kg, repos,  "GithubRepo", _repo_search_text,  METHODS, "USES_TECHNOLOGY",   "method")
    total += n; logger.info(f"  Repo  USES_TECHNOLOGY (method): {n}")

    # ── Phase 3: Biological concept nodes ───────────────────────────────
    logger.info("\n--- Phase 3: Biological concepts ---")
    upsert_concepts(kg, BIO_CONCEPTS, "biology")
    n = tag_items(kg, papers, "Paper",      _paper_search_text, BIO_CONCEPTS, "MENTIONS",     "biology")
    total += n; logger.info(f"  Paper MENTIONS (biology): {n}")
    n = tag_items(kg, repos,  "GithubRepo", _repo_search_text,  BIO_CONCEPTS, "MENTIONS",     "biology")
    total += n; logger.info(f"  Repo  MENTIONS (biology): {n}")

    logger.info(f"\nTotal new relationships created (phases 1-3): {total}")

    # ── Phase 4: Paper-paper semantic similarity ─────────────────────────
    logger.info("\n--- Phase 4: Paper-paper semantic similarity ---")
    logger.info(f"  Loading sentence-transformers model...")
    analyzer = SemanticAnalyzer()
    embeddings = analyzer.embed_items(papers)
    pairs = analyzer.find_all_similarities(embeddings, threshold=SIMILARITY_THRESHOLD)
    logger.info(f"  Found {len(pairs)} paper pairs above threshold {SIMILARITY_THRESHOLD}")

    sim_count = 0
    for item in pairs:
        # find_all_similarities returns List[Tuple[str, str, float]]
        id1, id2, score = item
        if id1 and id2 and id1 != id2:
            kg.add_relationship(
                from_id=id1,
                to_id=id2,
                rel_type="RELATES_TO",
                from_label="Paper",
                to_label="Paper",
                properties={"similarity_score": round(float(score), 4), "method": "embedding"},
            )
            logger.info(f"    RELATES_TO: {id1} <-> {id2} ({score:.3f})")
            sim_count += 1
    logger.info(f"  Created {sim_count} RELATES_TO relationships")

    # ── Summary ──────────────────────────────────────────────────────────
    stats = kg.get_statistics()
    print("\n--- Graph statistics ---")
    for k, v in stats.items():
        print(f"  {k}: {v}")

    kg.close()
    logger.info("concept_extractor.py complete")


if __name__ == "__main__":
    main()
