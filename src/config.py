"""Configuration for hybrid-obsidian-mcp server."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).parent.parent / ".env")

# Obsidian vault
OBSIDIAN_VAULT_PATH = os.getenv(
    "OBSIDIAN_VAULT_PATH",
    str(Path.home() / "Library/Mobile Documents/iCloud~md~obsidian/Documents/obsidian-vault"),
)

# TTL (seconds) for the vault taxonomy cache used by bulk_tag_taxonomy and friends.
# Set to 0 to bypass (every call rescans the vault).
TAXONOMY_CACHE_TTL_SECONDS = int(os.getenv("TAXONOMY_CACHE_TTL_SECONDS", 60))

# In-process LRU for read_note(), keyed on (resolved_path, mtime_ns) so edits
# automatically invalidate. Set to 0 to disable.
READ_NOTE_CACHE_SIZE = int(os.getenv("READ_NOTE_CACHE_SIZE", 64))

# Chunking (still used by vault_parser for doc-type inference)
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", 1000))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", 200))

# NAS vault sync (mirrors vault .md files to a mounted Synology SMB share so
# Synology FileIndexing makes them searchable via the synology-search MCP).
# Set NAS_VAULT_SYNC_PATH to the mounted share path, e.g.:
#   /Volumes/Blanton/obsidian-vault
# Leave NAS_SYNC_ENABLED=false (the default) to disable entirely.
NAS_SYNC_ENABLED = os.getenv("NAS_SYNC_ENABLED", "false").lower() == "true"
NAS_VAULT_SYNC_PATH = os.getenv("NAS_VAULT_SYNC_PATH", "")
