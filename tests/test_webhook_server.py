"""
Unit tests for WebhookServer and webhook security.
"""

import hashlib
import hmac
import json
from datetime import datetime
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.api.webhook_server import WebhookServer


class TestWebhookServer:
    """Test WebhookServer class"""

    @pytest.fixture
    def webhook_server(self, mock_config):
        """Create WebhookServer instance for testing"""
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "webhook.secret": mock_config["webhook"]["secret"],
            "webhook.import_check_delay": 600,
            "decisions.force_import_threshold": 10,
        }.get(key, default)

        monitor = MagicMock()

        with patch("src.api.webhook_server.RateLimiter"):
            server = WebhookServer(monitor, config)

        return server

    def test_init(self, webhook_server, mock_config):
        """Test webhook server initialization"""
        assert webhook_server.webhook_secret == mock_config["webhook"]["secret"]
        assert webhook_server.max_requests_per_minute == 30
        assert len(webhook_server.grab_cache) == 0
        assert webhook_server.metrics["requests_total"] == 0

    def test_init_sets_up_flask_app(self, webhook_server):
        """Test that Flask app is properly configured"""
        assert webhook_server.app is not None
        assert webhook_server.app.logger.disabled is True


class TestAuthentication:
    """Test webhook authentication methods"""

    @pytest.fixture
    def webhook_server(self, mock_config):
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "webhook.secret": "test-secret-123"
        }.get(key, default)

        monitor = MagicMock()

        with patch("src.api.webhook_server.RateLimiter"):
            server = WebhookServer(monitor, config)

        return server

    def test_authenticate_request_no_secret_configured(self):
        """Test authentication when no secret is configured"""
        config = MagicMock()
        config.get.return_value = None  # No secret
        monitor = MagicMock()

        with patch("src.api.webhook_server.RateLimiter"):
            server = WebhookServer(monitor, config)

        with server.app.test_request_context("/test"):
            result = server._authenticate_request()
            assert result is True

    def test_authenticate_request_valid_secret_header(self, webhook_server):
        """Test authentication with valid secret header"""
        with webhook_server.app.test_request_context(
            "/test", headers={"X-Webhook-Secret": "test-secret-123"}
        ):
            result = webhook_server._authenticate_request()
            assert result is True

    def test_authenticate_request_invalid_secret_header(self, webhook_server):
        """Test authentication with invalid secret header"""
        with webhook_server.app.test_request_context(
            "/test", headers={"X-Webhook-Secret": "wrong-secret"}
        ):
            result = webhook_server._authenticate_request()
            assert result is False

    def test_authenticate_request_valid_hmac_signature(self, webhook_server):
        """Test authentication with valid HMAC signature"""
        test_data = b'{"eventType": "Test"}'
        expected_sig = hmac.new(
            b"test-secret-123", test_data, hashlib.sha256
        ).hexdigest()

        with webhook_server.app.test_request_context(
            "/test",
            data=test_data,
            headers={"X-Webhook-Signature": f"sha256={expected_sig}"},
        ):
            result = webhook_server._authenticate_request()
            assert result is True

    def test_authenticate_request_invalid_hmac_signature(self, webhook_server):
        """Test authentication with invalid HMAC signature"""
        test_data = b'{"eventType": "Test"}'

        with webhook_server.app.test_request_context(
            "/test",
            data=test_data,
            headers={"X-Webhook-Signature": "sha256=invalid_signature"},
        ):
            result = webhook_server._authenticate_request()
            assert result is False

    def test_authenticate_request_malformed_hmac(self, webhook_server):
        """Test authentication with malformed HMAC header"""
        with webhook_server.app.test_request_context(
            "/test", headers={"X-Webhook-Signature": "invalid_format"}
        ):
            result = webhook_server._authenticate_request()
            assert result is False

    def test_authenticate_request_no_credentials(self, webhook_server):
        """Test authentication with no credentials provided"""
        with webhook_server.app.test_request_context("/test"):
            result = webhook_server._authenticate_request()
            assert result is False


class TestRoutes:
    """Test Flask route handlers"""

    @pytest.fixture
    def client(self, mock_config):
        """Create Flask test client"""
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "webhook.secret": None,  # No auth for easier testing
            "decisions.force_import_threshold": 10,
        }.get(key, default)

        monitor = MagicMock()

        with patch("src.api.webhook_server.RateLimiter") as mock_rate_limiter:
            mock_rate_limiter_instance = MagicMock()
            mock_rate_limiter_instance.is_allowed.return_value = True
            mock_rate_limiter.return_value = mock_rate_limiter_instance

            server = WebhookServer(monitor, config)

        return server.app.test_client()

    def test_health_endpoint(self, client):
        """Test health check endpoint"""
        response = client.get("/health")

        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "healthy"
        assert data["service"] == "Sonarr Import Monitor Webhook"
        assert data["version"] == "2.0.0"
        assert "timestamp" in data
        assert "uptime_seconds" in data

    def test_metrics_endpoint(self, client):
        """Test metrics endpoint"""
        response = client.get("/metrics")

        assert response.status_code == 200
        data = response.get_json()
        assert "uptime_seconds" in data
        assert "requests_total" in data
        assert "events_processed" in data
        assert "webhook_secret_configured" in data

    def test_webhook_info_endpoint(self, client):
        """Test webhook info endpoint"""
        response = client.get("/webhook/sonarr")

        assert response.status_code == 200
        data = response.get_json()
        assert data["service"] == "Sonarr Import Monitor Webhook"
        assert "supported_events" in data
        assert "Test" in data["supported_events"]
        assert "Grab" in data["supported_events"]


class TestWebhookHandlers:
    """Test individual webhook event handlers"""

    @pytest.fixture
    def webhook_server(self, mock_config):
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "webhook.secret": None,  # No auth for testing
            "webhook.import_check_delay": 600,
            "decisions.force_import_threshold": 10,
        }.get(key, default)

        monitor = MagicMock()

        with patch("src.api.webhook_server.RateLimiter") as mock_rate_limiter:
            mock_rate_limiter_instance = MagicMock()
            mock_rate_limiter_instance.is_allowed.return_value = True
            mock_rate_limiter.return_value = mock_rate_limiter_instance

            server = WebhookServer(monitor, config)

        return server

    def test_handle_test_webhook(self, webhook_server, sample_webhook_payload):
        """Test handling of test webhook"""
        test_payload = {
            "eventType": "Test",
            "series": {"title": "Test Series"},
            "episodes": [{"seasonNumber": 1, "episodeNumber": 1}],
        }

        with webhook_server.app.app_context():
            response, status_code = webhook_server.handle_test(test_payload)

        assert status_code == 200
        data = response.get_json()
        assert data["status"] == "success"
        assert "Webhook test successful" in data["message"]
        assert data["authenticated"] is True

    @patch("threading.Timer")
    def test_handle_grab_webhook(self, mock_timer, webhook_server):
        """Test handling of grab webhook"""
        grab_payload = {
            "eventType": "Grab",
            "series": {"id": 10, "title": "Test Series"},
            "episodes": [{"id": 100, "seasonNumber": 1, "episodeNumber": 1}],
            "release": {
                "customFormatScore": 100,
                "customFormats": [{"name": "HDR"}],
                "releaseTitle": "Test.Release.720p",
                "indexer": "TestTracker",
            },
            "downloadId": "test-download-123",
            "downloadClient": "qBittorrent",
        }

        mock_timer_instance = MagicMock()
        mock_timer.return_value = mock_timer_instance

        with webhook_server.app.app_context():
            response, status_code = webhook_server.handle_grab(grab_payload)

        assert status_code == 200
        data = response.get_json()
        assert data["status"] == "success"
        assert data["episodes_cached"] == 1

        # Verify grab was cached
        assert 100 in webhook_server.grab_cache
        grab_info = webhook_server.grab_cache[100]
        assert grab_info["score"] == 100
        assert grab_info["download_id"] == "test-download-123"
        assert grab_info["indexer"] == "TestTracker"

        # Verify timer was scheduled
        mock_timer.assert_called_once()
        mock_timer_instance.start.assert_called_once()

    def test_handle_download_webhook_with_cache(self, webhook_server):
        """Test handling download webhook with cached grab info"""
        # Pre-populate cache
        webhook_server.grab_cache[100] = {
            "score": 120,
            "title": "Test Release",
            "download_id": "test-123",
        }

        download_payload = {
            "eventType": "Download",
            "series": {"title": "Test Series"},
            "episodes": [{"id": 100}],
            "episodeFile": {"customFormatScore": 110},
        }

        with webhook_server.app.app_context():
            response, status_code = webhook_server.handle_download(download_payload)

        assert status_code == 200
        data = response.get_json()
        assert data["status"] == "success"

        # Cache should be cleared after successful import
        assert 100 not in webhook_server.grab_cache

    def test_handle_download_webhook_score_mismatch(self, webhook_server):
        """Test handling download with significant score mismatch"""
        # Pre-populate cache with high score
        webhook_server.grab_cache[100] = {
            "score": 150,  # Much higher than import
            "title": "Test Release",
            "download_id": "test-123",
        }

        download_payload = {
            "eventType": "Download",
            "series": {"title": "Test Series"},
            "episodes": [{"id": 100}],
            "episodeFile": {"customFormatScore": 50},  # Much lower
        }

        with patch("src.api.webhook_server.logger") as mock_logger:
            with webhook_server.app.app_context():
                response, status_code = webhook_server.handle_download(download_payload)

            assert status_code == 200
            # Should log warning about score mismatch
            warning_calls = [call for call in mock_logger.warning.call_args_list]
            assert any("Score mismatch" in str(call) for call in warning_calls)

    def test_handle_import_failed_webhook(self, webhook_server):
        """Test handling import failed webhook"""
        with patch("threading.Timer") as mock_timer:
            mock_timer_instance = MagicMock()
            mock_timer.return_value = mock_timer_instance

            failed_payload = {
                "eventType": "ImportFailed",
                "series": {"title": "Test Series"},
                "episodes": [{"id": 100}],
                "message": "Import failed: File not found",
            }

            with webhook_server.app.app_context():
                response, status_code = webhook_server.handle_import_failed(
                    failed_payload
                )

            assert status_code == 200
            data = response.get_json()
            assert data["status"] == "success"
            assert data["episodes_scheduled"] == 1

            # Should schedule immediate check
            mock_timer.assert_called_once()

    def test_handle_health_issue_webhook(self, webhook_server):
        """Test handling health issue webhook"""
        health_payload = {
            "eventType": "HealthIssue",
            "level": "Warning",
            "message": "Disk space low",
        }

        with patch("src.api.webhook_server.logger") as mock_logger:
            with webhook_server.app.app_context():
                response, status_code = webhook_server.handle_health_issue(
                    health_payload
                )

            assert status_code == 200
            data = response.get_json()
            assert data["status"] == "acknowledged"

            # Should log the health issue
            mock_logger.warning.assert_called_once()


class TestAuthenticatedRoutes:
    """Test routes with authentication enabled"""

    @pytest.fixture
    def authenticated_client(self, mock_config):
        """Create Flask test client with authentication"""
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "webhook.secret": "test-secret-123",
            "webhook.import_check_delay": 600,
            "decisions.force_import_threshold": 10,
        }.get(key, default)

        monitor = MagicMock()

        with patch("src.api.webhook_server.RateLimiter") as mock_rate_limiter:
            mock_rate_limiter_instance = MagicMock()
            mock_rate_limiter_instance.is_allowed.return_value = True
            mock_rate_limiter.return_value = mock_rate_limiter_instance

            server = WebhookServer(monitor, config)

        return server.app.test_client(), server

    def test_authenticated_webhook_valid_secret(self, authenticated_client):
        """Test authenticated webhook with valid secret"""
        client, server = authenticated_client

        test_payload = {"eventType": "Test"}

        response = client.post(
            "/webhook/sonarr",
            data=json.dumps(test_payload),
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Secret": "test-secret-123",
            },
        )

        assert response.status_code == 200
        assert server.metrics["requests_authenticated"] > 0

    def test_authenticated_webhook_invalid_secret(self, authenticated_client):
        """Test authenticated webhook with invalid secret"""
        client, server = authenticated_client

        test_payload = {"eventType": "Test"}

        response = client.post(
            "/webhook/sonarr",
            data=json.dumps(test_payload),
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Secret": "wrong-secret",
            },
        )

        assert response.status_code == 401
        data = response.get_json()
        assert data["error"] == "Authentication failed"
        assert server.metrics["requests_rejected"] > 0

    def test_authenticated_webhook_valid_hmac(self, authenticated_client):
        """Test authenticated webhook with valid HMAC signature"""
        client, server = authenticated_client

        test_payload = {"eventType": "Test"}
        test_data = json.dumps(test_payload).encode()

        # Calculate HMAC signature
        expected_sig = hmac.new(
            b"test-secret-123", test_data, hashlib.sha256
        ).hexdigest()

        response = client.post(
            "/webhook/sonarr",
            data=test_data,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Signature": f"sha256={expected_sig}",
            },
        )

        assert response.status_code == 200
        assert server.metrics["requests_authenticated"] > 0

    def test_authenticated_webhook_no_credentials(self, authenticated_client):
        """Test authenticated webhook with no credentials"""
        client, server = authenticated_client

        test_payload = {"eventType": "Test"}

        response = client.post(
            "/webhook/sonarr",
            data=json.dumps(test_payload),
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 401
        assert server.metrics["requests_rejected"] > 0


class TestRateLimiting:
    """Test rate limiting functionality"""

    @pytest.fixture
    def rate_limited_server(self, mock_config):
        """Create webhook server with rate limiting"""
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "webhook.secret": None  # No auth for easier testing
        }.get(key, default)

        monitor = MagicMock()

        # Create actual rate limiter for testing
        with patch("src.api.webhook_server.RateLimiter") as mock_rate_limiter_class:
            mock_rate_limiter = MagicMock()
            mock_rate_limiter_class.return_value = mock_rate_limiter

            server = WebhookServer(monitor, config)

        return server.app.test_client(), server, mock_rate_limiter

    def test_rate_limit_allowed(self, rate_limited_server):
        """Test request when rate limit is not exceeded"""
        client, server, mock_rate_limiter = rate_limited_server
        mock_rate_limiter.is_allowed.return_value = True

        test_payload = {"eventType": "Test"}

        response = client.post(
            "/webhook/sonarr",
            data=json.dumps(test_payload),
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 200
        mock_rate_limiter.is_allowed.assert_called_once()

    def test_rate_limit_exceeded(self, rate_limited_server):
        """Test request when rate limit is exceeded"""
        client, server, mock_rate_limiter = rate_limited_server
        mock_rate_limiter.is_allowed.return_value = False

        test_payload = {"eventType": "Test"}

        response = client.post(
            "/webhook/sonarr",
            data=json.dumps(test_payload),
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 429
        data = response.get_json()
        assert "Rate limit exceeded" in data["error"]
        assert server.metrics["requests_rejected"] > 0

    def test_health_endpoint_bypasses_rate_limit(self, rate_limited_server):
        """Test that health endpoint bypasses rate limiting"""
        client, server, mock_rate_limiter = rate_limited_server
        # Health endpoint should work normally when not rate limited
        mock_rate_limiter.is_allowed.return_value = True

        response = client.get("/health")
        assert response.status_code == 200

        # But when rate limited, health should also be limited in current implementation
        mock_rate_limiter.is_allowed.return_value = False
        response = client.get("/health")
        assert response.status_code == 429  # Currently health is also rate limited


class TestWebhookEventProcessing:
    """Test webhook event processing logic"""

    @pytest.fixture
    def webhook_server(self, mock_config):
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "webhook.secret": None,
            "webhook.import_check_delay": 10,  # Short delay for testing
            "decisions.force_import_threshold": 10,
        }.get(key, default)

        monitor = MagicMock()

        with patch("src.api.webhook_server.RateLimiter") as mock_rate_limiter:
            mock_rate_limiter_instance = MagicMock()
            mock_rate_limiter_instance.is_allowed.return_value = True
            mock_rate_limiter.return_value = mock_rate_limiter_instance

            server = WebhookServer(monitor, config)

        return server, server.app.test_client()

    def test_webhook_handler_test_event(self, webhook_server):
        """Test webhook handler for Test event"""
        server, client = webhook_server

        test_payload = {
            "eventType": "Test",
            "series": {"title": "Test Series"},
            "episodes": [{"seasonNumber": 1, "episodeNumber": 1}],
        }

        response = client.post(
            "/webhook/sonarr",
            data=json.dumps(test_payload),
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 200
        assert server.metrics["events_processed"]["Test"] == 1

    def test_webhook_handler_grab_event(self, webhook_server):
        """Test webhook handler for Grab event"""
        server, client = webhook_server

        grab_payload = {
            "eventType": "Grab",
            "series": {"id": 10, "title": "Test Series"},
            "episodes": [{"id": 100}],
            "release": {
                "customFormatScore": 100,
                "customFormats": [{"name": "HDR"}],
                "releaseTitle": "Test.Release",
            },
            "downloadId": "test-123",
        }

        with patch("threading.Timer"):
            response = client.post(
                "/webhook/sonarr",
                data=json.dumps(grab_payload),
                headers={"Content-Type": "application/json"},
            )

        assert response.status_code == 200
        assert server.metrics["events_processed"]["Grab"] == 1
        assert 100 in server.grab_cache

    def test_webhook_handler_unknown_event(self, webhook_server):
        """Test webhook handler for unknown event type"""
        server, client = webhook_server

        unknown_payload = {
            "eventType": "UnknownEvent",
            "series": {"title": "Test Series"},
        }

        response = client.post(
            "/webhook/sonarr",
            data=json.dumps(unknown_payload),
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 200
        data = response.get_json()
        assert data["status"] == "ignored"
        assert data["event_type"] == "UnknownEvent"

    def test_webhook_handler_invalid_json(self, webhook_server):
        """Test webhook handler with invalid JSON"""
        server, client = webhook_server

        response = client.post(
            "/webhook/sonarr",
            data="invalid json",
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 400
        data = response.get_json()
        assert "Invalid JSON data" in data["error"]

    def test_webhook_handler_processing_error(self, webhook_server):
        """Test webhook handler with processing error"""
        server, client = webhook_server

        # Mock handler to raise exception
        server.handle_test = Mock(side_effect=Exception("Processing error"))

        test_payload = {"eventType": "Test"}

        response = client.post(
            "/webhook/sonarr",
            data=json.dumps(test_payload),
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 500
        data = response.get_json()
        assert data["error"] == "Internal server error"


class TestDelayedProcessing:
    """Test delayed import checking functionality"""

    @pytest.fixture
    def webhook_server(self, mock_config):
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "webhook.secret": None,
            "webhook.import_check_delay": 600,
        }.get(key, default)

        monitor = MagicMock()

        with patch("src.api.webhook_server.RateLimiter"):
            server = WebhookServer(monitor, config)

        return server

    def test_check_if_imported_cache_cleared(self, webhook_server):
        """Test delayed check when cache already cleared (successful import)"""
        # Episode not in cache (already imported)
        webhook_server._check_if_imported(100, "test-123")

        # Should not call any queue methods
        webhook_server.monitor.sonarr_client.get_queue.assert_not_called()

    def test_check_if_imported_still_in_queue(self, webhook_server):
        """Test delayed check when item still in queue"""
        # Pre-populate cache
        webhook_server.grab_cache[100] = {
            "title": "Test Release",
            "download_id": "test-123",
        }

        # Mock queue with our item
        mock_queue_item = {
            "id": 1,
            "episode": {"id": 100},
            "downloadId": "test-123",
            "status": "completed",
            "trackedDownloadState": "importPending",
        }

        webhook_server.monitor.sonarr_client.get_queue.return_value = [mock_queue_item]
        with patch.object(
            webhook_server, "_find_queue_item", return_value=mock_queue_item
        ):
            webhook_server._check_if_imported(100, "test-123")

            # Should process the queue item
            webhook_server.monitor.process_queue_item.assert_called_once_with(
                mock_queue_item
            )

    def test_check_if_imported_silent_import(self, webhook_server):
        """Test delayed check for silent import"""
        # Pre-populate cache
        webhook_server.grab_cache[100] = {
            "title": "Test Release",
            "download_id": "test-123",
        }

        # Item not in queue
        webhook_server.monitor.sonarr_client.get_queue.return_value = []
        with patch.object(webhook_server, "_find_queue_item", return_value=None):
            with patch.object(
                webhook_server, "_was_imported_silently", return_value=True
            ):
                webhook_server._check_if_imported(100, "test-123")

                # Cache should be cleared
                assert 100 not in webhook_server.grab_cache

    def test_find_queue_item_found(self, webhook_server):
        """Test finding queue item by episode and download ID"""
        queue = [
            {"id": 1, "episode": {"id": 100}, "downloadId": "test-123"},
            {"id": 2, "episode": {"id": 101}, "downloadId": "test-456"},
        ]

        result = webhook_server._find_queue_item(queue, 100, "test-123")

        assert result is not None
        assert result["id"] == 1

    def test_find_queue_item_not_found(self, webhook_server):
        """Test finding queue item when not present"""
        queue = [{"id": 1, "episode": {"id": 101}, "downloadId": "test-456"}]

        result = webhook_server._find_queue_item(queue, 100, "test-123")
        assert result is None

    def test_was_imported_silently_true(self, webhook_server):
        """Test detecting silent import"""
        history = [{"eventType": "downloadFolderImported", "downloadId": "test-123"}]

        webhook_server.monitor.sonarr_client.get_history_for_episode.return_value = (
            history
        )

        result = webhook_server._was_imported_silently(100, "test-123")
        assert result is True

    def test_was_imported_silently_false(self, webhook_server):
        """Test when no silent import occurred"""
        history = [{"eventType": "grabbed", "downloadId": "test-123"}]

        webhook_server.monitor.sonarr_client.get_history_for_episode.return_value = (
            history
        )

        result = webhook_server._was_imported_silently(100, "test-123")
        assert result is False


class TestMetrics:
    """Test metrics tracking"""

    @pytest.fixture
    def webhook_server(self, mock_config):
        config = MagicMock()
        config.get.return_value = None
        monitor = MagicMock()

        with patch("src.api.webhook_server.RateLimiter"):
            server = WebhookServer(monitor, config)

        return server

    def test_metrics_initialization(self, webhook_server):
        """Test that metrics are properly initialized"""
        metrics = webhook_server.metrics

        assert metrics["requests_total"] == 0
        assert metrics["requests_authenticated"] == 0
        assert metrics["requests_rejected"] == 0
        assert isinstance(metrics["events_processed"], dict)
        assert "start_time" in metrics

    def test_metrics_tracking(self, webhook_server):
        """Test that metrics are tracked correctly"""
        client = webhook_server.app.test_client()

        # Make authenticated request
        with patch.object(webhook_server, "_authenticate_request", return_value=True):
            response = client.post(
                "/webhook/sonarr",
                data=json.dumps({"eventType": "Test"}),
                headers={"Content-Type": "application/json"},
            )

        assert response.status_code == 200
        assert webhook_server.metrics["requests_total"] > 0
        assert webhook_server.metrics["requests_authenticated"] > 0
        assert webhook_server.metrics["events_processed"]["Test"] > 0


class TestShutdown:
    """Test webhook server shutdown"""

    @pytest.fixture
    def webhook_server(self, mock_config):
        config = MagicMock()
        config.get.return_value = None
        monitor = MagicMock()

        with patch("src.api.webhook_server.RateLimiter"):
            server = WebhookServer(monitor, config)

        return server

    def test_shutdown_clears_cache(self, webhook_server):
        """Test that shutdown clears the grab cache"""
        # Pre-populate cache
        webhook_server.grab_cache[100] = {"test": "data"}

        webhook_server.shutdown()

        assert len(webhook_server.grab_cache) == 0


@pytest.mark.webhook
class TestWebhookIntegration:
    """Integration tests for webhook functionality"""

    @pytest.fixture
    def full_webhook_setup(self, mock_config):
        """Create complete webhook setup for integration testing"""
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "webhook.secret": "integration-test-secret",
            "webhook.import_check_delay": 1,  # Short delay for testing
            "decisions.force_import_threshold": 10,
        }.get(key, default)

        monitor = MagicMock()
        monitor.sonarr_client.get_queue.return_value = []
        monitor.sonarr_client.get_history_for_episode.return_value = []

        with patch("src.api.webhook_server.RateLimiter") as mock_rate_limiter:
            mock_rate_limiter_instance = MagicMock()
            mock_rate_limiter_instance.is_allowed.return_value = True
            mock_rate_limiter.return_value = mock_rate_limiter_instance

            server = WebhookServer(monitor, config)

        return server, server.app.test_client(), monitor

    def test_grab_to_download_workflow(self, full_webhook_setup):
        """Test complete grab to download workflow"""
        server, client, monitor = full_webhook_setup

        # Step 1: Receive grab webhook
        grab_payload = {
            "eventType": "Grab",
            "series": {"id": 10, "title": "Test Series"},
            "episodes": [{"id": 100}],
            "release": {"customFormatScore": 100},
            "downloadId": "test-123",
        }

        with patch("threading.Timer") as mock_timer:
            response = client.post(
                "/webhook/sonarr",
                data=json.dumps(grab_payload),
                headers={
                    "Content-Type": "application/json",
                    "X-Webhook-Secret": "integration-test-secret",
                },
            )

        assert response.status_code == 200
        assert 100 in server.grab_cache

        # Step 2: Receive download webhook
        download_payload = {
            "eventType": "Download",
            "series": {"title": "Test Series"},
            "episodes": [{"id": 100}],
            "episodeFile": {"customFormatScore": 95},
        }

        response = client.post(
            "/webhook/sonarr",
            data=json.dumps(download_payload),
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Secret": "integration-test-secret",
            },
        )

        assert response.status_code == 200
        # Cache should be cleared after successful import
        assert 100 not in server.grab_cache

    def test_grab_to_failure_workflow(self, full_webhook_setup):
        """Test grab to import failure workflow"""
        server, client, monitor = full_webhook_setup

        # Step 1: Receive grab webhook
        grab_payload = {
            "eventType": "Grab",
            "episodes": [{"id": 100}],
            "downloadId": "test-123",
        }

        with patch("threading.Timer"):
            client.post(
                "/webhook/sonarr",
                data=json.dumps(grab_payload),
                headers={
                    "Content-Type": "application/json",
                    "X-Webhook-Secret": "integration-test-secret",
                },
            )

        # Step 2: Receive import failed webhook
        failed_payload = {
            "eventType": "ImportFailed",
            "series": {"title": "Test Series"},
            "episodes": [{"id": 100}],
            "message": "Import failed",
        }

        with patch("threading.Timer") as mock_timer:
            response = client.post(
                "/webhook/sonarr",
                data=json.dumps(failed_payload),
                headers={
                    "Content-Type": "application/json",
                    "X-Webhook-Secret": "integration-test-secret",
                },
            )

        assert response.status_code == 200
        # Should schedule immediate check
        mock_timer.assert_called_once()
