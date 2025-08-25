"""Logging configuration for Docker MCP server with dual output (console + files)."""

import logging
import os
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler
from typing import Any

import structlog


def setup_logging(
    log_dir: Path | str = Path("logs"),
    log_level: str | None = None,
    max_file_size_mb: int = 10,
) -> None:
    """
    Configure logging for the MCP server: console output plus two rotating file logs (mcp_server.log and middleware.log) with truncation.
    
    This initializes a dual-output logging system and integrates structlog with the standard library loggers. Side effects:
    - Ensures the provided log directory exists (creates parent directories as needed).
    - Clears existing root logger handlers to avoid duplicate output.
    - Creates two rotating file handlers (no backups; files are truncated when max size is reached) and a console StreamHandler.
    - Configures separate loggers named "server" and "middleware" that write to their respective files and propagate to the console.
    - Chooses a structlog renderer: a human-friendly console renderer when stdout is a TTY, otherwise JSON; applies structlog ProcessorFormatter to handlers.
    - Emits an initialization event to the "server" logger containing the log paths and configuration.
    
    Parameters:
        log_dir (Path | str): Directory to place log files (created if missing).
        log_level (str | None): Log level name (e.g., "INFO", "DEBUG"). If None, reads LOG_LEVEL from the environment or defaults to "INFO".
        max_file_size_mb (int): Maximum size in megabytes for each rotating log file before truncation (backupCount is 0).
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
        fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    # Server log handler (mcp_server.log)
    server_file_handler = RotatingFileHandler(
        log_dir / "mcp_server.log",
        maxBytes=max_bytes,
        backupCount=0,  # Don't keep old files, just truncate
        encoding="utf-8"
    )
    server_file_handler.setLevel(log_level_num)
    server_file_handler.setFormatter(console_formatter)
    
    # Middleware log handler (middleware.log)
    middleware_file_handler = RotatingFileHandler(
        log_dir / "middleware.log",
        maxBytes=max_bytes,
        backupCount=0,  # Don't keep old files, just truncate
        encoding="utf-8"
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
    from structlog.stdlib import LoggerFactory, BoundLogger, ProcessorFormatter
    renderer = structlog.dev.ConsoleRenderer() if sys.stdout.isatty() else structlog.processors.JSONRenderer()
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
    server_file_handler.setFormatter(ProcessorFormatter(processor=structlog.processors.JSONRenderer()))
    middleware_file_handler.setFormatter(ProcessorFormatter(processor=structlog.processors.JSONRenderer()))
    
    # Log initialization
    logger = structlog.get_logger("server")
    logger.info(
        "Logging system initialized",
        log_dir=str(log_dir.absolute()),
        log_level=log_level,
        max_file_size_mb=max_file_size_mb,
        server_log=str(log_dir / "mcp_server.log"),
        middleware_log=str(log_dir / "middleware.log")
    )


def get_server_logger() -> Any:
    """Get logger for general server operations (writes to mcp_server.log)."""
    return structlog.get_logger("server")


def get_middleware_logger() -> Any:
    """Get logger for middleware operations (writes to middleware.log)."""
    return structlog.get_logger("middleware")


def ensure_log_directory(log_dir: Path | str = Path("logs")) -> Path:
    """
    Ensure the given log directory exists and return it as a Path.
    
    If the directory (or any of its parent directories) does not exist, it is created with parents=True and exist_ok=True. Accepts a Path or string; defaults to "logs".
    
    Returns:
        Path: The resolved Path object for the ensured log directory.
    """
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir