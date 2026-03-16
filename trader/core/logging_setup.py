"""
Centralized logging configuration for the trading system.
"""
import logging
import logging.handlers
import sys


def setup_logging(log_file: str = "trader.log", level: str = "INFO"):
    """Configure rotating file + console logging."""
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(name)-24s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=20 * 1024 * 1024, backupCount=10, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(getattr(logging, level, logging.INFO))

    # Avoid duplicate handlers on re-init
    if not root.handlers:
        root.addHandler(file_handler)
        root.addHandler(stream_handler)
