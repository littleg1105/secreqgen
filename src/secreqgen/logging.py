"""Logging configuration for SecReqGen using Rich."""

import logging
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.logging import RichHandler

# Global console instance
console = Console()


def setup_logging(
    level: str = "INFO",
    log_file: Optional[Path] = None,
) -> logging.Logger:
    """Configure logging with Rich console output and optional file logging.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Optional path to log file

    Returns:
        Configured logger instance
    """
    # Create logger
    logger = logging.getLogger("secreqgen")
    logger.setLevel(getattr(logging, level.upper()))

    # Clear existing handlers
    logger.handlers.clear()

    # Rich console handler
    console_handler = RichHandler(
        console=console,
        show_time=True,
        show_path=False,
        rich_tracebacks=True,
        tracebacks_show_locals=True,
    )
    console_handler.setLevel(getattr(logging, level.upper()))
    console_format = logging.Formatter("%(message)s")
    console_handler.setFormatter(console_format)
    logger.addHandler(console_handler)

    # Optional file handler
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)
        file_format = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        file_handler.setFormatter(file_format)
        logger.addHandler(file_handler)

    return logger


def get_logger(name: str = "secreqgen") -> logging.Logger:
    """Get a logger instance.

    Args:
        name: Logger name (will be prefixed with 'secreqgen.')

    Returns:
        Logger instance
    """
    if name == "secreqgen":
        return logging.getLogger("secreqgen")
    return logging.getLogger(f"secreqgen.{name}")
