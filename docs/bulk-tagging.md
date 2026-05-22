# Bulk Tagging

A multi-agent workflow that proposes, verifies, and applies frontmatter tags across every note in the vault. Built to run end-to-end driven by an LLM, with hard guarantees (preflight validation, dry-run, blocklist, alias normalization, per-note caps) so it can't trash the vault.

## When to use it

- Initial vault tagging — a fresh vault where most notes have no tags.
- Periodic clean-up — after months of writing, taxonomy drift creates near-duplicates (`code-review` vs `code-ref`) and unused stragglers worth consolidating.
- Onboarding a new section — a folder full of recently-imported notes that all need taxonomy alignment.

It is **not** for one-off retagging of a single note — just edit frontmatter directly, or use `note_create` / a plain text editor. The bulk workflow has setup overhead that only pays off across dozens of notes.

## TL;DR — run it

From an MCP client (Claude Code, an IDE plugin, an agent harness):

```text
Call mcp__obsidian-search__bulk_tag_workflow() and follow the returned prompt.
```

The tool returns an orchestration prompt. The calling LLM dispatches Haiku subagents that propose tags per batch in parallel, then this server applies the merged changes back to the vault with a `dry_run` pass first. From start to finish on a 600-note vault it takes a few minutes and a few cents of Haiku spend.

From the shell, the same flow is available as `obsidian-cli` subcommands — see the [CLI Reference](cli.md#bulk-tag-commands).

## The pipeline

```text
┌──────────────────┐
│ Step 1 — Seed    │   bulk_tag_taxonomy   →  {tag: count}
│   (parallel)     │   bulk_tag_list       →  every .md note (path, size, folder)
└────────┬─────────┘
         │
┌────────▼─────────┐
│ Step 2 — Batch   │   bulk_tag_create_batches(paths)
│                  │   size-aware: each batch's prepare() payload ≤ 80K chars
│                  │   (Claude Code's 25K-token MCP cap)
└────────┬─────────┘   batch sizes clamped to [5, 40]
         │
┌────────▼─────────┐
│ Step 3 — Propose │   For each batch, spawn one Haiku subagent in parallel.
│   (Haiku,        │   Each agent: bulk_tag_prepare → propose → Write JSON.
│   parallel)      │   Output: logs/tag-run/results/batch_NN.result.json
└────────┬─────────┘
         │
┌────────▼─────────┐
│ Step 4 — Verify  │   bulk_tag_verify(batch, result) for every batch in parallel
│   + aggregate    │   Abort if any verify returns ok=false.
│                  │   bulk_tag_aggregate(results_dir) → merged change set
│                  │                                       + consolidation_candidates
└────────┬─────────┘
         │
┌────────▼─────────┐
│ Step 5 — Apply   │   bulk_tag_consolidate(threshold=0.90)
│                  │   bulk_tag_apply(chunks, dry_run=true) first
│                  │   bulk_tag_apply(chunks)               then real
└────────┬─────────┘
         │
┌────────▼─────────┐
│ Step 6 — Report  │   bulk_tag_taxonomy again, diff the deltas
└──────────────────┘
```

The orchestration prompt is generated in `src/tagger.py` (`WORKFLOW_PROMPT`) — if you want to read it without running it, call `bulk_tag_workflow` or `obsidian-cli workflow`.

## Guarantees

Every safeguard exists because of a real failure mode. They're worth understanding before you turn this loose on the vault.

### Preflight path validation

`bulk_tag_apply` resolves every path in the change set **before any write**. If any resolves outside the vault, doesn't exist, or fails normalization, the whole batch aborts with `status="preflight_failed"` and zero files are touched. This is the contract — partial application is never allowed.

LLMs occasionally rewrite filenames from memory instead of copying exact bytes — curly quotes, en/em-dashes, NFC vs NFD normalization. `_canonicalize_path` folds the common drifts back before the path lookup, but anything still unmatchable fails preflight rather than silently writing to the wrong file.

### Blocklist

```python
TAG_BLOCKLIST = {"notes", "reference", "operational", "monitoring",
                 "logging", "todo", "misc", "general"}
```

Tags that are too generic to be useful for retrieval. They get filtered out at both the aggregate step and the apply step, so even if an LLM proposes them they never land in frontmatter.

### Aliases

```python
TAG_ALIASES = {
    "matching": "dnb-matching",
    "code-review": "code-ref",
}
```

Canonical-form mapping for tags that have a known established equivalent in the vault. Applied during merge so subagent proposals stay aligned with existing taxonomy.

### Per-note cap

`MAX_NEW_TAGS_PER_NOTE = 6` — the apply layer drops any addition beyond this cap and reports it in `capped` so the orchestrator can flag it. Keeps notes from becoming tag-spam.

### Normalization

All tags pass through `_norm_tag` → lowercase, kebab-case, deduplicated. Mixed-case proposals (`AWS`, `DigitalOcean`) collapse to `aws`, `digitalocean` before any comparison or merge.

### Singleton drop

`bulk_tag_aggregate` discards any **new** tag (i.e. not already in the taxonomy) that only appears once across the whole run, unless it's in `TAG_ALIASES.values()`. Rationale: a single-use tag isn't useful for retrieval; if it's worth keeping, it'll show up at least twice.

### Consolidation

`bulk_tag_consolidate` uses `difflib.SequenceMatcher` between each new tag and the existing taxonomy. Pairs with ratio ≥ `confidence_threshold` (default 0.90) auto-merge into the existing tag. Pairs in `[0.85, threshold)` get returned as `flagged_for_review` for manual triage.

### Dry-run

`bulk_tag_apply(changes, dry_run=True)` runs the full validation and planning pass but never writes. The orchestration prompt does a dry-run pass before the real apply, so you can inspect the planned diff in `logs/tag-run/results/` before committing.

## What an LLM subagent gets

For each batch, the subagent calls `bulk_tag_prepare(paths)` once and gets back, per note:

- `existing_tags` — current frontmatter tags, normalized.
- `content_excerpt` — head + tail of the note body (head ~2500 chars, tail ~500 chars), with a `truncated: true` flag when the middle was elided. Keeps long daily logs from blowing the token budget.
- `path` — vault-relative, as passed in.

It also gets the full taxonomy as a comma-separated tag list (no counts, no descriptions) so it can pick existing tags by preference. Cost-wise this is ~$0.003/run with Haiku.

The subagent's output contract is strict:

```json
[
  {"path": "Daily Log/2026-04-08.md", "add_tags": ["kmw", "nasuni"], "remove_tags": []},
  {"path": "KMW/Notes.md",            "add_tags": [],                "remove_tags": ["aws"]}
]
```

Subagents are instructed to propose **only additions** — they do not re-propose any tag already in `existing_tags`. `remove_tags` is reserved for factually-wrong tags only (high bar, e.g. `aws` on a pure-DigitalOcean note).

## Output files

By convention everything lands under `logs/tag-run/`:

```text
logs/tag-run/
├── batches/
│   ├── batch_00.json    paths for batch 0
│   ├── batch_01.json
│   └── ...
└── results/
    ├── batch_00.result.json   {path, add_tags, remove_tags}[] from the subagent
    ├── batch_01.result.json
    └── aggregate.json         optional — flatten + diagnostics
```

`bulk_tag_verify(batch_file, result_file)` returns `{ok: bool, missing_paths, extra_paths, stale_paths}` so the orchestrator can detect partial coverage before aggregating.

## Manual driving

For the rare cases when the LLM-driven flow isn't what you want, every step is a standalone CLI subcommand. See [CLI Reference — Bulk tag commands](cli.md#bulk-tag-commands).

A minimal manual flow:

```bash
uv run obsidian-cli taxonomy > taxonomy.json
uv run obsidian-cli tag-list > all_notes.json

# Build your own change set however you want, then:
cat changes.json | uv run obsidian-cli tag-apply --dry-run    # preview
cat changes.json | uv run obsidian-cli tag-apply              # commit
```

`tag-apply` reads the change list from stdin so it composes cleanly with `jq` and similar tools.

## Cost & performance

All classification work is on Haiku. Empirically a 600-note vault runs in 3–5 minutes wall-clock and ~$0.05 in API spend, dominated by the subagent dispatch — the orchestration itself stays on whatever model the user is driving from.

The taxonomy cache (`TAXONOMY_CACHE_TTL_SECONDS=60`) is what makes this fast: `bulk_tag_taxonomy` is called by the orchestrator, by every verify, and by the final report. Without the cache, that's 3-4 full vault rescans per run.

After the run, the vault watcher (`com.obsidian.search-watcher`) re-indexes changed files within ~10s, so search reflects the new tags without manual reindexing.
