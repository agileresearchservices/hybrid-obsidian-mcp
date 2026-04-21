"""Post-run review: tag-count distribution, noop rate, cross-domain contamination, path sanity."""
import json
import subprocess
from collections import Counter
from pathlib import Path

BASE = Path("/Users/kevin/github/personal/hybrid-obsidian-mcp/logs/tag-run")
CLI = "/Users/kevin/github/personal/hybrid-obsidian-mcp/.venv/bin/obsidian-cli"

aggregated = json.loads((BASE / "aggregated.json").read_text())
print(f"Total entries: {len(aggregated)}\n")

# 1) Tag count distribution per note (add_tags only — does NOT count existing frontmatter tags)
sizes = Counter(len(e["add_tags"]) for e in aggregated)
print("add_tags size distribution:")
for k in sorted(sizes):
    print(f"  {k} tags: {sizes[k]} notes")

# 2) Empty add_tags (noop by design)
empty = [e["path"] for e in aggregated if not e["add_tags"]]
print(f"\nEmpty add_tags: {len(empty)}")
for p in empty[:10]:
    print(f"  {p}")

# 3) Cross-domain contamination: Personal/* should not carry work tags
WORK_TAGS = {"nasuni", "lucille", "thermofisher", "hyrule", "gartner", "ihg",
             "kubernetes", "opensearch", "aws", "java", "eks", "kafka"}
personal_contaminated = []
for e in aggregated:
    if e["path"].startswith("Personal/"):
        bad = [t for t in e["add_tags"] if t in WORK_TAGS]
        if bad:
            personal_contaminated.append((e["path"], bad))
print(f"\nPersonal notes with work tags in add_tags: {len(personal_contaminated)}")
for p, tags in personal_contaminated:
    print(f"  {p}: {tags}")

# 4) Reverse: work notes with personal tags
PERSONAL_TAGS = {"whiskersandhomes", "petfinder", "recipes", "stripe"}
work_contaminated = []
for e in aggregated:
    if e["path"].startswith("KMW/") or e["path"].startswith("Daily Log/"):
        bad = [t for t in e["add_tags"] if t in PERSONAL_TAGS]
        if bad:
            work_contaminated.append((e["path"], bad))
print(f"\nWork notes with personal tags in add_tags: {len(work_contaminated)}")
for p, tags in work_contaminated:
    print(f"  {p}: {tags}")

# 5) Missing path: the "Agentic coding only becomes..." note
r = subprocess.run([CLI, "tag-list"], capture_output=True, text=True, check=True)
notes = json.loads(r.stdout)
paths = {n["path"] for n in notes}
problem_path = "Agentic coding only becomes “daily-usable”.md"
print(f"\nAgentic coding note exists in vault: {problem_path in paths}")
print(f"  Vault has note with 'Agentic' prefix:")
for p in paths:
    if p.startswith("Agentic"):
        print(f"    {p}")

# 6) New tags that are too generic / semantic duplicates of taxonomy
TAXONOMY = {"nasuni", "lucille", "thermofisher", "hyrule", "opensearch", "architecture",
            "standup", "java", "daily-log", "email", "calendar", "aws", "geotrans",
            "security", "kubernetes", "address-normalization", "docs", "working-session",
            "slack", "llm", "kafka", "eks", "hybrid-search", "secrets", "config",
            "research", "python", "multi-tenancy", "relevancy", "admin-ui", "governance",
            "gartner", "api", "ihg", "docker", "agentic-hybrid-search", "code-ref",
            "deployment", "technical", "haystack-2026", "hyrule-eks", "setup",
            "search-features", "development-log", "scaling", "whiskersandhomes", "solr",
            "dnb-matching", "onboarding", "duns", "web-search", "development",
            "s3-backups", "infrastructure", "sharding", "search", "performance",
            "conference", "in-progress", "automation", "competitor-research", "mcp",
            "keda", "ltr", "ehp", "langchain", "stripe", "digitalocean",
            "market-opportunity", "faq", "salesforce", "ism", "reranking",
            "data-quality", "garbage-collection", "jenkins", "google-places",
            "data-analysis", "rate-limiting", "git", "query-rewrite", "blog",
            "agentic-coding", "guard", "petfinder", "recipes", "rca", "generative-ai",
            "weekly-insights", "cost-optimization", "cve", "gpu", "autoscaling",
            "project-scope", "sagemaker", "dictionaries", "entity-extraction",
            "debugging", "rag", "sql", "testing", "ssh", "troubleshooting", "oracle",
            "admin", "pinecone", "benchmarking", "vector-search", "crownpeak", "verizon",
            "octopus", "oai-2025", "kmw-offsite", "startup"}
new_tag_uses = Counter()
for e in aggregated:
    for t in e["add_tags"]:
        if t not in TAXONOMY:
            new_tag_uses[t] += 1
print(f"\nNewly coined tags ({len(new_tag_uses)}):")
for t, c in sorted(new_tag_uses.items(), key=lambda x: -x[1]):
    print(f"  {t}: {c}")

# 7) Exact duplicates in add_tags for one note (wasted proposals)
for e in aggregated:
    if len(e["add_tags"]) != len(set(e["add_tags"])):
        print(f"Duplicate tags in {e['path']}: {e['add_tags']}")
