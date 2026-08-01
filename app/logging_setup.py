from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler


def configure_logging(log_path: str) -> None:
    """stdout stays primary (what watcher captures); mirrored to a capped,
    rotating file for local inspection."""
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(log_path, maxBytes=5_000_000, backupCount=3)
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(stdout_handler)
    root.addHandler(file_handler)
