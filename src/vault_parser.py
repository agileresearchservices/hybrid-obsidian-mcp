"""Obsidian vault parser - frontmatter extraction, chunking, tag parsing."""

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import frontmatter

from .config import CHUNK_SIZE, CHUNK_OVERLAP, OBSIDIAN_VAULT_PATH

# Per-doc_type chunk sizes: balance between semantic coherence and reranker effectiveness
# nomic-embed-text: 8192 token limit (~32K chars); cross-encoder: ~512 token sweet spot (~2K chars)
_CHUNK_SIZES: dict[str, int] = {
    "daily_log": 1500,  # section-aware primary; char fallback ceiling
    "weekly_summary": 2000,
    "customer": 2000,
    "project": 2000,
    "archive": 2000,
    "blog": 2000,
    "personal": 1500,
    "conference": 1500,
    "note": 1500,
    "todo": 1000,
    "work": 1000,
}
_DEFAULT_CHUNK_SIZE = 1500


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
    wikilinks: list[str] = field(default_factory=list)  # normalized [[link]] targets


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
    """Extract [[wiki links]] from content, ignoring matches inside code blocks.

    Shell scripts use `[[ ... ]]` for conditionals; we strip fenced and inline
    code first, then require the wiki-link inner text to start with a non-space
    character and stay on one line. That kicks out residual matches from
    unclosed Slack-style ``` fences (which leave bash conditionals exposed).
    """
    cleaned = re.sub(r"```[\s\S]*?```", "", content)
    cleaned = re.sub(r"`[^`]+`", "", cleaned)
    return re.findall(r"\[\[(\S[^\]|\n]*?)(?:\|[^\]\n]+)?\]\]", cleaned)


def normalize_wikilink(target: str) -> str:
    """Normalize a wikilink target for indexing/lookup.

    Strips section anchors (`Note#Section`), collapses whitespace, lowercases.
    Obsidian semantics: link text resolves to a note title, case-insensitive
    on the filesystem of typical vaults — lowercasing avoids `[[KMW]]` vs
    `[[kmw]]` mismatches.
    """
    target = target.split("#", 1)[0]
    return " ".join(target.split()).strip().lower()


def normalize_date(date_value) -> Optional[str]:
    """Normalize a date value to a real YYYY-MM-DD calendar date string.

    Returns None when the input doesn't look like a date *or* when it looks
    like one but isn't valid (e.g. frontmatter typos like `1031-20-25` or
    `9999-99-99`). OpenSearch's strict date parser rejects invalid dates,
    and the bulk indexer's `raise_on_error=False` would silently drop the
    whole chunk — better to drop the bad date and index the rest of the doc.
    """
    if date_value is None:
        return None
    date_str = str(date_value)
    match = re.match(r"(\d{4}-\d{2}-\d{2})", date_str)
    if not match:
        return None
    candidate = match.group(1)
    try:
        datetime.strptime(candidate, "%Y-%m-%d")
    except ValueError:
        return None
    return candidate


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

    # Extract inline tags and merge. Sorted so the chunk_hash used by the embed
    # cache is stable across runs (set() ordering varies with hash randomization).
    inline_tags = extract_inline_tags(post.content)
    all_tags = sorted(set(
        [t.lstrip("#") for t in fm_tags] + inline_tags
    ))

    doc_type = classify_doc_type(relative)
    content = post.content.strip()

    if not content or len(content) < 20:
        return None

    chunks = chunk_text(content, doc_type)

    raw_links = extract_wiki_links(post.content)
    wikilinks = sorted({n for n in (normalize_wikilink(t) for t in raw_links) if n})

    return ParsedNote(
        file_path=relative,
        title=title if isinstance(title, str) else str(title),
        date=date,
        tags=all_tags,
        folder=folder,
        doc_type=doc_type,
        content=content,
        chunks=chunks,
        wikilinks=wikilinks,
    )


def chunk_text(text: str, doc_type: str = "note") -> list[str]:
    """Split text into chunks, preferring section boundaries for structured docs.

    Uses per-doc_type sizes to balance semantic coherence with model capacity.
    """
    size = _CHUNK_SIZES.get(doc_type, _DEFAULT_CHUNK_SIZE)

    # For daily logs and structured docs, try section-aware chunking first
    if doc_type in ("daily_log", "weekly_summary"):
        sections = section_chunk(text)
        if sections:
            # Further chunk any sections that are too large
            result = []
            for section in sections:
                if len(section) > size * 1.5:
                    result.extend(char_chunk(section, size=size))
                else:
                    result.append(section)
            return result

    return char_chunk(text, size=size)


def section_chunk(text: str) -> list[str]:
    """Split text on ## headings, keeping each section intact."""
    sections = re.split(r"\n(?=## )", text)
    chunks = [s.strip() for s in sections if s.strip() and len(s.strip()) >= 20]
    return chunks if len(chunks) > 1 else []


def char_chunk(text: str, size: int = CHUNK_SIZE) -> list[str]:
    """Character-based chunking with overlap. Uses provided size or falls back to CHUNK_SIZE."""
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
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
