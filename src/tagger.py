"""Bulk tag operations: frontmatter merges, taxonomy collection, vault enumeration."""

from __future__ import annotations

import difflib
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Optional

import frontmatter

from .config import OBSIDIAN_VAULT_PATH

VAULT = Path(OBSIDIAN_VAULT_PATH).expanduser().resolve()

TAG_BLOCKLIST: set[str] = {
    "notes", "reference", "operational", "monitoring",
    "logging", "todo", "misc", "general",
}
TAG_ALIASES: dict[str, str] = {
    "matching": "dnb-matching",
    "code-review": "code-ref",
}
MAX_NEW_TAGS_PER_NOTE = 6
TRUNCATE_THRESHOLD = 3000
HEAD_CHARS = 2500
TAIL_CHARS = 500

_FRONTMATTER_RE = re.compile(r"^---\n.*?\n---\n", re.DOTALL)


def _norm_tag(t: str) -> str:
    return str(t).strip().lstrip("#").strip().lower()


def _apply_aliases(t: str) -> str:
    return TAG_ALIASES.get(t, t)


def _iter_notes():
    for p in VAULT.rglob("*.md"):
        try:
            p.resolve().relative_to(VAULT)
        except ValueError:
            continue
        yield p


def _resolve_path(rel_path: str) -> Optional[Path]:
    """Resolve a vault-relative path with Unicode tolerance.

    Tries the raw path, then NFC/NFD normalized forms, then quote-normalized
    forms (both directions), then falls back to a basename scan. Returns None
    if the file can't be located.
    """
    if not rel_path:
        return None

    # Try both quote normalizations to handle JSON deserialization variations
    quote_variations = [
        rel_path,
        rel_path.replace(""", '"').replace(""", '"'),  # Curly → straight
        rel_path.replace('"', """).replace('"', """),  # Straight → curly
    ]

    candidates = []
    for base_path in quote_variations:
        for form in ("NFC", "NFD"):
            candidates.append(unicodedata.normalize(form, base_path))
        candidates.append(base_path)

    seen: set[str] = set()
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        p = (VAULT / c)
        try:
            resolved = p.resolve()
            resolved.relative_to(VAULT)
        except (OSError, ValueError):
            continue
        if resolved.exists():
            return resolved

    # Basename fallback: iterate vault once, comparing normalized basenames.
    # Generate many variations of the target basename to handle quote issues
    target_basename = Path(rel_path).name
    target_names = {unicodedata.normalize("NFC", target_basename)}

    # Also try with swapped quotes: replace all quotes with both curly and straight variants
    for variant in quote_variations:
        target_names.add(unicodedata.normalize("NFC", Path(variant).name))

    for p in _iter_notes():
        p_normalized = unicodedata.normalize("NFC", p.name)
        if p_normalized in target_names:
            return p

    # Last resort: try a loose comparison that ignores quote types entirely
    # by removing all quote characters and comparing the rest
    # Use Unicode escape sequences for curly quotes to ensure proper handling
    quote_chars = {'"', '"', '“', '”'}  # straight, and both curly quotes
    rel_no_quotes = ''.join(c for c in target_basename if c not in quote_chars)
    for p in _iter_notes():
        p_no_quotes = ''.join(c for c in p.name if c not in quote_chars)
        if p_no_quotes == rel_no_quotes:
            return p

    return None


def _read_existing_tags(path: Path) -> tuple[list[str], object]:
    """Return (normalized_existing_tags, frontmatter_post) for a note."""
    post = frontmatter.load(path)
    raw = post.metadata.get("tags") or []
    if isinstance(raw, str):
        raw = [raw]
    out: list[str] = []
    seen: set[str] = set()
    for t in raw:
        nt = _norm_tag(t)
        if not nt or nt in seen:
            continue
        seen.add(nt)
        out.append(nt)
    return out, post


def _truncate(text: str) -> tuple[str, bool]:
    """Strip leading YAML frontmatter; if remaining text > threshold, return
    head+tail excerpt with a divider. Returns (excerpt, was_truncated)."""
    body = _FRONTMATTER_RE.sub("", text, count=1).lstrip()
    if len(body) <= TRUNCATE_THRESHOLD:
        return body, False
    head = body[:HEAD_CHARS]
    tail = body[-TAIL_CHARS:]
    omitted = len(body) - HEAD_CHARS - TAIL_CHARS
    divider = f"\n\n...[truncated {omitted} chars]...\n\n"
    return head + divider + tail, True


def collect_taxonomy() -> dict[str, int]:
    """Scan vault, return {tag: count} sorted by descending count."""
    counts: Counter[str] = Counter()
    for p in _iter_notes():
        try:
            post = frontmatter.load(p)
        except Exception:
            continue
        raw = post.metadata.get("tags") or []
        if isinstance(raw, str):
            raw = [raw]
        for t in raw:
            nt = _norm_tag(t)
            if nt:
                counts[nt] += 1
    return dict(counts.most_common())


def list_notes() -> list[dict]:
    """Enumerate all .md notes with path, size, folder."""
    out = []
    for p in _iter_notes():
        rel = p.relative_to(VAULT)
        out.append({
            "path": str(rel),
            "size": p.stat().st_size,
            "folder": str(rel.parent) if rel.parent != Path(".") else "",
        })
    out.sort(key=lambda x: x["path"])
    return out


def prepare_batch(paths: list[str]) -> list[dict]:
    """Return per-note {path, exists, size, existing_tags, content_excerpt, truncated}.

    Replaces 20× read_note round trips with a single call: agents receive
    pre-parsed existing_tags (so they don't re-propose them) and head+tail
    excerpts of large notes (so Haiku input stays small).
    """
    out: list[dict] = []
    for rel in paths:
        resolved = _resolve_path(rel)
        if resolved is None:
            out.append({"path": rel, "exists": False})
            continue
        try:
            text = resolved.read_text(encoding="utf-8")
        except Exception as e:
            out.append({"path": rel, "exists": True, "error": str(e)})
            continue
        existing_tags, _ = _read_existing_tags(resolved)
        excerpt, truncated = _truncate(text)
        out.append({
            "path": rel,
            "exists": True,
            "size": resolved.stat().st_size,
            "existing_tags": existing_tags,
            "content_excerpt": excerpt,
            "truncated": truncated,
        })
    return out


def merge_tags(
    rel_path: str,
    add_tags: list[str],
    remove_tags: Optional[list[str]] = None,
    dry_run: bool = False,
) -> dict:
    """Merge tags into a note's frontmatter. Returns a status dict.

    Applies TAG_ALIASES, filters TAG_BLOCKLIST, caps new additions at
    MAX_NEW_TAGS_PER_NOTE. When dry_run=True, computes the merge but does
    not write; status becomes "would-update" or "would-noop".
    """
    resolved = _resolve_path(rel_path)
    if resolved is None:
        return {"path": rel_path, "status": "error", "reason": "not found"}

    norm_adds: list[str] = []
    seen_add: set[str] = set()
    capped = 0
    filtered: list[str] = []
    for raw in add_tags or []:
        nt = _apply_aliases(_norm_tag(raw))
        if not nt:
            continue
        if nt in TAG_BLOCKLIST:
            filtered.append(nt)
            continue
        if nt in seen_add:
            continue
        seen_add.add(nt)
        norm_adds.append(nt)
    remove = {t for t in (_norm_tag(t) for t in (remove_tags or [])) if t}

    existing_norm, post = _read_existing_tags(resolved)

    # Detect normalization/dedup drift in existing tags (match legacy behavior).
    raw_existing = post.metadata.get("tags") or []
    if isinstance(raw_existing, str):
        raw_existing = [raw_existing]
    had_change = False
    for t in raw_existing:
        nt = _norm_tag(t)
        if nt and str(t) != nt:
            had_change = True
            break
    if len(existing_norm) != sum(1 for t in raw_existing if _norm_tag(t)):
        had_change = True

    removed: list[str] = []
    kept: list[str] = []
    for t in existing_norm:
        if t in remove:
            removed.append(t)
        else:
            kept.append(t)
    if removed:
        had_change = True
    kept_seen = set(kept)

    added: list[str] = []
    for t in norm_adds:
        if t in kept_seen:
            continue
        if len(added) >= MAX_NEW_TAGS_PER_NOTE:
            capped += 1
            continue
        kept.append(t)
        kept_seen.add(t)
        added.append(t)
    if added:
        had_change = True

    if not had_change:
        status = "would-noop" if dry_run else "noop"
        result = {"path": rel_path, "status": status, "tags": kept}
        if filtered:
            result["filtered"] = filtered
        if capped:
            result["capped"] = capped
        return result

    if not dry_run:
        post["tags"] = kept
        text = frontmatter.dumps(post)
        if not text.endswith("\n"):
            text += "\n"
        resolved.write_text(text, encoding="utf-8")

    status = "would-update" if dry_run else "updated"
    result = {
        "path": rel_path,
        "status": status,
        "added": added,
        "removed": removed,
        "tags_now": kept,
    }
    if filtered:
        result["filtered"] = filtered
    if capped:
        result["capped"] = capped
    return result


def bulk_apply(changes: list[dict], dry_run: bool = False) -> list[dict]:
    """Apply a list of {path, add_tags, remove_tags} entries. Returns per-entry results."""
    results = []
    for entry in changes:
        rel = entry.get("path")
        if not rel:
            results.append({"status": "error", "reason": "missing path"})
            continue
        try:
            results.append(merge_tags(
                rel_path=rel,
                add_tags=entry.get("add_tags") or [],
                remove_tags=entry.get("remove_tags") or [],
                dry_run=dry_run,
            ))
        except Exception as e:
            results.append({"path": rel, "status": "error", "reason": str(e)})
    return results


def verify_batch(batch_file: str, result_file: str) -> dict:
    """Verify that result_file covers every path in batch_file.

    Flags results as stale if result_mtime < batch_mtime. Returns a dict with
    ok, missing_paths, extra_paths, mtimes, and stale.
    """
    bp, rp = Path(batch_file), Path(result_file)
    if not bp.exists():
        return {"ok": False, "reason": f"batch file not found: {batch_file}"}
    if not rp.exists():
        return {"ok": False, "reason": f"result file not found: {result_file}"}
    try:
        batch_paths = set(json.loads(bp.read_text()))
        results = json.loads(rp.read_text())
        result_paths = {e["path"] for e in results}
    except Exception as e:
        return {"ok": False, "reason": f"parse error: {e}"}
    missing = sorted(batch_paths - result_paths)
    extra = sorted(result_paths - batch_paths)
    batch_mtime = bp.stat().st_mtime
    result_mtime = rp.stat().st_mtime
    stale = result_mtime < batch_mtime
    ok = not missing and not extra and not stale
    return {
        "ok": ok,
        "batch_file": str(bp),
        "result_file": str(rp),
        "batch_count": len(batch_paths),
        "result_count": len(result_paths),
        "missing_paths": missing,
        "extra_paths": extra,
        "batch_mtime": batch_mtime,
        "result_mtime": result_mtime,
        "stale": stale,
    }


def consolidation_candidates(
    proposals: list[dict],
    taxonomy: dict[str, int],
    cutoff: float = 0.8,
) -> list[dict]:
    """Flag newly-proposed tags that look like near-duplicates of taxonomy tags.

    Uses difflib.SequenceMatcher. Returns [{proposed, nearest, score, count}]
    for every proposed new tag within `cutoff` similarity of an existing tag.
    """
    new_counts: Counter[str] = Counter()
    tax_set = set(taxonomy.keys())
    for entry in proposals:
        for t in entry.get("add_tags") or []:
            nt = _apply_aliases(_norm_tag(t))
            if nt and nt not in tax_set and nt not in TAG_BLOCKLIST:
                new_counts[nt] += 1
    tax_list = list(tax_set)
    out: list[dict] = []
    for tag, count in new_counts.most_common():
        matches = difflib.get_close_matches(tag, tax_list, n=1, cutoff=cutoff)
        if matches:
            score = difflib.SequenceMatcher(None, tag, matches[0]).ratio()
            out.append({
                "proposed": tag,
                "nearest": matches[0],
                "score": round(score, 3),
                "count": count,
            })
    return out


def aggregate_results(results_dir: str) -> dict:
    """Flatten all batch_*.json in results_dir, apply aliases, drop blocklisted
    and new-singletons. Returns {changes, rejected, consolidation_candidates}.
    """
    rdir = Path(results_dir)
    if not rdir.exists():
        return {"error": f"results dir not found: {results_dir}"}
    all_entries: list[dict] = []
    for f in sorted(rdir.glob("batch_*.json")):
        try:
            all_entries.extend(json.loads(f.read_text()))
        except Exception as e:
            return {"error": f"parse {f.name}: {e}"}

    taxonomy = collect_taxonomy()
    tax_set = set(taxonomy.keys())
    new_tag_counts: Counter[str] = Counter()
    for e in all_entries:
        for t in e.get("add_tags") or []:
            nt = _apply_aliases(_norm_tag(t))
            if nt and nt not in tax_set:
                new_tag_counts[nt] += 1

    changes: list[dict] = []
    rejected: list[dict] = []
    for e in all_entries:
        add_out: list[str] = []
        dropped: list[dict] = []
        for t in e.get("add_tags") or []:
            nt = _apply_aliases(_norm_tag(t))
            if not nt:
                continue
            if nt in TAG_BLOCKLIST:
                dropped.append({"tag": nt, "reason": "blocklist"})
                continue
            if nt not in tax_set and new_tag_counts.get(nt, 0) <= 1 and nt not in TAG_ALIASES.values():
                dropped.append({"tag": nt, "reason": "new-singleton"})
                continue
            add_out.append(nt)
        remove_out = sorted({_norm_tag(t) for t in e.get("remove_tags") or [] if _norm_tag(t)})
        changes.append({
            "path": e["path"],
            "add_tags": sorted(set(add_out)),
            "remove_tags": remove_out,
        })
        if dropped:
            rejected.append({"path": e["path"], "dropped": dropped})

    candidates = consolidation_candidates(all_entries, taxonomy)
    return {
        "changes": changes,
        "rejected": rejected,
        "consolidation_candidates": candidates,
        "new_tag_counts": dict(new_tag_counts.most_common()),
    }


WORKFLOW_PROMPT = f"""\
BULK TAG UPDATE WORKFLOW — the user wants to review every note in the Obsidian vault and propose/update/remove frontmatter tags.

## Step 1 — Seed

Call `mcp__obsidian-search__bulk_tag_taxonomy()` to get the current `{{tag: count}}` map. This is your starting vocabulary.

Call `mcp__obsidian-search__bulk_tag_list()` to get all .md note paths.

## Step 2 — Plan batches

**First, wipe stale state** — delete any existing `logs/tag-run/batches/*.json` and `logs/tag-run/results/*.json`. Stale result files from prior runs can silently mask failed agent writes.

Split the note list into batches of **~20 notes each**. For N notes, that's roughly `ceil(N / 20)` batches. Write each batch as a JSON array of paths to `logs/tag-run/batches/batch_{{NN}}.json`.

## Step 3 — Dispatch subagents IN PARALLEL (all Haiku)

For every batch, spawn one `general-purpose` subagent with **`model: "haiku"`**. Send them all in a single message so they run concurrently.

Each subagent's prompt must include:
- The batch file path (e.g. `logs/tag-run/batches/batch_00.json`)
- The current taxonomy (comma-separated tag names)
- Output file path (e.g. `logs/tag-run/results/batch_00.json`)
- Rules:
  1. Call `mcp__obsidian-search__bulk_tag_prepare(paths=<batch>)` **once** per batch. The response includes `existing_tags` and `content_excerpt` for every note — do NOT call `read_note` per-file.
  2. Propose **only additions** — do NOT re-propose any tag already in `existing_tags`. Empty `add_tags: []` is valid and preferred when coverage is already good.
  3. Target up to {MAX_NEW_TAGS_PER_NOTE} new tags per note. The apply layer enforces a hard cap of {MAX_NEW_TAGS_PER_NOTE}; proposals beyond that are dropped.
  4. Prefer taxonomy tags. Coin NEW tags only for load-bearing recurring themes, not incidentals. Lowercase kebab-case.
  5. `remove_tags` only for factually-wrong tags (high bar; e.g. `aws` on a pure-DigitalOcean file).
  6. Use the `Write` tool (NOT Bash) to write the JSON array to the output file: `[{{"path": "...", "add_tags": [...], "remove_tags": [...]}}, ...]`. No prose.

## Step 4 — Verify + aggregate

For every batch, call `mcp__obsidian-search__bulk_tag_verify(batch_file, result_file)`. If any returns `ok=false`, abort and report — do NOT proceed to apply with partial/stale results.

Then call `mcp__obsidian-search__bulk_tag_aggregate(results_dir="logs/tag-run/results")`. Review the returned `consolidation_candidates` — if a new tag with count ≥ 2 is close (score ≥ 0.85) to an existing taxonomy tag, edit the proposals to use the existing variant.

## Step 5 — Apply

Call `mcp__obsidian-search__bulk_tag_apply(changes=<aggregated.changes>, dry_run=True)` first. Review `would-update` vs `would-noop` counts.

Then call again without `dry_run` to commit.

## Step 6 — Report

Call `mcp__obsidian-search__bulk_tag_taxonomy()` again to get the post-run taxonomy. Summarize:
- Total notes processed / updated / noop / errors
- Taxonomy delta (new tags, biggest growth)
- Any removals (with justification)
- Aggregator's `rejected` count (blocklisted or new-singleton drops)
- Any `capped` entries from apply (notes where proposals exceeded the {MAX_NEW_TAGS_PER_NOTE}-tag limit)

## Notes

- All subagent LLM work runs on Haiku for cost efficiency. The orchestration is lightweight and stays on whatever model the user is using.
- The vault watcher (launchd `com.obsidian.search-watcher`) auto-reindexes changed files within ~10s, so no manual reindex is needed after step 5.
"""


def workflow_prompt() -> str:
    """Return the bulk-tag workflow orchestration prompt."""
    return WORKFLOW_PROMPT
