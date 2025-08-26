"""
Centralized logging configuration.
"""

import json
import logging
import logging.handlers
import sys
from datetime import datetime
from typing import Optional


class JSONFormatter(logging.Formatter):
    """JSON formatter for structured logging."""

    def format(self, record):
        """Format log record as JSON."""
        log_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Add extra fields if present
        if hasattr(record, "extra_fields"):
            log_entry.update(record.extra_fields)

        return json.dumps(log_entry)


class ColorFormatter(logging.Formatter):
    """Colored formatter for console output."""

    # Color codes
    COLORS = {
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    RESET = "\033[0m"

    def format(self, record):
        """Format log record with colors."""
        # Add color to level name
        level_name = record.levelname
        if level_name in self.COLORS:
            colored_level = f"{self.COLORS[level_name]}{level_name}{self.RESET}"
            record.levelname = colored_level

        # Format the record
        formatted = super().format(record)

        # Reset level name for other formatters
        record.levelname = level_name

        return formatted


def setup_logging(
    level: str = "INFO",
    format_type: str = "text",
    log_file: Optional[str] = None,
    max_file_size: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5,
):
    """
    Setup centralized logging configuration.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        format_type: Format type ('text' or 'json')
        log_file: Optional log file path
        max_file_size: Maximum log file size in bytes before rotation
        backup_count: Number of backup files to keep
    """

    # Convert level string to logging constant
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    # Remove any existing handlers
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Create formatters
    if format_type.lower() == "json":
        formatter: logging.Formatter = JSONFormatter()
        console_formatter: logging.Formatter = (
            JSONFormatter()
        )  # JSON for console too in production
    else:
        # Text format with colors for console
        text_format = "%(asctime)s - %(levelname)s - %(message)s"
        formatter = logging.Formatter(text_format)
        console_formatter = ColorFormatter(text_format)

    # Console handler (always enabled)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(console_formatter)

    # Add console handler
    root_logger.addHandler(console_handler)
    root_logger.setLevel(numeric_level)

    # File handler (optional)
    if log_file:
        try:
            # Create rotating file handler
            file_handler = logging.handlers.RotatingFileHandler(
                log_file, maxBytes=max_file_size, backupCount=backup_count
            )
            file_handler.setLevel(numeric_level)
            file_handler.setFormatter(formatter)

            root_logger.addHandler(file_handler)

            # Log that file logging is enabled
            logger = logging.getLogger(__name__)
            logger.info(f"File logging enabled: {log_file}")

        except Exception as e:
            # Don't fail if file logging can't be setup
            logger = logging.getLogger(__name__)
            logger.warning(f"Could not setup file logging: {e}")

    # Suppress noisy loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    # Log setup completion
    logger = logging.getLogger(__name__)
    logger.info(f"Logging initialized: level={level}, format={format_type}")


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger with the specified name.

    Args:
        name: Logger name

    Returns:
        Logger instance
    """
    return logging.getLogger(name)


class ContextLogger:
    """Logger wrapper that adds context fields to log records."""

    def __init__(self, logger: logging.Logger, context: dict):
        """
        Initialize context logger.

        Args:
            logger: Base logger instance
            context: Context fields to add to all log records
        """
        self.logger = logger
        self.context = context

    def _log_with_context(self, level, msg, *args, **kwargs):
        """Log message with context fields."""
        extra = kwargs.get("extra", {})
        extra["extra_fields"] = {**self.context, **extra.get("extra_fields", {})}
        kwargs["extra"] = extra

        getattr(self.logger, level)(msg, *args, **kwargs)

    def debug(self, msg, *args, **kwargs):
        self._log_with_context("debug", msg, *args, **kwargs)

    def info(self, msg, *args, **kwargs):
        self._log_with_context("info", msg, *args, **kwargs)

    def warning(self, msg, *args, **kwargs):
        self._log_with_context("warning", msg, *args, **kwargs)

    def error(self, msg, *args, **kwargs):
        self._log_with_context("error", msg, *args, **kwargs)

    def critical(self, msg, *args, **kwargs):
        self._log_with_context("critical", msg, *args, **kwargs)


def create_context_logger(name: str, **context) -> ContextLogger:
    """
    Create a logger with context fields.

    Args:
        name: Logger name
        **context: Context fields to add to all log records

    Returns:
        ContextLogger instance
    """
    base_logger = logging.getLogger(name)
    return ContextLogger(base_logger, context)
