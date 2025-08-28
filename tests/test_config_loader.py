"""
Unit tests for ConfigLoader and configuration management.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest
import yaml

from src.config.loader import DEFAULT_CONFIG, ConfigLoader, ConfigurationError


class TestConfigLoader:
    """Test ConfigLoader class"""

    def test_init_with_defaults(self):
        """Test initialization with default empty config"""
        with patch("src.config.loader.load_dotenv"):
            with patch.object(Path, "exists", return_value=False):
                with patch.dict("os.environ", {}, clear=True):
                    with pytest.raises(ConfigurationError):
                        # Should fail due to missing required config
                        ConfigLoader()

    def test_init_with_config_file(self, temp_config_file):
        """Test initialization with config file"""
        with patch("src.config.loader.load_dotenv"):
            with patch.dict("os.environ", {}, clear=True):
                config = ConfigLoader(config_path=temp_config_file)

                assert config.get("sonarr.url") == "http://test-sonarr:8989"
                assert config.get("webhook.port") == 8090

    def test_init_finds_default_config_yaml(self):
        """Test that it finds config.yaml in current directory"""
        mock_config = {"sonarr": {"url": "http://test:8989", "api_key": "test-key"}}

        with patch("src.config.loader.load_dotenv"):
            with patch.dict("os.environ", {}, clear=True):
                with patch("src.config.loader.Path") as mock_path_class:
                    mock_path_instance = MagicMock()
                    mock_path_class.return_value = mock_path_instance
                    mock_path_instance.exists.side_effect = (
                        lambda: True
                    )  # config.yaml exists

                    with patch(
                        "builtins.open", mock_open(read_data=yaml.dump(mock_config))
                    ):
                        config = ConfigLoader()
                        assert config.get("sonarr.url") == "http://test:8989"

    def test_init_finds_config_in_config_dir(self):
        """Test that it finds config.yaml in config/ directory"""
        mock_config = {
            "sonarr": {"url": "http://config-dir:8989", "api_key": "test-key"}
        }

        with patch("src.config.loader.load_dotenv"):
            with patch.dict("os.environ", {}, clear=True):
                with patch("src.config.loader.Path") as mock_path_class:

                    def mock_path_exists(path_str):
                        path_instance = MagicMock()
                        # Return True for config/config.yaml, False for others
                        path_instance.exists.return_value = (
                            str(path_str) == "config/config.yaml"
                        )
                        return path_instance

                    mock_path_class.side_effect = mock_path_exists

                    with patch(
                        "builtins.open", mock_open(read_data=yaml.dump(mock_config))
                    ):
                        config = ConfigLoader()
                        assert config.get("sonarr.url") == "http://config-dir:8989"


class TestEnvironmentOverrides:
    """Test environment variable override functionality"""

    def test_env_override_string_value(self, test_env_vars):
        """Test environment variable override for string values"""
        with patch("src.config.loader.load_dotenv"):
            with patch.object(Path, "exists", return_value=False):
                config = ConfigLoader()

                assert config.get("sonarr.url") == "http://test-sonarr:8989"
                assert (
                    config.get("sonarr.api_key")
                    == "test-key-123456789012345678901234567890"
                )

    def test_env_override_boolean_values(self):
        """Test environment variable conversion to boolean"""
        test_cases = [
            ("true", True),
            ("True", True),
            ("yes", True),
            ("1", True),
            ("on", True),
            ("false", False),
            ("False", False),
            ("no", False),
            ("0", False),
            ("off", False),
        ]

        for env_value, expected in test_cases:
            with patch.dict(
                os.environ,
                {
                    "WEBHOOK_ENABLED": env_value,
                    "SONARR_URL": "http://test-sonarr:8989",
                    "SONARR_API_KEY": "test-key-for-github-actions-123456789",
                },
            ):
                with patch("src.config.loader.load_dotenv"):
                    with patch.object(Path, "exists", return_value=False):
                        config = ConfigLoader()
                        assert config.get("webhook.enabled") == expected

    def test_env_override_numeric_values(self):
        """Test environment variable conversion to numeric types"""
        with patch.dict(
            os.environ,
            {
                "WEBHOOK_PORT": "9090",
                "FORCE_IMPORT_THRESHOLD": "15",
                "SONARR_URL": "http://test-sonarr:8989",
                "SONARR_API_KEY": "test-key-for-github-actions-123456789",
            },
        ):
            with patch("src.config.loader.load_dotenv"):
                with patch.object(Path, "exists", return_value=False):
                    config = ConfigLoader()

                    assert config.get("webhook.port") == 9090
                    assert config.get("decisions.force_import_threshold") == 15

    def test_env_override_float_values(self):
        """Test environment variable conversion to float"""
        with patch.dict(
            os.environ,
            {
                "MONITORING_INTERVAL": "60.5",
                "SONARR_URL": "http://test-sonarr:8989",
                "SONARR_API_KEY": "test-key-for-github-actions-123456789",
            },
        ):
            with patch("src.config.loader.load_dotenv"):
                with patch.object(Path, "exists", return_value=False):
                    config = ConfigLoader()

                    assert config.get("monitoring.interval") == 60.5


class TestWebhookSecretGeneration:
    """Test webhook secret auto-generation"""

    @patch("src.config.loader.secrets.token_urlsafe")
    @patch("builtins.print")
    def test_auto_generate_webhook_secret(self, mock_print, mock_token):
        """Test that webhook secret is auto-generated when missing"""
        mock_token.return_value = "generated-secret-123"

        # Ensure WEBHOOK_SECRET env var is not set for this test
        test_env = {
            "SONARR_URL": "http://test:8989",
            "SONARR_API_KEY": "test123456789012345678901234567890",
            # Intentionally omit WEBHOOK_SECRET to test auto-generation
        }

        with patch("src.config.loader.load_dotenv"):
            with patch.object(Path, "exists", return_value=False):
                with patch.dict("os.environ", test_env, clear=True):
                    config = ConfigLoader()

                    assert config.get("webhook.secret") == "generated-secret-123"
                    mock_token.assert_called_once_with(32)
                    mock_print.assert_called()

    def test_preserve_existing_webhook_secret(self, test_env_vars):
        """Test that existing webhook secret is preserved"""
        with patch("src.config.loader.load_dotenv"):
            with patch.object(Path, "exists", return_value=False):
                config = ConfigLoader()

                # Should not auto-generate since it's provided
                assert config.get("webhook.secret") == test_env_vars["WEBHOOK_SECRET"]


class TestConfigValidation:
    """Test configuration validation"""

    def test_validation_success(self, test_env_vars):
        """Test successful validation with all required fields"""
        with patch("src.config.loader.load_dotenv"):
            with patch.object(Path, "exists", return_value=False):
                config = ConfigLoader()  # Should not raise
                assert config.get("sonarr.url") is not None

    def test_validation_missing_url(self):
        """Test validation failure when URL is missing"""
        with patch.dict(os.environ, {"SONARR_API_KEY": "test-key"}, clear=True):
            with patch("src.config.loader.load_dotenv"):
                with patch.object(Path, "exists", return_value=False):
                    with pytest.raises(ConfigurationError) as exc_info:
                        ConfigLoader()

                    assert "sonarr.url" in str(exc_info.value)

    def test_validation_missing_api_key(self):
        """Test validation failure when API key is missing"""
        with patch.dict(os.environ, {"SONARR_URL": "http://test:8989"}, clear=True):
            with patch("src.config.loader.load_dotenv"):
                with patch.object(Path, "exists", return_value=False):
                    with pytest.raises(ConfigurationError) as exc_info:
                        ConfigLoader()

                    assert "sonarr.api_key" in str(exc_info.value)

    def test_validation_placeholder_api_key(self):
        """Test validation failure for placeholder API key"""
        placeholders = ["your-api-key", "your-api-key-here", "changeme"]

        for placeholder in placeholders:
            with patch.dict(
                os.environ,
                {"SONARR_URL": "http://test:8989", "SONARR_API_KEY": placeholder},
                clear=True,
            ):
                with patch("src.config.loader.load_dotenv"):
                    with patch.object(Path, "exists", return_value=False):
                        with pytest.raises(ConfigurationError) as exc_info:
                            ConfigLoader()

                        assert "placeholder" in str(exc_info.value)

    def test_validation_invalid_url_format(self):
        """Test validation failure for invalid URL format"""
        with patch.dict(
            os.environ,
            {"SONARR_URL": "invalid-url", "SONARR_API_KEY": "valid-key-123"},
            clear=True,
        ):
            with patch("src.config.loader.load_dotenv"):
                with patch.object(Path, "exists", return_value=False):
                    with pytest.raises(ConfigurationError) as exc_info:
                        ConfigLoader()

                    assert "http://" in str(exc_info.value)


class TestSensitiveValueMasking:
    """Test sensitive value masking functionality"""

    def test_mask_api_key(self):
        """Test that API key is masked in logging config"""
        test_env = {
            "SONARR_URL": "http://test:8989",
            "SONARR_API_KEY": "secret-api-key-123",
        }
        with patch.dict("os.environ", test_env, clear=True):
            with patch("src.config.loader.load_dotenv"):
                with patch.object(Path, "exists", return_value=False):
                    with patch(
                        "src.config.loader.secrets.token_urlsafe",
                        return_value="mock-webhook-secret",
                    ):
                        config = ConfigLoader()

                        masked_config = config.get_masked_config_for_logging()
                        assert masked_config["sonarr"]["api_key"] == "***MASKED***"
                        assert masked_config["webhook"]["secret"] == "***MASKED***"

    def test_mask_nested_sensitive_values(self):
        """Test masking of nested sensitive values"""
        test_config = {
            "database": {"password": "secret123", "user": "myuser"},
            "api": {"token": "abc123", "endpoint": "http://api.test.com"},
        }

        test_env = {"SONARR_URL": "http://test:8989", "SONARR_API_KEY": "test123"}
        with patch.dict("os.environ", test_env, clear=True):
            with patch("src.config.loader.load_dotenv"):
                with patch.object(Path, "exists", return_value=False):
                    config = ConfigLoader()
                    config.config = test_config

                    masked = config._mask_sensitive_recursive(test_config)

                    assert masked["database"]["password"] == "***MASKED***"
                    assert masked["database"]["user"] == "myuser"  # Not sensitive
                    assert masked["api"]["token"] == "***MASKED***"
                    assert (
                        masked["api"]["endpoint"] == "http://api.test.com"
                    )  # Not sensitive

    def test_mask_list_values(self):
        """Test masking works with list values"""
        test_config = {
            "config": {
                "api_keys": ["key1", "key2"],
                "endpoints": ["http://test1.com", "http://test2.com"],
            }
        }

        test_env = {"SONARR_URL": "http://test:8989", "SONARR_API_KEY": "test123"}
        with patch.dict("os.environ", test_env, clear=True):
            with patch("src.config.loader.load_dotenv"):
                with patch.object(Path, "exists", return_value=False):
                    config = ConfigLoader()
                    config.config = test_config

                    masked = config._mask_sensitive_recursive(test_config)

                    assert masked["config"]["api_keys"] == "***MASKED***"
                    assert masked["config"]["endpoints"] == [
                        "http://test1.com",
                        "http://test2.com",
                    ]


class TestNestedValueOperations:
    """Test nested value get/set operations"""

    def test_get_nested_value_found(self):
        """Test getting nested value that exists"""
        test_env = {"SONARR_URL": "http://test:8989", "SONARR_API_KEY": "test123"}
        with patch.dict("os.environ", test_env, clear=True):
            with patch("src.config.loader.load_dotenv"):
                with patch.object(Path, "exists", return_value=False):
                    config = ConfigLoader()
                    config.config = {"level1": {"level2": {"value": "found"}}}

                    result = config.get("level1.level2.value")
                    assert result == "found"

    def test_get_nested_value_not_found(self):
        """Test getting nested value that doesn't exist"""
        test_env = {"SONARR_URL": "http://test:8989", "SONARR_API_KEY": "test123"}
        with patch.dict("os.environ", test_env, clear=True):
            with patch("src.config.loader.load_dotenv"):
                with patch.object(Path, "exists", return_value=False):
                    config = ConfigLoader()
                    config.config = {"level1": {}}

                    result = config.get("level1.level2.value", "default")
                    assert result == "default"

    def test_get_nested_value_partial_path(self):
        """Test getting value when path partially exists"""
        with patch.dict(
            os.environ,
            {
                "SONARR_URL": "http://test-sonarr:8989",
                "SONARR_API_KEY": "test-key-for-github-actions-123456789",
            },
        ):
            with patch("src.config.loader.load_dotenv"):
                with patch.object(Path, "exists", return_value=False):
                    config = ConfigLoader()
                config.config = {"level1": "not_dict"}

                result = config.get("level1.level2.value", "default")
                assert result == "default"

    def test_set_nested_value_new_path(self):
        """Test setting nested value with new path"""
        with patch.dict(
            os.environ,
            {
                "SONARR_URL": "http://test-sonarr:8989",
                "SONARR_API_KEY": "test-key-for-github-actions-123456789",
            },
        ):
            with patch("src.config.loader.load_dotenv"):
                with patch.object(Path, "exists", return_value=False):
                    config = ConfigLoader()
                config.config = {}

                config._set_nested_value("new.nested.value", "test")

                assert config.config["new"]["nested"]["value"] == "test"

    def test_set_nested_value_existing_path(self):
        """Test setting nested value in existing path"""
        with patch("src.config.loader.load_dotenv"):
            with patch.object(Path, "exists", return_value=False):
                with patch.dict(
                    os.environ,
                    {"SONARR_URL": "http://test", "SONARR_API_KEY": "test123"},
                ):
                    config = ConfigLoader()
                config.config = {"existing": {"nested": {"old_value": "old"}}}

                config._set_nested_value("existing.nested.new_value", "new")

                assert config.config["existing"]["nested"]["new_value"] == "new"
                assert config.config["existing"]["nested"]["old_value"] == "old"


class TestEnvironmentValueConversion:
    """Test environment variable type conversion"""

    def test_convert_boolean_values(self):
        """Test boolean conversion from environment variables"""
        test_env = {"SONARR_URL": "http://test:8989", "SONARR_API_KEY": "test123"}
        with patch.dict("os.environ", test_env, clear=True):
            with patch("src.config.loader.load_dotenv"):
                with patch.object(Path, "exists", return_value=False):
                    config = ConfigLoader()

                    test_cases = [
                        ("true", True),
                        ("TRUE", True),
                        ("yes", True),
                        ("1", True),
                        ("on", True),
                        ("false", False),
                        ("FALSE", False),
                        ("no", False),
                        ("0", False),
                        ("off", False),
                    ]

                    for input_val, expected in test_cases:
                        result = config._convert_env_value(input_val)
                        assert result == expected, f"Failed for input: {input_val}"

    def test_convert_numeric_values(self):
        """Test numeric conversion from environment variables"""
        test_env = {"SONARR_URL": "http://test:8989", "SONARR_API_KEY": "test123"}
        with patch.dict("os.environ", test_env, clear=True):
            with patch("src.config.loader.load_dotenv"):
                with patch.object(Path, "exists", return_value=False):
                    config = ConfigLoader()

                    # Integer values
                    assert config._convert_env_value("123") == 123
                    assert config._convert_env_value("0") == 0

                    # Float values
                    assert config._convert_env_value("123.45") == 123.45
                    assert config._convert_env_value("0.5") == 0.5

    def test_convert_string_values(self):
        """Test string values remain as strings"""
        test_env = {"SONARR_URL": "http://test:8989", "SONARR_API_KEY": "test123"}
        with patch.dict("os.environ", test_env, clear=True):
            with patch("src.config.loader.load_dotenv"):
                with patch.object(Path, "exists", return_value=False):
                    config = ConfigLoader()

                    # Non-numeric strings
                    assert config._convert_env_value("hello") == "hello"
                    assert (
                        config._convert_env_value("http://test.com")
                        == "http://test.com"
                    )
                    assert config._convert_env_value("test-123-abc") == "test-123-abc"


class TestConfigFileLoading:
    """Test YAML config file loading"""

    def test_load_valid_yaml(self):
        """Test loading valid YAML configuration"""
        yaml_content = """
        sonarr:
          url: http://localhost:8989
          api_key: test-key
        webhook:
          port: 8090
        """

        with patch.dict("os.environ", {}, clear=True):
            with patch("src.config.loader.load_dotenv"):
                with patch("builtins.open", mock_open(read_data=yaml_content)):
                    with patch.object(Path, "exists", return_value=True):
                        config = ConfigLoader(config_path="test.yaml")

                        assert config.get("sonarr.url") == "http://localhost:8989"
                        assert config.get("webhook.port") == 8090

    def test_load_empty_yaml(self):
        """Test loading empty YAML file"""
        with patch.dict("os.environ", {}, clear=True):
            with patch("src.config.loader.load_dotenv"):
                with patch("builtins.open", mock_open(read_data="")):
                    with patch.object(Path, "exists", return_value=True):
                        # Empty config will fail validation without required fields
                        with pytest.raises(ConfigurationError):
                            ConfigLoader(config_path="empty.yaml")

    def test_load_invalid_yaml(self):
        """Test handling of invalid YAML"""
        invalid_yaml = "invalid: yaml: content: [unclosed"

        with patch.dict("os.environ", {}, clear=True):
            with patch("src.config.loader.load_dotenv"):
                with patch("builtins.open", mock_open(read_data=invalid_yaml)):
                    with patch.object(Path, "exists", return_value=True):
                        with pytest.raises(yaml.YAMLError):
                            ConfigLoader(config_path="invalid.yaml")


class TestDefaultValues:
    """Test default value handling"""

    def test_get_with_default(self):
        """Test getting value with default when key doesn't exist"""
        test_env = {"SONARR_URL": "http://test:8989", "SONARR_API_KEY": "test123"}
        with patch.dict("os.environ", test_env, clear=True):
            with patch("src.config.loader.load_dotenv"):
                with patch.object(Path, "exists", return_value=False):
                    config = ConfigLoader()

                    result = config.get("nonexistent.key", "default_value")
                    assert result == "default_value"

    def test_get_without_default(self):
        """Test getting value without default when key doesn't exist"""
        test_env = {"SONARR_URL": "http://test:8989", "SONARR_API_KEY": "test123"}
        with patch.dict("os.environ", test_env, clear=True):
            with patch("src.config.loader.load_dotenv"):
                with patch.object(Path, "exists", return_value=False):
                    config = ConfigLoader()

                    result = config.get("nonexistent.key")
                    assert result is None


class TestCompleteConfigScenarios:
    """Test complete configuration scenarios"""

    def test_config_file_with_env_override(self, temp_config_file):
        """Test config file values overridden by environment"""
        # Config file has different values than env vars
        with patch.dict(
            os.environ,
            {"SONARR_URL": "http://env-override:9999", "WEBHOOK_PORT": "9090"},
            clear=True,
        ):  # Clear other env vars that might interfere
            with patch("src.config.loader.load_dotenv"):
                config = ConfigLoader(config_path=temp_config_file)

                # Environment should override file
                assert config.get("sonarr.url") == "http://env-override:9999"
                assert config.get("webhook.port") == 9090

    def test_minimal_valid_config(self):
        """Test minimal valid configuration"""
        with patch.dict(
            os.environ,
            {
                "SONARR_URL": "http://localhost:8989",
                "SONARR_API_KEY": "valid-key-32-chars-long-123456",
            },
            clear=True,
        ):
            with patch("src.config.loader.load_dotenv"):
                with patch.object(Path, "exists", return_value=False):
                    config = ConfigLoader()

                    # Should have required values
                    assert config.get("sonarr.url") == "http://localhost:8989"
                    assert (
                        config.get("sonarr.api_key") == "valid-key-32-chars-long-123456"
                    )

                    # Should auto-generate webhook secret
                    assert config.get("webhook.secret") is not None
                    assert len(config.get("webhook.secret")) > 0


class TestErrorHandling:
    """Test error handling scenarios"""

    def test_file_permission_error(self):
        """Test handling of file permission errors"""
        with patch("src.config.loader.load_dotenv"):
            with patch(
                "builtins.open", side_effect=PermissionError("Permission denied")
            ):
                with patch.object(Path, "exists", return_value=True):
                    with pytest.raises(PermissionError):
                        ConfigLoader(config_path="protected.yaml")

    def test_file_not_found_graceful_fallback(self):
        """Test graceful fallback when config file doesn't exist"""
        with patch.dict(
            os.environ,
            {"SONARR_URL": "http://localhost:8989", "SONARR_API_KEY": "valid-key-123"},
        ):
            with patch("src.config.loader.load_dotenv"):
                with patch.object(Path, "exists", return_value=False):
                    # Should not raise an error
                    config = ConfigLoader(config_path="nonexistent.yaml")

                    # Should still work with env vars
                    assert config.get("sonarr.url") == "http://localhost:8989"


@pytest.mark.unit
class TestConfigIntegration:
    """Integration tests for complete config loading flow"""

    def test_production_like_config(self):
        """Test production-like configuration scenario"""
        # Simulate production environment
        with patch.dict(
            os.environ,
            {
                "SONARR_URL": "https://sonarr.mydomain.com",
                "SONARR_API_KEY": "prod-key-abcdef123456789012345678",
                "WEBHOOK_SECRET": "prod-webhook-secret-123456789",
                "WEBHOOK_PORT": "8090",
                "FORCE_IMPORT_THRESHOLD": "15",
                "LOG_LEVEL": "INFO",
            },
            clear=True,
        ):
            with patch("src.config.loader.load_dotenv"):
                with patch.object(Path, "exists", return_value=False):
                    config = ConfigLoader()

                    # Verify all values are loaded correctly
                    assert config.get("sonarr.url") == "https://sonarr.mydomain.com"
                    assert config.get("webhook.port") == 8090
                    assert config.get("decisions.force_import_threshold") == 15
                    assert config.get("logging.level") == "INFO"

                    # Verify sensitive values are masked
                    masked = config.get_masked_config_for_logging()
                    assert "***MASKED***" in str(masked)
                    assert "prod-key-" not in str(masked)

    def test_development_config_with_file(self, temp_config_file):
        """Test development configuration with file and minimal env vars"""
        with patch.dict(
            os.environ, {"LOG_LEVEL": "DEBUG"}, clear=True  # Only override log level
        ):  # Clear all other env vars
            with patch("src.config.loader.load_dotenv"):
                config = ConfigLoader(config_path=temp_config_file)

                # File values
                assert config.get("sonarr.url") == "http://test-sonarr:8989"
                assert config.get("webhook.port") == 8090

                # Environment override
                assert config.get("logging.level") == "DEBUG"

                # Auto-generated
                assert config.get("webhook.secret") is not None
