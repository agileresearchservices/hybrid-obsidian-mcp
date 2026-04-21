"""Flatten all batch results, inventory new tags, consolidate near-duplicates."""
import json
import re
from collections import Counter
from pathlib import Path

BASE = Path("/Users/kevin/github/personal/hybrid-obsidian-mcp/logs/tag-run")
TAXONOMY_STR = """nasuni, lucille, thermofisher, hyrule, opensearch, architecture, standup, java, daily-log, email, calendar, aws, geotrans, security, kubernetes, address-normalization, docs, working-session, slack, llm, kafka, eks, hybrid-search, secrets, config, research, python, multi-tenancy, relevancy, admin-ui, governance, gartner, api, ihg, docker, agentic-hybrid-search, code-ref, deployment, technical, haystack-2026, hyrule-eks, setup, search-features, development-log, scaling, whiskersandhomes, solr, dnb-matching, onboarding, duns, web-search, development, s3-backups, infrastructure, sharding, search, performance, conference, in-progress, automation, competitor-research, mcp, keda, ltr, ehp, langchain, stripe, digitalocean, market-opportunity, faq, salesforce, ism, reranking, data-quality, garbage-collection, jenkins, google-places, data-analysis, rate-limiting, git, query-rewrite, blog, agentic-coding, guard, petfinder, recipes, rca, generative-ai, weekly-insights, cost-optimization, cve, gpu, autoscaling, project-scope, sagemaker, dictionaries, entity-extraction, debugging, rag, sql, testing, ssh, troubleshooting, oracle, admin, pinecone, benchmarking, vector-search, crownpeak, verizon, octopus, oai-2025, kmw-offsite, startup"""
TAXONOMY = {t.strip() for t in TAXONOMY_STR.split(",")}


def normalize(tag: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", tag.strip().lower()).strip("-")


flat = []
for f in sorted((BASE / "results").glob("batch_*.json")):
    flat.extend(json.loads(f.read_text()))

print(f"Total entries: {len(flat)}")

all_add_tags = Counter()
for e in flat:
    for t in e.get("add_tags", []):
        all_add_tags[normalize(t)] += 1

new_tags = {t: c for t, c in all_add_tags.items() if t not in TAXONOMY}
print(f"\nNewly proposed tags ({len(new_tags)}):")
for t, c in sorted(new_tags.items(), key=lambda x: -x[1]):
    print(f"  {t}: {c}")

# Write flattened (with normalized tags) for apply step
out = BASE / "aggregated.json"
normalized = []
for e in flat:
    normalized.append({
        "path": e["path"],
        "add_tags": sorted({normalize(t) for t in e.get("add_tags", [])}),
        "remove_tags": sorted({normalize(t) for t in e.get("remove_tags", [])}),
    })
out.write_text(json.dumps(normalized, indent=2))
print(f"\nWrote {out} ({len(normalized)} entries)")

# Surface removals for review
removals = [e for e in normalized if e["remove_tags"]]
if removals:
    print(f"\nRemovals requested ({len(removals)}):")
    for e in removals:
        print(f"  {e['path']}: remove {e['remove_tags']}")
else:
    print("\nNo removals requested.")
