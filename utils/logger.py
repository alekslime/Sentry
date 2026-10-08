"""Application-wide logging setup for Sentry.

Call `setup_logging()` exactly once, early in `main.py`, before any other
module logs anything. Every other module should just do:

    import logging
    logger = logging.getLogger(__name__)

and rely on the root configuration set up here.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from config.paths import LOG_DIR, ensure_app_directories
from config.schema import LoggingSettings

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(settings: LoggingSettings) -> None:
    """Configure the root logger according to `settings`.

    Idempotent: safe to call more than once (e.g. in tests) — existing
    handlers are cleared first to avoid duplicate log lines.
    """
    ensure_app_directories()

    root_logger = logging.getLogger()
    root_logger.setLevel(settings.level.upper())

    # Clear any pre-existing handlers (e.g. from a previous call or the
    # default handler libraries sometimes attach).
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    if settings.console:
        console_handler = logging.StreamHandler(stream=sys.stdout)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    if settings.file:
        log_path = LOG_DIR / "sentry.log"
        file_handler = RotatingFileHandler(
            log_path,
            maxBytes=settings.max_bytes,
            backupCount=settings.backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    logging.getLogger(__name__).debug("Logging initialized at level %s", settings.level)
