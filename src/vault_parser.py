"""Obsidian vault parser - frontmatter extraction, chunking, tag parsing."""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import frontmatter

from .config import CHUNK_SIZE, CHUNK_OVERLAP, OBSIDIAN_VAULT_PATH


@dataclass
class ParsedNote:
    """A parsed Obsidian note with metadata and chunks."""

    file_path: str  # relative to vault root
    title: str
    date: Optional[str]
    tags: list[str]
    folder: str
    doc_type: str
    content: str  # full raw content (without frontmatter)
    chunks: list[str] = field(default_factory=list)


def classify_doc_type(relative_path: str) -> str:
    """Classify a note's type based on its folder path."""
    if relative_path == "TODO.md":
        return "todo"
    parts = relative_path.split("/")
    if parts[0] == "Daily Log":
        return "daily_log"
    if parts[0] == "Weekly":
        return "weekly_summary"
    if parts[0] == "KMW":
        if len(parts) > 1:
            sub = parts[1].lower()
            if sub == "archive":
                return "archive"
            if sub == "blog":
                return "blog"
            if sub == "conferences":
                return "conference"
            if sub == "customers":
                return "customer"
            if sub == "lucille":
                return "project"
        return "work"
    if parts[0] == "Personal":
        return "personal"
    return "note"


def extract_inline_tags(content: str) -> list[str]:
    """Extract inline #tags from content (not inside code blocks)."""
    # Remove code blocks first
    cleaned = re.sub(r"```[\s\S]*?```", "", content)
    cleaned = re.sub(r"`[^`]+`", "", cleaned)
    # Match #tag patterns (not part of headings)
    tags = re.findall(r"(?:^|[\s(])#([a-zA-Z][\w-]*(?:/[\w-]+)*)", cleaned)
    return list(set(tags))


def extract_wiki_links(content: str) -> list[str]:
    """Extract [[wiki links]] from content."""
    return re.findall(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", content)


def normalize_date(date_value) -> Optional[str]:
    """Normalize a date value to YYYY-MM-DD string."""
    if date_value is None:
        return None
    date_str = str(date_value)
    # Handle ISO datetime format
    match = re.match(r"(\d{4}-\d{2}-\d{2})", date_str)
    if match:
        return match.group(1)
    return None


def parse_note(file_path: Path, vault_root: Path) -> Optional[ParsedNote]:
    """Parse a single Obsidian markdown file."""
    try:
        post = frontmatter.load(file_path)
    except Exception:
        return None

    relative = str(file_path.relative_to(vault_root))
    folder = str(file_path.parent.relative_to(vault_root))
    if folder == ".":
        folder = ""

    # Extract metadata from frontmatter
    title = post.get("title", file_path.stem.replace("-", " ").replace("_", " "))
    date = normalize_date(post.get("date"))
    fm_tags = post.get("tags", [])
    if isinstance(fm_tags, str):
        fm_tags = [t.strip() for t in fm_tags.split(",")]

    # Extract inline tags and merge
    inline_tags = extract_inline_tags(post.content)
    all_tags = list(set(
        [t.lstrip("#") for t in fm_tags] + inline_tags
    ))

    doc_type = classify_doc_type(relative)
    content = post.content.strip()

    if not content or len(content) < 20:
        return None

    chunks = chunk_text(content, doc_type)

    return ParsedNote(
        file_path=relative,
        title=title if isinstance(title, str) else str(title),
        date=date,
        tags=all_tags,
        folder=folder,
        doc_type=doc_type,
        content=content,
        chunks=chunks,
    )


def chunk_text(text: str, doc_type: str = "note") -> list[str]:
    """Split text into chunks, preferring section boundaries for structured docs."""
    # For daily logs and structured docs, try section-aware chunking first
    if doc_type in ("daily_log", "weekly_summary"):
        sections = section_chunk(text)
        if sections:
            # Further chunk any sections that are too large
            result = []
            for section in sections:
                if len(section) > CHUNK_SIZE * 1.5:
                    result.extend(char_chunk(section))
                else:
                    result.append(section)
            return result

    return char_chunk(text)


def section_chunk(text: str) -> list[str]:
    """Split text on ## headings, keeping each section intact."""
    sections = re.split(r"\n(?=## )", text)
    chunks = [s.strip() for s in sections if s.strip() and len(s.strip()) >= 20]
    return chunks if len(chunks) > 1 else []


def char_chunk(text: str) -> list[str]:
    """Character-based chunking with overlap."""
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + CHUNK_SIZE, len(text))
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk.strip())
        start = end - CHUNK_OVERLAP
        if end == len(text):
            break
    return chunks


def discover_notes(vault_path: Optional[str] = None) -> list[Path]:
    """Find all markdown files in the vault, excluding .obsidian/ config."""
    root = Path(vault_path or OBSIDIAN_VAULT_PATH)
    notes = []
    for md_file in sorted(root.rglob("*.md")):
        # Skip Obsidian config files
        if ".obsidian" in md_file.parts:
            continue
        notes.append(md_file)
    return notes


def parse_vault(vault_path: Optional[str] = None) -> list[ParsedNote]:
    """Parse all notes in the vault."""
    root = Path(vault_path or OBSIDIAN_VAULT_PATH)
    notes = discover_notes(vault_path)
    parsed = []
    for note_path in notes:
        note = parse_note(note_path, root)
        if note:
            parsed.append(note)
    return parsed
