"""
Configuration validation utilities.
"""

import re
from typing import Any, Dict, List
from urllib.parse import urlparse


class ValidationError(Exception):
    """Raised when configuration validation fails."""

    pass


class ConfigValidator:
    """Validates configuration values and structure."""

    @staticmethod
    def validate_url(url: str, schemes: List[str] = None) -> bool:
        """
        Validate URL format.

        Args:
            url: URL to validate
            schemes: Allowed schemes (defaults to ['http', 'https'])
        """
        if schemes is None:
            schemes = ["http", "https"]

        try:
            parsed = urlparse(url)
            if parsed.scheme not in schemes:
                raise ValidationError(
                    f"URL scheme must be one of {schemes}, got: {parsed.scheme}"
                )

            if not parsed.netloc:
                raise ValidationError("URL must include hostname")

            return True
        except Exception as e:
            raise ValidationError(f"Invalid URL format: {e}")

    @staticmethod
    def validate_api_key(api_key: str) -> bool:
        """
        Validate API key format.

        Args:
            api_key: API key to validate
        """
        if not api_key:
            raise ValidationError("API key cannot be empty")

        if len(api_key) < 10:
            raise ValidationError("API key appears too short")

        # Check for placeholder values
        placeholders = [
            "your-api-key",
            "your-api-key-here",
            "changeme",
            "replace-me",
            "example",
            "test",
            "placeholder",
        ]

        if api_key.lower() in placeholders:
            raise ValidationError("API key appears to be a placeholder value")

        # Sonarr API keys are typically 32-character hex strings
        if len(api_key) == 32 and re.match(r"^[a-fA-F0-9]{32}$", api_key):
            return True

        # Allow other formats but warn
        print(f"Warning: API key format doesn't match typical Sonarr pattern")
        return True

    @staticmethod
    def validate_port(port: Any) -> bool:
        """
        Validate port number.

        Args:
            port: Port number to validate
        """
        try:
            port_int = int(port)
            if not (1 <= port_int <= 65535):
                raise ValidationError(f"Port must be between 1-65535, got: {port_int}")
            return True
        except (ValueError, TypeError):
            raise ValidationError(f"Port must be a valid integer, got: {port}")

    @staticmethod
    def validate_threshold(
        threshold: Any, min_val: int = 0, max_val: int = 1000
    ) -> bool:
        """
        Validate threshold values.

        Args:
            threshold: Threshold value to validate
            min_val: Minimum allowed value
            max_val: Maximum allowed value
        """
        try:
            threshold_int = int(threshold)
            if not (min_val <= threshold_int <= max_val):
                raise ValidationError(
                    f"Threshold must be between {min_val}-{max_val}, got: {threshold_int}"
                )
            return True
        except (ValueError, TypeError):
            raise ValidationError(
                f"Threshold must be a valid integer, got: {threshold}"
            )

    @staticmethod
    def validate_interval(interval: Any, min_val: int = 10) -> bool:
        """
        Validate interval values.

        Args:
            interval: Interval value to validate (in seconds)
            min_val: Minimum allowed value in seconds
        """
        try:
            interval_int = int(interval)
            if interval_int < min_val:
                raise ValidationError(
                    f"Interval must be at least {min_val} seconds, got: {interval_int}"
                )
            return True
        except (ValueError, TypeError):
            raise ValidationError(f"Interval must be a valid integer, got: {interval}")

    @staticmethod
    def validate_tracker_lists(trackers_config: Dict[str, List[str]]) -> bool:
        """
        Validate tracker configuration.

        Args:
            trackers_config: Tracker configuration dictionary
        """
        if not isinstance(trackers_config, dict):
            raise ValidationError("Trackers config must be a dictionary")

        required_keys = ["private", "public"]
        for key in required_keys:
            if key not in trackers_config:
                raise ValidationError(f"Missing required tracker list: {key}")

            if not isinstance(trackers_config[key], list):
                raise ValidationError(f"Tracker list '{key}' must be a list")

            if not trackers_config[key]:
                print(f"Warning: {key} tracker list is empty")

        return True

    @staticmethod
    def validate_log_level(level: str) -> bool:
        """
        Validate log level.

        Args:
            level: Log level string
        """
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

        if level.upper() not in valid_levels:
            raise ValidationError(
                f"Log level must be one of {valid_levels}, got: {level}"
            )

        return True

    @staticmethod
    def validate_log_format(format_type: str) -> bool:
        """
        Validate log format.

        Args:
            format_type: Log format type
        """
        valid_formats = ["text", "json"]

        if format_type.lower() not in valid_formats:
            raise ValidationError(
                f"Log format must be one of {valid_formats}, got: {format_type}"
            )

        return True
