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

# Characters LLMs commonly "normalize" when echoing a path back (curly→straight
# quotes, en/em-dashes, ellipsis). When an LLM rewrites a filename from memory
# instead of copying exact bytes, these substitutions drift the path away from
# the real on-disk name. _canonicalize_path folds these back.
_PATH_FOLDS = str.maketrans({
    "“": '"', "”": '"',
    "‘": "'", "’": "'",
    "–": "-", "—": "-",
})


def _fold_path(p: str) -> str:
    return unicodedata.normalize("NFC", p).translate(_PATH_FOLDS)


def _canonicalize_path(path: str, canonical_paths: set[str]) -> str | None:
    """Map a (possibly LLM-normalized) path to its on-disk form. Returns None
    if no canonical match can be found even after Unicode/char folding."""
    if path in canonical_paths:
        return path
    folded_target = _fold_path(path)
    for cp in canonical_paths:
        if _fold_path(cp) == folded_target:
            return cp
    return None


def _load_result_entries(path: Path) -> list[dict]:
    """Parse a result file. Accepts a raw list OR a dict with a `changes` key
    (subagents occasionally wrap output in {\"changes\": [...]}).
    """
    data = json.loads(path.read_text())
    if isinstance(data, dict) and isinstance(data.get("changes"), list):
        return data["changes"]
    if not isinstance(data, list):
        raise ValueError(f"expected JSON list or dict-with-changes, got {type(data).__name__}")
    return data


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
    """Scan vault, return {tag: count} sorted by descending count.

    Applies aliases and filters blocklisted tags (same rules as aggregate_results).
    """
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
            nt = _apply_aliases(_norm_tag(t))
            if nt and nt not in TAG_BLOCKLIST:
                counts[nt] += 1
    return dict(counts.most_common())


def collect_taxonomy_top_k(k: int = 100) -> list[str]:
    """Return the top K tags by frequency as a simple list (for subagent prompts)."""
    taxonomy = collect_taxonomy()
    return list(taxonomy.keys())[:k]


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
) -> dict:
    """Merge tags into a note's frontmatter. Returns a status dict.

    Applies TAG_ALIASES, filters TAG_BLOCKLIST, caps new additions at
    MAX_NEW_TAGS_PER_NOTE. Merges and writes to the file.
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
        status = "noop"
        result = {"path": rel_path, "status": status, "tags": kept}
        if filtered:
            result["filtered"] = filtered
        if capped:
            result["capped"] = capped
        return result

    post["tags"] = kept
    text = frontmatter.dumps(post)
    if not text.endswith("\n"):
        text += "\n"
    resolved.write_text(text, encoding="utf-8")

    status = "updated"
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
    """Apply a list of {path, add_tags, remove_tags} entries.

    Pre-validates every path before writing anything; if any path is missing,
    no writes occur and every entry is returned with status=preflight_failed.
    With dry_run=True, returns the would-be-written tag merges without mutating
    the vault.
    """
    # Preflight: resolve all paths up front so a missing file mid-batch can't
    # leave the vault in a partial state.
    preflight: list[tuple[dict, Optional[Path], Optional[str]]] = []
    missing_paths: list[str] = []
    for entry in changes:
        rel = entry.get("path")
        if not rel:
            preflight.append((entry, None, "missing path"))
            continue
        resolved = _resolve_path(rel)
        if resolved is None:
            missing_paths.append(rel)
            preflight.append((entry, None, "file not found"))
        else:
            preflight.append((entry, resolved, None))

    if missing_paths:
        return [
            {
                "path": entry.get("path"),
                "status": "preflight_failed",
                "reason": reason or f"batch aborted: {len(missing_paths)} path(s) missing",
            }
            for entry, _, reason in preflight
        ]

    if dry_run:
        return [
            {
                "path": entry.get("path"),
                "status": "dry_run",
                "add_tags": entry.get("add_tags") or [],
                "remove_tags": entry.get("remove_tags") or [],
            }
            for entry, _, _ in preflight
        ]

    results: list[dict] = []
    for entry, _, _ in preflight:
        rel = entry["path"]
        try:
            results.append(merge_tags(
                rel_path=rel,
                add_tags=entry.get("add_tags") or [],
                remove_tags=entry.get("remove_tags") or [],
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
        results = _load_result_entries(rp)
        # Canonicalize any LLM-normalized paths (e.g. curly→straight quotes)
        # back to their on-disk form before set comparison.
        result_paths: set[str] = set()
        for e in results:
            p = e["path"]
            canonical = _canonicalize_path(p, batch_paths)
            result_paths.add(canonical or p)
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


def apply_consolidation(
    changes: list[dict],
    consolidation_candidates: list[dict],
    confidence_threshold: float = 0.90,
) -> tuple[list[dict], list[dict]]:
    """Auto-merge near-duplicate tags in changes based on consolidation_candidates.

    For candidates with score >= confidence_threshold, automatically replace the
    new tag with the existing variant. Returns (updated_changes, flagged_for_review).

    Flagged entries have score < confidence_threshold but >= 0.85.
    """
    high_conf = {c["proposed"]: c["nearest"] for c in consolidation_candidates if c["score"] >= confidence_threshold}
    low_conf = [c for c in consolidation_candidates if 0.85 <= c["score"] < confidence_threshold]

    updated = []
    for entry in changes:
        add_tags = entry.get("add_tags") or []
        new_adds = []
        merged = []
        for tag in add_tags:
            if tag in high_conf:
                merged.append({"from": tag, "to": high_conf[tag]})
                new_adds.append(high_conf[tag])
            else:
                new_adds.append(tag)
        entry = dict(entry)
        entry["add_tags"] = list(set(new_adds))
        if merged:
            entry["_consolidation_applied"] = merged
        updated.append(entry)

    return updated, low_conf


def aggregate_results(results_dir: str) -> dict:
    """Flatten all batch_*.json in results_dir, apply aliases, drop blocklisted
    and new-singletons. Returns {changes, rejected, consolidation_candidates}.
    """
    rdir = Path(results_dir)
    if not rdir.exists():
        return {"error": f"results dir not found: {results_dir}"}
    # Canonical on-disk paths, used to fold LLM-normalized result paths back
    # to the real filename before downstream apply steps try to open them.
    canonical_paths = {str(p.relative_to(VAULT)) for p in _iter_notes()}
    all_entries: list[dict] = []
    for f in sorted(rdir.glob("batch_*.json")):
        try:
            entries = _load_result_entries(f)
        except Exception as e:
            return {"error": f"parse {f.name}: {e}"}
        for e in entries:
            canonical = _canonicalize_path(e["path"], canonical_paths)
            if canonical:
                e["path"] = canonical
            all_entries.append(e)

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

## Step 1 — Seed (run in parallel)

Call `mcp__obsidian-search__bulk_tag_taxonomy()` AND `mcp__obsidian-search__bulk_tag_list()` **in a single parallel message** — both tool calls in one response block, not sequentially.

## Step 2 — Create batches (automatic)

Call `mcp__obsidian-search__bulk_tag_create_batches(paths=<note list from step 1>)` to automatically split notes into batches. By default, batches are **size-aware**: each batch's estimated `bulk_tag_prepare` payload stays under ~80K chars (safely below Claude Code's 25K-token MCP tool-result cap). A batch of small standup notes may hold 30+; a batch of large daily logs may hold only 10–20. Batch length is clamped to [5, 40].

Pass `batch_size=N` to force a uniform batch size (not recommended — can exceed the MCP cap on big notes). Returns batch file paths and per-batch sizes.

## Step 3 — Dispatch subagents IN PARALLEL (all Haiku)

For every batch, spawn one `general-purpose` subagent with **`model: "haiku"`**. Send them all in a single message so they run concurrently.

Each subagent's prompt must include:
- The note paths as a **JSON array inlined directly in the prompt** (do NOT ask the agent to read the batch file — copy the array here so the agent can call `bulk_tag_prepare` immediately)
- The complete tag vocabulary as comma-separated tag names (call `mcp__obsidian-search__bulk_tag_taxonomy()` and extract tag names). Send **only tag names**, not counts. This ensures subagents see the full tag context, including rare/specialized tags that matter for domain-specific tagging.
- Output file path (e.g. `logs/tag-run/results/batch_00.json`)
- Rules:
  1. Call `mcp__obsidian-search__bulk_tag_prepare(paths=<batch paths from prompt>)` **once** per batch. The response includes `existing_tags` and `content_excerpt` for every note — do NOT call `read_note` per-file.
  2. Propose **only additions** — do NOT re-propose any tag already in `existing_tags`. Empty `add_tags: []` is valid and preferred when coverage is already good.
  3. Target up to {MAX_NEW_TAGS_PER_NOTE} new tags per note. The apply layer enforces a hard cap of {MAX_NEW_TAGS_PER_NOTE}; proposals beyond that are dropped.
  4. Prefer taxonomy tags. Coin NEW tags only for load-bearing recurring themes, not incidentals. Lowercase kebab-case.
  5. `remove_tags` only for factually-wrong tags (high bar; e.g. `aws` on a pure-DigitalOcean file).
  6. Use the `Write` tool (NOT Bash) to write the JSON array to the output file: `[{{"path": "...", "add_tags": [...], "remove_tags": [...]}}, ...]`. No prose.

## Step 4 — Verify + aggregate (verify in parallel)

Call ALL `mcp__obsidian-search__bulk_tag_verify(batch_file, result_file)` calls **in a single parallel message** — one per batch, all at once. If any returns `ok=false`, abort immediately and report which batches failed — do NOT proceed to aggregate or apply with partial/stale results.

Then call `mcp__obsidian-search__bulk_tag_aggregate(results_dir="logs/tag-run/results")`.

Finally, call `mcp__obsidian-search__bulk_tag_consolidate(changes=<aggregated changes>, consolidation_candidates=<from aggregate>, confidence_threshold=0.90)` to automatically merge high-confidence near-duplicates. This merges any new tag that matches an existing tag with score ≥ 0.90. Review the returned `flagged_for_review` (score 0.85-0.89) manually if desired, but they are optional.

## Step 5 — Apply (parallel batches)

Split the changes list into N roughly equal chunks (target 3–4 chunks of ~60 notes). Call `mcp__obsidian-search__bulk_tag_apply(changes=<chunk>)` for each chunk **in a single parallel message** — chunks cover different notes so there are no write conflicts. Collect all results and report any errors per chunk.

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
- **Complete taxonomy sent to subagents**: Subagents receive all tags (not compressed). This ensures rare/specialized tags are visible and can be proposed, avoiding duplication or missed tagging opportunities. Cost impact is minimal (~$0.003/run).
- **Future enhancement**: If Claude API supports prompt caching for MCP (check Claude API docs), the full taxonomy + rules can be cached across all subagent calls, saving ~90% on those tokens.
- **Consolidation**: The workflow automatically merges high-confidence near-duplicate tags (score ≥0.90). Tags with lower scores (0.85-0.89) are flagged for optional manual review.
"""


# Claude Code's default MCP tool-result cap is 25K tokens (~100K chars).
# Each prepare() note contributes min(body_size, HEAD_CHARS + TAIL_CHARS) + JSON
# overhead (path, existing_tags, flags). Target 80K chars/batch for safety margin.
BATCH_CHAR_BUDGET = 80_000
NOTE_OVERHEAD_CHARS = 500
MIN_BATCH = 5
MAX_BATCH = 40


def _estimate_note_payload(path: str) -> int:
    """Estimate chars a single note contributes to a prepare() response.

    After _truncate(), body is capped at HEAD_CHARS + TAIL_CHARS (+ divider).
    Add fixed JSON overhead for keys, path, and existing_tags list.
    """
    try:
        size = (VAULT / path).stat().st_size
    except OSError:
        size = HEAD_CHARS + TAIL_CHARS
    body = min(size, HEAD_CHARS + TAIL_CHARS + 200)
    return body + NOTE_OVERHEAD_CHARS + len(path)


def pack_batches_by_size(paths: list[str], char_budget: int = BATCH_CHAR_BUDGET) -> list[list[str]]:
    """Greedy-pack paths into batches, keeping each batch's estimated prepare()
    payload under char_budget. Clamps batch length to [MIN_BATCH, MAX_BATCH]."""
    batches: list[list[str]] = []
    current: list[str] = []
    current_size = 0
    for p in paths:
        cost = _estimate_note_payload(p)
        would_exceed = current_size + cost > char_budget
        hit_cap = len(current) >= MAX_BATCH
        if current and (would_exceed or hit_cap) and len(current) >= MIN_BATCH:
            batches.append(current)
            current, current_size = [], 0
        elif current and hit_cap:
            batches.append(current)
            current, current_size = [], 0
        current.append(p)
        current_size += cost
    if current:
        batches.append(current)
    return batches


def create_batches(paths: list[str], batch_size: Optional[int] = None, output_dir: str = "logs/tag-run/batches") -> dict:
    """Create batch files from a list of note paths.

    Splits paths into batches and writes each as a JSON array to
    output_dir/batch_NN.json. Clears stale batch/result files first.

    If batch_size is None, uses size-aware packing: each batch's estimated
    prepare() payload stays under BATCH_CHAR_BUDGET (80K chars, safely below
    Claude Code's 25K-token MCP result cap). Batch length clamped to
    [MIN_BATCH, MAX_BATCH]. A batch of tiny standup notes may hold 30+; a
    batch of big daily logs may hold ~20.

    If batch_size is an int, uses uniform fixed-size batching (override).

    Returns {batch_files, total_notes, num_batches, batch_size | batch_sizes}.
    """
    if batch_size is None:
        batches = pack_batches_by_size(paths)
    else:
        batches = [paths[i:i + batch_size] for i in range(0, len(paths), batch_size)]

    output_path = Path(output_dir).resolve()
    output_path.mkdir(parents=True, exist_ok=True)

    # Clean stale batch and result files
    for f in output_path.glob("batch_*.json"):
        f.unlink()
    for f in Path(output_dir.replace("batches", "results")).glob("batch_*.json"):
        f.unlink()

    batch_files = []
    for i, batch in enumerate(batches):
        batch_file = output_path / f"batch_{i:02d}.json"
        batch_file.write_text(json.dumps(batch, indent=2))
        batch_files.append(str(batch_file))

    result = {
        "batch_files": batch_files,
        "total_notes": len(paths),
        "num_batches": len(batches),
    }
    if batch_size is None:
        result["batch_sizes"] = [len(b) for b in batches]
    else:
        result["batch_size"] = batch_size
    return result


def workflow_prompt() -> str:
    """Return the bulk-tag workflow orchestration prompt."""
    return WORKFLOW_PROMPT
