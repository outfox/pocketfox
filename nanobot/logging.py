"""Logging configuration for pocketfox."""

import sys

from loguru import logger


def configure_logging(verbose: bool = False) -> None:
    """
    Configure loguru with pocketfox's preferred format.
    
    Args:
        verbose: If True, show DEBUG level and include source location.
    """
    # Remove default handler
    logger.remove()
    
    # Format without source location (cleaner for production)
    # The source location (module:function:line) is noise for most log messages
    simple_format = (
        "<level>{level: <8}</level> | "
        "<cyan>{time:YYYY-MM-DD HH:mm:ss.SSS}</cyan> | "
        "{message}"
    )
    
    # Format with source location (useful for debugging)
    verbose_format = (
        "<level>{level: <8}</level> | "
        "<cyan>{time:YYYY-MM-DD HH:mm:ss.SSS}</cyan> | "
        "<dim>{name}:{function}:{line}</dim> | "
        "{message}"
    )
    
    level = "DEBUG" if verbose else "INFO"
    fmt = verbose_format if verbose else simple_format
    
    logger.add(
        sys.stderr,
        format=fmt,
        level=level,
        colorize=True,
    )
