"""Vault file watcher - monitors Obsidian vault for changes and triggers incremental indexing."""

import logging
import signal
import sys
import time
from pathlib import Path
from threading import Timer

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from .config import OBSIDIAN_VAULT_PATH
from .nas_sync import sync_to_nas

logger = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 10


class VaultHandler(FileSystemEventHandler):
    """Watches for .md file changes and batches them for indexing."""

    def __init__(self, vault_root: Path):
        self.vault_root = vault_root
        self.pending: set[str] = set()
        self.pending_deletes: set[str] = set()
        self.timer: Timer | None = None

    def _should_index(self, path: str) -> bool:
        """Filter to indexable markdown files only."""
        p = Path(path)
        if not p.suffix == ".md":
            return False
        if ".obsidian" in p.parts:
            return False
        if ".trash" in p.parts:
            return False
        return True

    def _schedule_flush(self):
        """Reset the debounce timer."""
        if self.timer:
            self.timer.cancel()
        self.timer = Timer(DEBOUNCE_SECONDS, self._flush)
        self.timer.daemon = True
        self.timer.start()

    def _flush(self):
        """Mirror all pending changed/deleted vault files to the NAS sync mount."""
        if not self.pending and not self.pending_deletes:
            return
        deletes = list(self.pending_deletes)
        self.pending_deletes.clear()
        batch = list(self.pending)
        self.pending.clear()
        sync_to_nas(batch, deletes)

    def _enqueue(self, path: str):
        """Add a file to the pending set and schedule a flush."""
        try:
            rel = str(Path(path).relative_to(self.vault_root))
        except ValueError:
            return
        self.pending.add(rel)
        count = len(self.pending)
        logger.debug("Queued: %s (%d pending)", rel, count)
        self._schedule_flush()

    def on_modified(self, event):
        if not event.is_directory and self._should_index(event.src_path):
            self._enqueue(event.src_path)

    def on_created(self, event):
        if not event.is_directory and self._should_index(event.src_path):
            self._enqueue(event.src_path)

    def on_moved(self, event):
        if event.is_directory:
            return
        # Stale chunks at the source path must be deleted, otherwise they
        # linger under the old file_path forever. If the source was queued
        # for (re-)index in this debounce window, drop it — the file is gone.
        if self._should_index(event.src_path):
            try:
                rel_src = str(Path(event.src_path).relative_to(self.vault_root))
                self.pending.discard(rel_src)
                self.pending_deletes.add(rel_src)
                self._schedule_flush()
            except ValueError:
                pass
        if self._should_index(event.dest_path):
            self._enqueue(event.dest_path)

    def on_deleted(self, event):
        if event.is_directory or not self._should_index(event.src_path):
            return
        try:
            rel = str(Path(event.src_path).relative_to(self.vault_root))
        except ValueError:
            return
        # A modify+delete within the same debounce window must not try to
        # index the now-missing path.
        self.pending.discard(rel)
        self.pending_deletes.add(rel)
        self._schedule_flush()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    # Flush stdout after each log line for launchd
    sys.stdout.reconfigure(line_buffering=True)

    vault_path = Path(OBSIDIAN_VAULT_PATH)
    if not vault_path.exists():
        logger.error("Vault not found: %s", vault_path)
        sys.exit(1)

    handler = VaultHandler(vault_path)
    observer = Observer()
    observer.schedule(handler, str(vault_path), recursive=True)
    observer.start()
    logger.info("Watching %s (debounce: %ds)", vault_path, DEBOUNCE_SECONDS)

    def shutdown(signum, frame):
        logger.info("Shutting down...")
        if handler.timer:
            handler.timer.cancel()
        if handler.pending or handler.pending_deletes:
            logger.info(
                "Flushing %d pending / %d deletes before exit",
                len(handler.pending),
                len(handler.pending_deletes),
            )
            handler._flush()
        observer.stop()
        observer.join()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown(None, None)


if __name__ == "__main__":
    main()
