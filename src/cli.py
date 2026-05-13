"""obsidian-cli — shell-facing CLI over the Obsidian MCP project.

Exposes every write op, read op, and bulk-tag op as a subcommand. This is the
single surface for shell/cron/automation consumers (slack-gateway, daily-digest,
etc.). Same Python codepath as the MCP tools.

Usage:
    obsidian-cli <subcommand> [args...]
    obsidian-cli help
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from . import writer
from . import tagger


def _split_csv(s: Optional[str]) -> Optional[list[str]]:
    if not s:
        return None
    return [x.strip() for x in s.split(",") if x.strip()]


# ----------------------------------------------------------------------------
# Todo handlers
# ----------------------------------------------------------------------------

def cmd_list_todos(args: argparse.Namespace) -> int:
    print(writer.list_todos(tag=args.tag, status=args.status, limit=args.limit))
    return 0


def cmd_add_todo(args: argparse.Namespace) -> int:
    text = " ".join(args.text)
    print(writer.add_todo(text=text, tags=_split_csv(args.tags) or []))
    return 0


def cmd_complete_todo(args: argparse.Namespace) -> int:
    print(writer.complete_todo(todo_id=args.id))
    return 0


def cmd_search_todos(args: argparse.Namespace) -> int:
    query = " ".join(args.query)
    print(writer.search_todos(query=query))
    return 0


# ----------------------------------------------------------------------------
# Daily log handlers
# ----------------------------------------------------------------------------

def cmd_daily_log(args: argparse.Namespace) -> int:
    sub = args.daily_sub
    if sub == "view":
        print(writer.daily_log_view(date_str=args.date))
    elif sub == "create":
        print(writer.daily_log_create(date_str=args.date, force=args.force))
    elif sub == "append":
        content = " ".join(args.content)
        print(writer.daily_log_append(
            content=content, section=args.section, date_str=args.date,
        ))
    elif sub == "summary":
        print(writer.daily_log_summary(days=args.days))
    else:
        print(f"Unknown daily-log subcommand: {sub}", file=sys.stderr)
        return 2
    return 0


# ----------------------------------------------------------------------------
# Note handlers
# ----------------------------------------------------------------------------

def cmd_note(args: argparse.Namespace) -> int:
    sub = args.note_sub
    if sub == "create":
        print(writer.note_create(
            title=args.title,
            content=args.content or "",
            folder=args.folder,
            tags=_split_csv(args.tags) or [],
        ))
    elif sub == "append":
        print(writer.note_append(rel_path=args.path, content=args.content))
    else:
        print(f"Unknown note subcommand: {sub}", file=sys.stderr)
        return 2
    return 0


def cmd_recent_notes(args: argparse.Namespace) -> int:
    print(writer.recent_notes(limit=args.limit))
    return 0


def cmd_stats(_args: argparse.Namespace) -> int:
    print(writer.vault_stats())
    return 0


# ----------------------------------------------------------------------------
# Search / list / read (delegate to searcher for feature parity with MCP)
# ----------------------------------------------------------------------------

def cmd_search(args: argparse.Namespace) -> int:
    from .searcher import hybrid_search
    query = " ".join(args.query)
    results = hybrid_search(
        query=query,
        k=args.k,
        tags=_split_csv(args.tags),
        folder=args.folder,
        date_from=args.date_from,
        date_to=args.date_to,
        exclude_tags=_split_csv(args.exclude_tags),
        rerank=not args.no_rerank,
    )
    if not results:
        print("No results found.")
        return 0
    for i, r in enumerate(results, 1):
        title = r.metadata.get("title", "Unknown")
        date = r.metadata.get("date", "")
        fp = r.metadata.get("file_path", "")
        print(f"### {i}. {title}" + (f" ({date})" if date else "") + f"  [score: {r.score:.3f}]")
        if fp:
            print(f"  File: {fp}")
        print()
        print(r.chunk_text)
        print()
    return 0


def cmd_list_notes(args: argparse.Namespace) -> int:
    from .searcher import list_notes as list_notes_search
    results = list_notes_search(
        folder=args.folder,
        tags=_split_csv(args.tags),
        date_from=args.date_from,
        date_to=args.date_to,
        limit=args.limit,
        exclude_tags=_split_csv(args.exclude_tags),
    )
    if not results:
        print("No notes found matching filters.")
        return 0
    for note in results:
        title = note.get("title", "?")
        date = note.get("date", "")
        fp = note.get("file_path", "")
        tags = note.get("tags", [])
        line = f"- **{title}**"
        if date:
            line += f" ({date})"
        if fp:
            line += f" — `{fp}`"
        if tags:
            tag_str = ", ".join(tags) if isinstance(tags, list) else tags
            line += f" [{tag_str}]"
        print(line)
    return 0


def cmd_read_note(args: argparse.Namespace) -> int:
    from pathlib import Path
    from .config import OBSIDIAN_VAULT_PATH
    vault = Path(OBSIDIAN_VAULT_PATH)
    path = vault / args.path
    if not path.exists():
        print(f"Note not found: {args.path}", file=sys.stderr)
        return 1
    if not path.resolve().is_relative_to(vault.resolve()):
        print("Invalid path: outside vault", file=sys.stderr)
        return 2
    print(path.read_text(encoding="utf-8"))
    return 0


# ----------------------------------------------------------------------------
# Bulk tag handlers
# ----------------------------------------------------------------------------

def cmd_taxonomy(_args: argparse.Namespace) -> int:
    print(json.dumps(tagger.collect_taxonomy(), indent=2))
    return 0


def cmd_taxonomy_topk(args: argparse.Namespace) -> int:
    tags = tagger.collect_taxonomy_top_k(args.k)
    print("\n".join(tags))
    return 0


def cmd_tag_list(_args: argparse.Namespace) -> int:
    print(json.dumps(tagger.list_notes(), indent=2))
    return 0


def cmd_tag_apply(args: argparse.Namespace) -> int:
    data = json.load(sys.stdin)
    if not isinstance(data, list):
        print('{"error": "expected JSON list of {path, add_tags, remove_tags}"}',
              file=sys.stderr)
        return 2
    print(json.dumps(tagger.bulk_apply(data, dry_run=args.dry_run), indent=2))
    return 0


def cmd_tag_verify(args: argparse.Namespace) -> int:
    result = tagger.verify_batch(args.batch_file, args.result_file)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


def cmd_tag_aggregate(args: argparse.Namespace) -> int:
    print(json.dumps(tagger.aggregate_results(args.results_dir), indent=2))
    return 0


def cmd_tag_consolidate(args: argparse.Namespace) -> int:
    changes = json.load(sys.stdin)
    if not isinstance(changes, list):
        print('{"error": "expected JSON list of {path, add_tags, remove_tags} on stdin"}',
              file=sys.stderr)
        return 2
    consolidation_candidates = json.loads(args.candidates_json)
    if not isinstance(consolidation_candidates, list):
        print('{"error": "candidates must be a JSON list"}', file=sys.stderr)
        return 2
    updated, flagged = tagger.apply_consolidation(
        changes, consolidation_candidates, args.threshold
    )
    print(json.dumps({
        "changes": updated,
        "flagged_for_review": flagged,
    }, indent=2))
    return 0


def cmd_tag_prepare(_args: argparse.Namespace) -> int:
    data = json.load(sys.stdin)
    if not isinstance(data, list):
        print('{"error": "expected JSON list of paths on stdin"}', file=sys.stderr)
        return 2
    print(json.dumps(tagger.prepare_batch(data), indent=2))
    return 0


def cmd_workflow(_args: argparse.Namespace) -> int:
    print(tagger.workflow_prompt())
    return 0


# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------

def cmd_config(_args: argparse.Namespace) -> int:
    from .config import OBSIDIAN_VAULT_PATH
    print(f"VAULT_PATH={OBSIDIAN_VAULT_PATH}")
    return 0


# ----------------------------------------------------------------------------
# Parser
# ----------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="obsidian-cli")
    sp = ap.add_subparsers(dest="cmd", required=True)

    # Todos
    p = sp.add_parser("list-todos")
    p.add_argument("--tag")
    p.add_argument("--status", default="open", choices=["open", "completed", "all"])
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_list_todos)

    p = sp.add_parser("add-todo")
    p.add_argument("text", nargs="+")
    p.add_argument("--tags")
    p.set_defaults(func=cmd_add_todo)

    p = sp.add_parser("complete-todo")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_complete_todo)

    p = sp.add_parser("search-todos")
    p.add_argument("query", nargs="+")
    p.set_defaults(func=cmd_search_todos)

    # Daily log (nested)
    p = sp.add_parser("daily-log")
    dsp = p.add_subparsers(dest="daily_sub", required=True)

    dp = dsp.add_parser("view")
    dp.add_argument("--date")
    dp = dsp.add_parser("create")
    dp.add_argument("--date")
    dp.add_argument("--force", action="store_true")
    dp = dsp.add_parser("append")
    dp.add_argument("content", nargs="+")
    dp.add_argument("--section")
    dp.add_argument("--date")
    dp = dsp.add_parser("summary")
    dp.add_argument("--days", type=int, default=7)
    p.set_defaults(func=cmd_daily_log)

    # Notes
    p = sp.add_parser("note")
    nsp = p.add_subparsers(dest="note_sub", required=True)
    np_ = nsp.add_parser("create")
    np_.add_argument("title")
    np_.add_argument("--content")
    np_.add_argument("--folder")
    np_.add_argument("--tags")
    np_ = nsp.add_parser("append")
    np_.add_argument("path")
    np_.add_argument("content")
    p.set_defaults(func=cmd_note)

    p = sp.add_parser("recent-notes")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_recent_notes)

    p = sp.add_parser("stats")
    p.set_defaults(func=cmd_stats)

    # Search / list / read
    p = sp.add_parser("search")
    p.add_argument("query", nargs="+")
    p.add_argument("--k", type=int, default=5)
    p.add_argument("--tags")
    p.add_argument("--exclude-tags", dest="exclude_tags")
    p.add_argument("--folder")
    p.add_argument("--date-from", dest="date_from")
    p.add_argument("--date-to", dest="date_to")
    p.add_argument("--no-rerank", action="store_true")
    p.set_defaults(func=cmd_search)

    p = sp.add_parser("list-notes")
    p.add_argument("--folder")
    p.add_argument("--tags")
    p.add_argument("--exclude-tags", dest="exclude_tags")
    p.add_argument("--date-from", dest="date_from")
    p.add_argument("--date-to", dest="date_to")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_list_notes)

    p = sp.add_parser("read-note")
    p.add_argument("path")
    p.set_defaults(func=cmd_read_note)

    # Bulk tags
    p = sp.add_parser("taxonomy", help="Print tag→count JSON")
    p.set_defaults(func=cmd_taxonomy)
    p = sp.add_parser("taxonomy-topk", help="Print top-K tags (one per line)")
    p.add_argument("--k", type=int, default=100, help="Number of top tags (default 100)")
    p.set_defaults(func=cmd_taxonomy_topk)
    p = sp.add_parser("tag-list", help="Print all notes as JSON (for bulk tag batching)")
    p.set_defaults(func=cmd_tag_list)
    p = sp.add_parser("tag-apply", help="Read [{path, add_tags, remove_tags}] JSON from stdin and apply")
    p.add_argument("--dry-run", action="store_true", help="Preview without writing to the vault")
    p.set_defaults(func=cmd_tag_apply)
    p = sp.add_parser("tag-verify", help="Verify a result file covers its batch input (exit 1 if not ok)")
    p.add_argument("batch_file")
    p.add_argument("result_file")
    p.set_defaults(func=cmd_tag_verify)
    p = sp.add_parser("tag-aggregate", help="Flatten batch_*.json in a dir; drop blocklisted/singleton tags")
    p.add_argument("results_dir")
    p.set_defaults(func=cmd_tag_aggregate)
    p = sp.add_parser("tag-consolidate", help="Auto-merge high-confidence near-duplicate tags (read changes from stdin)")
    p.add_argument("candidates_json", help="Consolidation candidates as JSON string")
    p.add_argument("--threshold", type=float, default=0.90, help="Confidence threshold for auto-merge (0-1, default 0.90)")
    p.set_defaults(func=cmd_tag_consolidate)
    p = sp.add_parser("tag-prepare", help="Read JSON list of paths from stdin; return {existing_tags, content_excerpt}")
    p.set_defaults(func=cmd_tag_prepare)
    p = sp.add_parser("workflow", help="Print the bulk-tag orchestration prompt")
    p.set_defaults(func=cmd_workflow)

    # Config
    p = sp.add_parser("config")
    p.set_defaults(func=cmd_config)

    return ap


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
