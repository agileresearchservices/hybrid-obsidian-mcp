"""Mirror changed vault files to the NAS over a mounted SMB share.

When NAS_SYNC_ENABLED=true and NAS_VAULT_SYNC_PATH points to a mounted
Synology share directory, the vault watcher calls sync_to_nas() after each
debounce flush so the NAS copy stays current with the iCloud vault.
Synology FileIndexing then makes those notes searchable via the
synology-search MCP without any additional configuration.

Errors are always logged and suppressed — NAS sync must not disrupt the
primary indexing pipeline.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from .config import NAS_SYNC_ENABLED, NAS_VAULT_SYNC_PATH, OBSIDIAN_VAULT_PATH

logger = logging.getLogger(__name__)


def sync_to_nas(changed: list[str], deleted: list[str]) -> None:
    """Copy changed files and remove deleted files on the NAS sync mount.

    Args:
        changed: vault-relative paths of created/modified .md files
                 (e.g. "Daily Log/2026-06-02.md")
        deleted: vault-relative paths of removed/renamed-away .md files

    Both lists may be empty; the function exits early if there is nothing
    to do or if the NAS mount is not available.
    """
    if not NAS_SYNC_ENABLED or not NAS_VAULT_SYNC_PATH:
        return

    if not changed and not deleted:
        return

    nas_root = Path(NAS_VAULT_SYNC_PATH)
    vault_root = Path(OBSIDIAN_VAULT_PATH)

    if not nas_root.exists():
        logger.warning("NAS sync mount not found: %s — skipping sync", nas_root)
        return

    synced = errors = 0

    for rel in changed:
        src = vault_root / rel
        dst = nas_root / rel
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            # Use copyfile (data only) rather than copy2 — SMB mounts on macOS
            # reject metadata/xattr writes and raise [Errno 22] Invalid argument.
            shutil.copyfile(src, dst)
            synced += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("NAS sync copy failed (%s): %s", rel, e)
            errors += 1

    for rel in deleted:
        dst = nas_root / rel
        try:
            dst.unlink(missing_ok=True)
        except Exception as e:  # noqa: BLE001
            logger.warning("NAS sync delete failed (%s): %s", rel, e)
            errors += 1

    if synced or errors:
        logger.info(
            "NAS sync: %d copied%s",
            synced,
            f", {errors} error(s)" if errors else "",
        )
