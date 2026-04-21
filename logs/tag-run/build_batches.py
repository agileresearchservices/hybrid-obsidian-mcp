"""Fetch vault note list and split into ~20-note batches."""
import json
import subprocess
from pathlib import Path

CLI = "/Users/kevin/github/personal/hybrid-obsidian-mcp/.venv/bin/obsidian-cli"
OUT = Path("/Users/kevin/github/personal/hybrid-obsidian-mcp/logs/tag-run/batches")
OUT.mkdir(parents=True, exist_ok=True)

result = subprocess.run([CLI, "tag-list"], capture_output=True, text=True, check=True)
notes = json.loads(result.stdout)
paths = [n["path"] for n in notes]
print(f"Total notes: {len(paths)}")

BATCH_SIZE = 20
for i in range(0, len(paths), BATCH_SIZE):
    batch = paths[i : i + BATCH_SIZE]
    idx = i // BATCH_SIZE
    out_file = OUT / f"batch_{idx:02d}.json"
    out_file.write_text(json.dumps(batch, indent=2))
    print(f"Wrote {out_file.name} ({len(batch)} notes)")

print(f"Total batches: {(len(paths) + BATCH_SIZE - 1) // BATCH_SIZE}")
