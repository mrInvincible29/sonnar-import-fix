"""
Secure configuration loader with environment variable override support.
"""

import os
import secrets
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from dotenv import load_dotenv


class ConfigurationError(Exception):
    """Raised when configuration is invalid or missing required values."""

    pass


class ConfigLoader:
    """
    Secure configuration loader that:
    1. Loads base config from YAML file
    2. Overrides with environment variables
    3. Validates required settings
    4. Masks sensitive values for logging
    5. Auto-generates webhook secret if not provided
    """

    # Sensitive keys that should be masked in logs
    SENSITIVE_KEYS = ["api_key", "webhook_secret", "password", "token", "key", "secret"]

    # Environment variable mappings to config paths
    ENV_MAPPINGS = {
        "SONARR_URL": "sonarr.url",
        "SONARR_API_KEY": "sonarr.api_key",
        "WEBHOOK_ENABLED": "webhook.enabled",
        "WEBHOOK_HOST": "webhook.host",
        "WEBHOOK_PORT": "webhook.port",
        "WEBHOOK_SECRET": "webhook.secret",
        "WEBHOOK_IMPORT_CHECK_DELAY": "webhook.import_check_delay",
        "MONITORING_INTERVAL": "monitoring.interval",
        "MONITORING_STUCK_THRESHOLD": "monitoring.stuck_threshold",
        "FORCE_IMPORT_THRESHOLD": "decisions.force_import_threshold",
        "REMOVE_PUBLIC_FAILURES": "decisions.remove_public_failures",
        "PROTECT_PRIVATE_RATIO": "decisions.protect_private_ratio",
        "LOG_LEVEL": "logging.level",
        "LOG_FORMAT": "logging.format",
    }

    def __init__(
        self, config_path: Optional[str] = None, env_file: Optional[str] = None
    ):
        """
        Initialize configuration loader.

        Args:
            config_path: Path to YAML configuration file
            env_file: Path to .env file (defaults to .env in current directory)
        """
        # Load .env file if it exists
        if env_file:
            load_dotenv(env_file)
        else:
            load_dotenv()  # Load .env from current directory if exists

        # Load base configuration
        self.config = self._load_base_config(config_path)

        # Override with environment variables
        self._override_with_env()

        # Auto-generate webhook secret if not provided
        self._ensure_webhook_secret()

        # Validate configuration
        self._validate_config()

        # Create masked version for logging
        self.masked_config = self._create_masked_config()

    def _load_base_config(self, config_path: Optional[str]) -> Dict[str, Any]:
        """Load base configuration from YAML file."""
        if not config_path:
            # Try default locations
            for path in ["config.yaml", "config/config.yaml"]:
                if Path(path).exists():
                    config_path = path
                    break

        if config_path and Path(config_path).exists():
            with open(config_path, "r") as f:
                config = yaml.safe_load(f) or {}
            return config

        # Return empty config if no file found
        return {}

    def _override_with_env(self):
        """Override configuration values with environment variables."""
        for env_key, config_path in self.ENV_MAPPINGS.items():
            value = os.getenv(env_key)
            if value is not None:
                # Convert string values to appropriate types
                value = self._convert_env_value(value)
                self._set_nested_value(config_path, value)

    def _convert_env_value(self, value: str) -> Any:
        """Convert environment variable string to appropriate type."""
        # Handle boolean values
        if value.lower() in ("true", "yes", "1", "on"):
            return True
        elif value.lower() in ("false", "no", "0", "off"):
            return False

        # Handle numeric values
        if value.isdigit():
            return int(value)

        try:
            return float(value)
        except ValueError:
            pass

        # Return as string
        return value

    def _ensure_webhook_secret(self):
        """Auto-generate webhook secret if not provided."""
        webhook_secret = self.get("webhook.secret")
        if not webhook_secret:
            # Generate a secure random secret
            generated_secret = secrets.token_urlsafe(32)
            self._set_nested_value("webhook.secret", generated_secret)
            print(f"Auto-generated webhook secret: {generated_secret}")
            print("Save this secret for configuring Sonarr webhook!")

    def _validate_config(self):
        """Validate required configuration values."""
        required_settings = ["sonarr.url", "sonarr.api_key"]

        missing = []
        for setting in required_settings:
            value = self.get(setting)
            if not value:
                missing.append(setting)

        if missing:
            raise ConfigurationError(
                f"Required configuration missing: {', '.join(missing)}. "
                f"Set these in config.yaml or environment variables."
            )

        # Validate API key is not a placeholder
        api_key = self.get("sonarr.api_key")
        if api_key in ["your-api-key", "your-api-key-here", "changeme"]:
            raise ConfigurationError(
                "SONARR_API_KEY appears to be a placeholder. "
                "Please set your actual Sonarr API key."
            )

        # Validate URL format
        url = self.get("sonarr.url")
        if not url.startswith(("http://", "https://")):
            raise ConfigurationError(
                f"SONARR_URL must start with http:// or https://, got: {url}"
            )

    def _create_masked_config(self) -> Dict[str, Any]:
        """Create a masked version of config for safe logging."""
        return self._mask_sensitive_recursive(self.config.copy())

    def _mask_sensitive_recursive(self, obj: Any) -> Any:
        """Recursively mask sensitive values."""
        if isinstance(obj, dict):
            result = {}
            for key, value in obj.items():
                if any(sensitive in key.lower() for sensitive in self.SENSITIVE_KEYS):
                    result[key] = "***MASKED***" if value else None
                else:
                    result[key] = self._mask_sensitive_recursive(value)
            return result
        elif isinstance(obj, list):
            return [self._mask_sensitive_recursive(item) for item in obj]
        else:
            return obj

    def get(self, key_path: str, default: Any = None) -> Any:
        """
        Get configuration value using dot notation.

        Args:
            key_path: Dot-separated path (e.g., 'sonarr.url')
            default: Default value if key not found
        """
        return self._get_nested_value(key_path, default)

    def _get_nested_value(self, key_path: str, default: Any = None) -> Any:
        """Get nested dictionary value using dot notation."""
        keys = key_path.split(".")
        value = self.config

        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default

        return value

    def _set_nested_value(self, key_path: str, value: Any):
        """Set nested dictionary value using dot notation."""
        keys = key_path.split(".")
        current = self.config

        # Navigate to the parent of the target key
        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]

        # Set the final value
        current[keys[-1]] = value

    def get_masked_config_for_logging(self) -> Dict[str, Any]:
        """Get configuration with sensitive values masked for safe logging."""
        return self.masked_config

    def validate_sonarr_connection(self) -> bool:
        """Validate that Sonarr connection settings are accessible."""
        # This method can be implemented later for connection testing
        return True


# Default configuration values
DEFAULT_CONFIG = {
    "sonarr": {
        "url": None,  # Must be provided
        "api_key": None,  # Must be provided
        "timeout": 30,
    },
    "monitoring": {
        "interval": 60,
        "stuck_threshold": 300,
        "score_tolerance": 50,
        "detect_repeated_grabs": True,
    },
    "webhook": {
        "enabled": True,
        "host": "0.0.0.0",
        "port": 8090,
        "secret": None,  # Auto-generated if not provided
        "import_check_delay": 600,
    },
    "trackers": {
        "private": [
            "beyondhd",
            "bhd",
            "privatehd",
            "passthepopcorn",
            "ptp",
            "broadcasthenet",
            "btn",
            "redacted",
            "orpheus",
        ],
        "public": [
            "nyaa",
            "animetosho",
            "rarbg",
            "1337x",
            "thepiratebay",
            "yts",
            "eztv",
            "torrentgalaxy",
        ],
    },
    "decisions": {
        "force_import_threshold": 10,
        "remove_public_failures": True,
        "protect_private_ratio": True,
    },
    "logging": {"level": "INFO", "format": "text"},  # 'text' or 'json'
}
