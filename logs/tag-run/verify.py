"""Verify each batch result file matches its input batch paths."""
import json
from pathlib import Path

BASE = Path("/Users/kevin/github/personal/hybrid-obsidian-mcp/logs/tag-run")
batches = sorted((BASE / "batches").glob("batch_*.json"))

mismatches = []
for b in batches:
    idx = b.stem  # batch_NN
    r = BASE / "results" / b.name
    if not r.exists():
        mismatches.append((idx, "MISSING result"))
        continue
    input_paths = set(json.loads(b.read_text()))
    try:
        results = json.loads(r.read_text())
        result_paths = {e["path"] for e in results}
    except Exception as e:
        mismatches.append((idx, f"parse error: {e}"))
        continue
    missing = input_paths - result_paths
    extra = result_paths - input_paths
    if missing or extra:
        mismatches.append(
            (idx, f"missing={len(missing)} extra={len(extra)} input={len(input_paths)} result={len(result_paths)}")
        )
    else:
        print(f"{idx}: OK ({len(results)} entries)")

if mismatches:
    print("\nMISMATCHES:")
    for idx, msg in mismatches:
        print(f"  {idx}: {msg}")
else:
    print("\nAll batches verified.")
