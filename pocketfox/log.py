"""Logging configuration for pocketfox."""

from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

# Format without source location (cleaner for production)
_SIMPLE_FORMAT = (
    "<level>{level: <8}</level> | <cyan>{time:YYYY-MM-DD HH:mm:ss.SSS}</cyan> | {message}"
)

# Format with source location (useful for debugging)
_VERBOSE_FORMAT = (
    "<level>{level: <8}</level> | "
    "<cyan>{time:YYYY-MM-DD HH:mm:ss.SSS}</cyan> | "
    "<dim>{name}:{function}:{line}</dim> | "
    "{message}"
)

# Plain format for log files (no ANSI colour tags)
_FILE_FORMAT = "{level: <8} | {time:YYYY-MM-DD HH:mm:ss.SSS} | {name}:{function}:{line} | {message}"


def configure_logging(
    verbose: bool = False,
    log_file: Path | None = None,
) -> None:
    """
    Configure loguru with pocketfox's preferred format.

    Args:
        verbose: If True, show DEBUG level and include source location.
        log_file: If set, log to this file instead of stderr (useful for
            interactive CLI mode where stderr output pollutes the REPL).
    """
    logger.remove()

    level = "DEBUG" if verbose else "INFO"

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            log_file,
            format=_FILE_FORMAT,
            level=level,
            rotation="10 MB",
            retention=3,
        )
    else:
        fmt = _VERBOSE_FORMAT if verbose else _SIMPLE_FORMAT
        logger.add(
            sys.stderr,
            format=fmt,
            level=level,
            colorize=True,
        )
