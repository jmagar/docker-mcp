"""Logging configuration for Docker MCP server with dual output (console + files)."""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import structlog


def setup_logging(
    log_dir: Path | str = Path("logs"),
    log_level: str | None = None,
    max_file_size_mb: int = 10,
) -> None:
    """Setup dual logging system: console + files with automatic truncation.

    Creates two log files:
    - mcp_server.log: General server operations
    - middleware.log: Middleware request/response tracking

    Args:
        log_dir: Directory for log files
        log_level: Log level (defaults to LOG_LEVEL env var or INFO)
        max_file_size_mb: Max file size before truncation (no backup files kept)
    """
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    # Get log level from environment or parameter
    if log_level is None:
        log_level = os.getenv("LOG_LEVEL", "INFO")

    log_level_num = getattr(logging, log_level.upper(), logging.INFO)
    max_bytes = max_file_size_mb * 1024 * 1024

    # Clear any existing handlers to prevent duplicates
    logging.getLogger().handlers.clear()

    # Create formatters
    console_formatter = logging.Formatter(
        fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Server log handler (mcp_server.log)
    server_file_handler = RotatingFileHandler(
        log_dir / "mcp_server.log",
        maxBytes=max_bytes,
        backupCount=0,  # Don't keep old files, just truncate
        encoding="utf-8",
    )
    server_file_handler.setLevel(log_level_num)
    server_file_handler.setFormatter(console_formatter)

    # Middleware log handler (middleware.log)
    middleware_file_handler = RotatingFileHandler(
        log_dir / "middleware.log",
        maxBytes=max_bytes,
        backupCount=0,  # Don't keep old files, just truncate
        encoding="utf-8",
    )
    middleware_file_handler.setLevel(log_level_num)
    middleware_file_handler.setFormatter(console_formatter)

    # Console handler (for both server and middleware)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level_num)
    console_handler.setFormatter(console_formatter)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level_num)
    root_logger.addHandler(console_handler)

    # Configure server logger (writes to mcp_server.log + console)
    server_logger = logging.getLogger("server")
    server_logger.addHandler(server_file_handler)
    server_logger.propagate = True  # Also send to console via root logger

    # Configure middleware logger (writes to middleware.log + console)
    middleware_logger = logging.getLogger("middleware")
    middleware_logger.addHandler(middleware_file_handler)
    middleware_logger.propagate = True  # Also send to console via root logger

    # Configure structlog to use standard library logging
    from structlog.stdlib import BoundLogger, LoggerFactory, ProcessorFormatter

    renderer = (
        structlog.dev.ConsoleRenderer()
        if sys.stdout.isatty()
        else structlog.processors.JSONRenderer()
    )
    # Integrate with stdlib handlers so file and console both receive events
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=LoggerFactory(),
        wrapper_class=BoundLogger,
        cache_logger_on_first_use=True,
    )
    # Apply structlog formatting via ProcessorFormatter on each handler
    console_handler.setFormatter(ProcessorFormatter(processor=renderer))
    server_file_handler.setFormatter(
        ProcessorFormatter(processor=structlog.processors.JSONRenderer())
    )
    middleware_file_handler.setFormatter(
        ProcessorFormatter(processor=structlog.processors.JSONRenderer())
    )

    # Log initialization
    logger = structlog.get_logger("server")
    logger.info(
        "Logging system initialized",
        log_dir=str(log_dir.absolute()),
        log_level=log_level,
        max_file_size_mb=max_file_size_mb,
        server_log=str(log_dir / "mcp_server.log"),
        middleware_log=str(log_dir / "middleware.log"),
    )


def get_server_logger() -> Any:
    """Get logger for general server operations (writes to mcp_server.log)."""
    return structlog.get_logger("server")


def get_middleware_logger() -> Any:
    """Get logger for middleware operations (writes to middleware.log)."""
    return structlog.get_logger("middleware")


# Removed unused ensure_log_directory helper; server initializes log dir directly.
