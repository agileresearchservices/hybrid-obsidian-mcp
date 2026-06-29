"""Vault write operations for Obsidian MCP server.

Handles todos, daily logs, and note creation/updates.
All functions return plain strings suitable for MCP tool responses.
"""

import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .config import OBSIDIAN_VAULT_PATH

VAULT_PATH = Path(OBSIDIAN_VAULT_PATH)
TODO_FILE = "KMW/TODO.md"
DAILY_LOG_DIR = "Daily Log"


# ============================================================================
# Safety
# ============================================================================

def _safe_path(path: Path) -> bool:
    """Prevent path traversal outside vault."""
    try:
        return path.resolve().is_relative_to(VAULT_PATH.resolve())
    except Exception:
        return False


def _resolve(rel_path: str) -> Optional[Path]:
    """Resolve a vault-relative path. Rejects absolute paths and traversal escapes."""
    if not rel_path or rel_path.startswith("/") or rel_path.startswith("~"):
        return None
    target = VAULT_PATH / rel_path
    if not _safe_path(target):
        return None
    return target.resolve()


# ============================================================================
# Frontmatter
# ============================================================================

def _parse_frontmatter(content: str) -> tuple[dict, str]:
    match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
    if not match:
        return {}, content
    fm: dict = {}
    current_key = None
    current_list: list | None = None
    for line in match.group(1).split("\n"):
        stripped = line.strip()
        if stripped.startswith("- ") and current_list is not None:
            # List item for the current key
            current_list.append(stripped[2:].strip().strip("\"'"))
        elif ":" in stripped and not stripped.startswith("-"):
            # Save previous list key if any
            if current_key and current_list is not None:
                fm[current_key] = current_list
            k, v = stripped.split(":", 1)
            current_key = k.strip()
            v = v.strip().strip("\"'")
            if v.startswith("[") and v.endswith("]"):
                # Inline list: tags: [a, b, c]
                fm[current_key] = [i.strip().strip("\"'") for i in v[1:-1].split(",") if i.strip()]
                current_key = None
                current_list = None
            elif v == "":
                # Multi-line list follows
                current_list = []
            else:
                fm[current_key] = v
                current_key = None
                current_list = None
    if current_key and current_list is not None:
        fm[current_key] = current_list
    return fm, content[match.end():]


def _make_frontmatter(title: str, tags: list[str] = None, date: datetime = None) -> str:
    date = date or datetime.now()
    lines = [
        "---",
        f"date: {date.strftime('%Y-%m-%d')}",
        f"title: {title}",
    ]
    if tags:
        lines.append("tags:")
        for t in tags:
            lines.append(f"  - {t}")
    lines.append("---")
    return "\n".join(lines) + "\n"


# ============================================================================
# Todos
# ============================================================================

def _parse_todos(content: str) -> list[dict]:
    todos = []
    for i, line in enumerate(content.split("\n")):
        m = re.match(r"^(\s*)-\s*\[([ xX])\]\s*(.*)$", line)
        if m:
            indent, status, text = m.groups()
            tags = re.findall(r"#([\w][\w-]*)", text)
            todos.append({
                "id": i,
                "text": text.strip(),
                "completed": status.lower() == "x",
                "tags": tags,
                "indent": len(indent),
            })
    return todos


def list_todos(tag: str = None, status: str = "open", limit: int = 50) -> str:
    todo_path = VAULT_PATH / TODO_FILE
    if not todo_path.exists():
        return "TODO.md not found."

    content = todo_path.read_text(encoding="utf-8")
    todos = _parse_todos(content)

    if status == "open":
        todos = [t for t in todos if not t["completed"]]
    elif status == "completed":
        todos = [t for t in todos if t["completed"]]

    if tag:
        todos = [t for t in todos if tag.lower() in [tg.lower() for tg in t["tags"]]]

    todos = todos[:limit]

    if not todos:
        return f"No {'open' if status == 'open' else status} todos{f' tagged #{tag}' if tag else ''}."

    lines = [f"Found {len(todos)} todo(s):\n"]
    for t in todos:
        mark = "[x]" if t["completed"] else "[ ]"
        tag_str = " ".join(f"#{tg}" for tg in t["tags"])
        lines.append(f"  ID {t['id']:>4}  {mark}  {tag_str + '  ' if tag_str else ''}{t['text']}")
    return "\n".join(lines)


def add_todo(text: str, tags: list[str] = None) -> str:
    tags = tags or []
    todo_path = VAULT_PATH / TODO_FILE

    # Extract inline tags from text (e.g. "#Inbox Call customer")
    inline_tags = re.findall(r"#(\w+)", text)
    clean_text = re.sub(r"#\w+\s*", "", text).strip()
    all_tags = inline_tags or tags
    primary_tag = all_tags[0].lower() if all_tags else None

    tag_str = " ".join(f"#{t}" for t in all_tags)
    todo_line = f"- [ ] {tag_str} {clean_text}".strip() if tag_str else f"- [ ] {clean_text}"

    if not todo_path.exists():
        todo_path.write_text("# Todos\n\n", encoding="utf-8")

    content = todo_path.read_text(encoding="utf-8")

    if primary_tag:
        section = f"## {primary_tag.capitalize()}"
        lines = content.split("\n")
        insert_idx = None
        for i, line in enumerate(lines):
            if line.lower() == section.lower():
                insert_idx = i + 1
                break
        if insert_idx is not None:
            lines.insert(insert_idx, todo_line)
            content = "\n".join(lines)
        else:
            content = content.rstrip("\n") + f"\n\n{section}\n{todo_line}\n"
    else:
        content = content.rstrip("\n") + f"\n{todo_line}\n"

    todo_path.write_text(content, encoding="utf-8")
    return f"Added todo: {clean_text}"


def complete_todo(todo_id: int) -> str:
    todo_path = VAULT_PATH / TODO_FILE
    if not todo_path.exists():
        return "TODO.md not found."

    lines = todo_path.read_text(encoding="utf-8").split("\n")
    if todo_id < 0 or todo_id >= len(lines):
        return f"Invalid todo ID: {todo_id}. Run list_todos to see valid IDs."

    line = lines[todo_id]
    if "[ ]" in line:
        lines[todo_id] = line.replace("[ ]", "[x]", 1)
        todo_path.write_text("\n".join(lines), encoding="utf-8")
        return f"Marked todo {todo_id} as complete."
    elif "[x]" in line.lower():
        return f"Todo {todo_id} is already completed."
    else:
        return f"Line {todo_id} is not a todo item."


def search_todos(query: str) -> str:
    todo_path = VAULT_PATH / TODO_FILE
    if not todo_path.exists():
        return "TODO.md not found."

    content = todo_path.read_text(encoding="utf-8")
    todos = _parse_todos(content)
    matches = [t for t in todos if query.lower() in t["text"].lower()]

    if not matches:
        return f"No todos found matching: {query}"

    lines = [f"Found {len(matches)} matching todo(s):\n"]
    for t in matches:
        mark = "[x]" if t["completed"] else "[ ]"
        tag_str = " ".join(f"#{tg}" for tg in t["tags"])
        lines.append(f"  ID {t['id']:>4}  {mark}  {tag_str + '  ' if tag_str else ''}{t['text']}")
    return "\n".join(lines)


# ============================================================================
# Daily Log
# ============================================================================

def _log_path(date: datetime) -> Path:
    return VAULT_PATH / DAILY_LOG_DIR / f"{date.strftime('%Y-%m-%d')}.md"


def _log_template(date: datetime) -> str:
    day_name = date.strftime("%A")
    date_str = date.strftime("%B %d, %Y")
    fm = _make_frontmatter(
        title=f"Daily Log — {date_str} ({day_name})",
        tags=["daily-log"],
        date=date,
    )
    return (
        fm
        + f"\n# Daily Log — {date_str} ({day_name})\n\n"
        "## Notes 📝\n\n\n"
        "## Tasks ✅\n- [ ] \n\n\n"
        "## Completed Today 🎉\n\n\n"
        "## Blocked Items 🚫\n\n\n"
        "## Tomorrow's Focus 🎯\n\n"
    )


def daily_log_create(date_str: str = None, force: bool = False) -> str:
    date = datetime.strptime(date_str, "%Y-%m-%d") if date_str else datetime.now()
    path = _log_path(date)

    if path.exists() and not force:
        return f"Daily log for {date.strftime('%Y-%m-%d')} already exists. Use force=true to overwrite."

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_log_template(date), encoding="utf-8")
    return f"Created daily log: {path.name}"


def daily_log_view(date_str: str = None) -> str:
    date = datetime.strptime(date_str, "%Y-%m-%d") if date_str else datetime.now()
    path = _log_path(date)

    if not path.exists():
        return f"No daily log found for {date.strftime('%Y-%m-%d')}."

    return path.read_text(encoding="utf-8")


def daily_log_append(content: str, section: str = None, date_str: str = None) -> str:
    date = datetime.strptime(date_str, "%Y-%m-%d") if date_str else datetime.now()
    path = _log_path(date)

    if not path.exists():
        result = daily_log_create(date_str=date.strftime("%Y-%m-%d"))
        if "already exists" in result or "Created" not in result:
            return result

    current = path.read_text(encoding="utf-8")

    if section:
        marker = f"## {section}"
        if marker in current:
            parts = current.split(marker, 1)
            next_sec = re.search(r"\n## ", parts[1])
            if next_sec:
                insert_at = len(parts[0]) + len(marker) + next_sec.start()
            else:
                insert_at = len(current)
            current = current[:insert_at].rstrip("\n") + f"\n{content}\n" + current[insert_at:]
        else:
            current = current.rstrip("\n") + f"\n\n{marker}\n{content}\n"
    else:
        current = current.rstrip("\n") + f"\n{content}\n"

    path.write_text(current, encoding="utf-8")
    return f"Appended to daily log{f' ({section})' if section else ''}."


def daily_log_summary(days: int = 7) -> str:
    lines = [f"Daily log summary — last {days} days:\n"]
    for i in range(days):
        date = datetime.now() - timedelta(days=i)
        path = _log_path(date)
        if path.exists():
            size = path.stat().st_size
            lines.append(f"  {date.strftime('%Y-%m-%d')} ({date.strftime('%a')}) — {size:,} bytes")
        else:
            lines.append(f"  {date.strftime('%Y-%m-%d')} ({date.strftime('%a')}) — no log")
    return "\n".join(lines)


# ============================================================================
# Notes
# ============================================================================

def note_create(title: str, content: str = "", folder: str = None, tags: list[str] = None) -> str:
    tags = tags or []

    if folder:
        note_dir = _resolve(folder)
        if note_dir is None:
            return f"Invalid folder path: {folder}"
        note_dir.mkdir(parents=True, exist_ok=True)
        note_path = note_dir / f"{title}.md"
    else:
        note_path = VAULT_PATH / f"{title}.md"

    if note_path.exists():
        return f"Note already exists: {note_path.relative_to(VAULT_PATH)}"

    fm = _make_frontmatter(title=title, tags=tags)
    note_path.write_text(fm + f"\n# {title}\n\n{content}", encoding="utf-8")
    return f"Created note: {note_path.relative_to(VAULT_PATH)}"


def note_append(rel_path: str, content: str) -> str:
    path = _resolve(rel_path)
    if path is None:
        return f"Invalid path: {rel_path}"
    if not path.exists():
        return f"Note not found: {rel_path}"

    current = path.read_text(encoding="utf-8")
    path.write_text(current.rstrip("\n") + f"\n{content}\n", encoding="utf-8")
    return f"Appended to: {rel_path}"


def recent_notes(limit: int = 10) -> str:
    results = []
    for md_file in VAULT_PATH.rglob("*.md"):
        if not _safe_path(md_file):
            continue
        try:
            fm, _ = _parse_frontmatter(md_file.read_text(encoding="utf-8", errors="ignore"))
            results.append({
                "path": str(md_file.relative_to(VAULT_PATH)),
                "title": fm.get("title", md_file.stem),
                "modified": datetime.fromtimestamp(md_file.stat().st_mtime),
            })
        except Exception:
            pass

    results.sort(key=lambda x: x["modified"], reverse=True)
    results = results[:limit]

    if not results:
        return "No notes found."

    lines = [f"Recently modified notes ({len(results)}):\n"]
    for r in results:
        delta = datetime.now() - r["modified"]
        if delta.total_seconds() < 3600:
            age = f"{int(delta.total_seconds() / 60)}m ago"
        elif delta.total_seconds() < 86400:
            age = f"{int(delta.total_seconds() / 3600)}h ago"
        else:
            age = r["modified"].strftime("%Y-%m-%d")
        lines.append(f"  {age:>12}  {r['title']}")
    return "\n".join(lines)


def search_notes(
    query: str = "",
    tags: list[str] = None,
    exclude_tags: list[str] = None,
    folder: str = None,
    date_from: str = None,
    date_to: str = None,
    limit: int = 10,
) -> str:
    """Text search over vault notes with optional frontmatter filtering.

    Scans all vault .md files, matches query against title + body content,
    and filters by tags/folder/date. Returns results sorted by recency.
    """
    query_lower = query.strip().lower() if query else None
    matches = []

    for md_file in VAULT_PATH.rglob("*.md"):
        rel = md_file.relative_to(VAULT_PATH)
        if any(p.startswith(".") for p in rel.parts[:-1]):
            continue
        if not _safe_path(md_file):
            continue

        rel_str = str(rel)
        if folder and not rel_str.startswith(folder):
            continue

        try:
            raw = md_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        fm, body = _parse_frontmatter(raw)
        note_tags = fm.get("tags", [])
        if isinstance(note_tags, str):
            note_tags = [note_tags]
        note_tags_lower = [t.lower() for t in note_tags]

        if tags and not any(t.lower() in note_tags_lower for t in tags):
            continue
        if exclude_tags and any(t.lower() in note_tags_lower for t in exclude_tags):
            continue

        note_date = str(fm.get("date", ""))
        if date_from and note_date and note_date < date_from:
            continue
        if date_to and note_date and note_date > date_to:
            continue

        if query_lower:
            title = str(fm.get("title", md_file.stem))
            if query_lower not in title.lower() and query_lower not in body.lower():
                continue

        snippet = ""
        if query_lower and body:
            idx = body.lower().find(query_lower)
            if idx >= 0:
                start = max(0, idx - 80)
                end = min(len(body), idx + len(query_lower) + 120)
                snippet = ("…" if start > 0 else "") + body[start:end].strip() + ("…" if end < len(body) else "")
        elif body:
            snippet = body[:200].strip() + ("…" if len(body) > 200 else "")

        matches.append({
            "path": rel_str,
            "title": str(fm.get("title", md_file.stem)),
            "date": note_date,
            "tags": note_tags,
            "snippet": snippet,
            "mtime": md_file.stat().st_mtime,
        })

    matches.sort(key=lambda x: x["mtime"], reverse=True)
    matches = matches[:limit]

    if not matches:
        return "No notes found matching your query."

    lines = [f"Found {len(matches)} note(s):\n"]
    for r in matches:
        date_part = f" ({r['date']})" if r["date"] else ""
        lines.append(f"### {r['title']}{date_part}")
        lines.append(f"  Path: {r['path']}")
        if r["tags"]:
            lines.append(f"  Tags: {', '.join(r['tags'])}")
        if r["snippet"]:
            lines.append(f"  {r['snippet']}")
        lines.append("")
    return "\n".join(lines)


def list_notes(
    folder: str = None,
    tags: list[str] = None,
    exclude_tags: list[str] = None,
    date_from: str = None,
    date_to: str = None,
    limit: int = 50,
) -> str:
    """List vault notes with optional metadata filtering, sorted by recency."""
    results = []

    for md_file in VAULT_PATH.rglob("*.md"):
        rel = md_file.relative_to(VAULT_PATH)
        if any(p.startswith(".") for p in rel.parts[:-1]):
            continue
        if not _safe_path(md_file):
            continue

        rel_str = str(rel)
        if folder and not rel_str.startswith(folder):
            continue

        try:
            raw = md_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        fm, _ = _parse_frontmatter(raw)
        note_tags = fm.get("tags", [])
        if isinstance(note_tags, str):
            note_tags = [note_tags]
        note_tags_lower = [t.lower() for t in note_tags]

        if tags and not any(t.lower() in note_tags_lower for t in tags):
            continue
        if exclude_tags and any(t.lower() in note_tags_lower for t in exclude_tags):
            continue

        note_date = str(fm.get("date", ""))
        if date_from and note_date and note_date < date_from:
            continue
        if date_to and note_date and note_date > date_to:
            continue

        results.append({
            "path": rel_str,
            "title": str(fm.get("title", md_file.stem)),
            "date": note_date,
            "tags": note_tags,
            "mtime": md_file.stat().st_mtime,
        })

    results.sort(key=lambda x: x["mtime"], reverse=True)
    results = results[:limit]

    if not results:
        return "No notes found."

    lines = [f"Found {len(results)} note(s):\n"]
    for r in results:
        tag_str = f" [{', '.join(r['tags'])}]" if r["tags"] else ""
        date_str = f" ({r['date']})" if r["date"] else ""
        lines.append(f"  {r['title']}{date_str} — {r['path']}{tag_str}")
    return "\n".join(lines)


def vault_stats() -> str:
    total_notes = 0
    total_todos = 0
    completed_todos = 0
    total_size = 0
    tag_counts: dict = defaultdict(int)
    last_modified = None

    for md_file in VAULT_PATH.rglob("*.md"):
        if not _safe_path(md_file):
            continue
        total_notes += 1
        total_size += md_file.stat().st_size
        mtime = datetime.fromtimestamp(md_file.stat().st_mtime)
        if last_modified is None or mtime > last_modified:
            last_modified = mtime
        try:
            content = md_file.read_text(encoding="utf-8", errors="ignore")
            fm, _ = _parse_frontmatter(content)
            raw_tags = fm.get("tags", [])
            tag_list = raw_tags if isinstance(raw_tags, list) else [raw_tags] if raw_tags else []
            for tag in tag_list:
                tag_counts[tag.strip()] += 1
            todos = _parse_todos(content)
            total_todos += len(todos)
            completed_todos += sum(1 for t in todos if t["completed"])
        except Exception:
            pass

    size_str = f"{total_size / 1024:.1f} KB" if total_size < 1024 * 1024 else f"{total_size / 1024 / 1024:.1f} MB"
    lines = [
        "Vault Statistics\n",
        f"  Notes:      {total_notes}",
        f"  Size:       {size_str}",
        f"  Todos:      {total_todos} total ({total_todos - completed_todos} open, {completed_todos} done)",
        f"  Modified:   {last_modified.strftime('%Y-%m-%d %H:%M') if last_modified else 'N/A'}",
    ]
    if tag_counts:
        top = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:8]
        lines.append("\n  Top tags:")
        for tag, count in top:
            lines.append(f"    #{tag}: {count}")
    return "\n".join(lines)
