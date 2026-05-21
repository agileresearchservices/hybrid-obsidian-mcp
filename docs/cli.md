# CLI Reference

`obsidian-cli` is the shell entrypoint over the same Python codepath the MCP server uses. It's the right surface for scripts, cron, slack-gateway, daily-digest, and any other automation.

## Invocation

```bash
uv run obsidian-cli <subcommand> [args...]
```

If you've activated the project's virtualenv directly, plain `obsidian-cli ...` works too.

## Search & read

### `search`

```bash
uv run obsidian-cli search "your query" \
  [--k N] \
  [--tags csv] [--exclude-tags csv] \
  [--folder PATH] \
  [--date-from YYYY-MM-DD] [--date-to YYYY-MM-DD] \
  [--no-rerank]
```

Hybrid search with reranking on by default. Output is markdown — one block per hit with score, file path, and chunk text.

### `list-notes`

```bash
uv run obsidian-cli list-notes \
  [--folder PATH] \
  [--tags csv] [--exclude-tags csv] \
  [--date-from YYYY-MM-DD] [--date-to YYYY-MM-DD] \
  [--limit N]
```

Metadata-only listing. Faster than `search` when you don't need text matching.

### `read-note`

```bash
uv run obsidian-cli read-note "Daily Log/2026-04-08.md"
```

Prints the file contents (frontmatter included). Refuses paths outside the vault.

### `recent-notes`

```bash
uv run obsidian-cli recent-notes [--limit N]
```

## Todos

```bash
uv run obsidian-cli list-todos [--tag TAG] [--status open|completed|all] [--limit N]
uv run obsidian-cli add-todo "fix bug" [--tags csv]
uv run obsidian-cli complete-todo <id>
uv run obsidian-cli search-todos "free text"
```

`add-todo` auto-detects inline `#hashtags` and places the todo under matching section headings in `TODO.md`.

## Daily logs

```bash
uv run obsidian-cli daily-log view    [--date YYYY-MM-DD]
uv run obsidian-cli daily-log create  [--date YYYY-MM-DD] [--force]
uv run obsidian-cli daily-log append "content..." [--section "Notes"] [--date YYYY-MM-DD]
uv run obsidian-cli daily-log summary [--days N]
```

`append` creates the log from the standard template first if it doesn't exist.

## Notes

```bash
uv run obsidian-cli note create "Title" [--content "body"] [--folder PATH] [--tags csv]
uv run obsidian-cli note append "path/to/note.md" "content to append"
```

## Vault stats & caches

```bash
uv run obsidian-cli stats         # vault stats (note count, size, top tags, todo counts)
uv run obsidian-cli cache-stats   # in-process cache hits/misses/sizes/hit_rate
uv run obsidian-cli config        # print resolved config (vault path)
```

`cache-stats` snapshots all four caches in one call — useful for confirming the prewarm and LRU sizes are doing real work in production.

## Bulk tag commands

These are the primitives the bulk-tag workflow chains together. Most users will only need `workflow`, but the individual commands are documented for one-off use and scripting.

```bash
uv run obsidian-cli taxonomy                          # {tag: count} JSON
uv run obsidian-cli taxonomy-topk --k 100             # top-K tags, one per line
uv run obsidian-cli tag-list                          # all notes as JSON

# stdin/stdout chain
echo '[{"path":"...","add_tags":["x"],"remove_tags":[]}]' \
  | uv run obsidian-cli tag-apply [--dry-run]

uv run obsidian-cli tag-verify  batch.json result.json
uv run obsidian-cli tag-aggregate logs/tag-run/batches/

# Stdin = changes JSON; argv = consolidation candidates JSON
uv run obsidian-cli tag-consolidate '[...]' [--threshold 0.90]

# Stdin = list of paths; stdout = {existing_tags, content_excerpt} per note
echo '["a.md","b.md"]' | uv run obsidian-cli tag-prepare

uv run obsidian-cli workflow                          # print the orchestration prompt
```

`tag-apply` validates every path before any write — if any path is missing, the whole batch aborts and no files are touched. Pass `--dry-run` to preview without writing.

`tag-verify` exits non-zero if a result file doesn't fully cover its batch input (missing paths, extras, or stale data) — use it as a gate in CI / scripts before calling `tag-apply`.
