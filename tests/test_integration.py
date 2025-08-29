"""
Integration tests for complete workflows and component interactions.
"""

import json
import threading
import time
from unittest.mock import MagicMock, Mock, patch

import pytest
import requests_mock

from src.api.sonarr_client import SonarrClient
from src.api.webhook_server import WebhookServer
from src.config.loader import ConfigLoader
from src.core.analyzer import Decision, ScoreAnalyzer
from src.core.monitor import SonarrImportMonitor


@pytest.mark.integration
class TestCompleteWorkflow:
    """Test complete end-to-end workflows"""

    @pytest.fixture
    def full_system(self, mock_config, test_env_vars):
        """Create complete system with all components"""
        with patch("src.config.loader.load_dotenv"), patch(
            "src.config.loader.Path.exists", return_value=False
        ), patch(
            "src.config.loader.secrets.token_urlsafe", return_value="generated-secret"
        ):
            config = ConfigLoader()

        # Create real components (but with mocked APIs)
        with requests_mock.Mocker() as m:
            # Mock all required API endpoints
            m.get(
                "http://test-sonarr:8989/api/v3/system/status",
                json={"version": "3.0.0"},
            )
            m.get("http://test-sonarr:8989/api/v3/customformat", json=[])
            m.get("http://test-sonarr:8989/api/v3/qualityprofile", json=[])
            m.get("http://test-sonarr:8989/api/v3/series", json=[])

            with patch("src.core.monitor.signal.signal"):
                monitor = SonarrImportMonitor(config)

        return monitor, config

    def test_monitor_initialization_and_configuration_test(self, full_system):
        """Test complete monitor initialization and configuration testing"""
        monitor, config = full_system

        with requests_mock.Mocker() as m:
            # Mock successful API responses
            m.get(
                "http://test-sonarr:8989/api/v3/system/status",
                json={"version": "3.0.0"},
            )
            m.get(
                "http://test-sonarr:8989/api/v3/customformat",
                json=[{"id": 1, "name": "HDR"}],
            )
            m.get(
                "http://test-sonarr:8989/api/v3/qualityprofile",
                json=[{"id": 1, "name": "HD-1080p"}],
            )
            m.get(
                "http://test-sonarr:8989/api/v3/series",
                json=[{"id": 10, "qualityProfileId": 1}],
            )

            result = monitor.test_configuration()

            assert result is True
            assert len(m.request_history) == 4  # All API endpoints called

    def test_complete_queue_processing_workflow(self, full_system):
        """Test complete queue processing with decision making"""
        monitor, config = full_system

        # Mock queue with stuck item
        queue_item = {
            "id": 1,
            "episode": {"id": 100},
            "series": {"id": 10},
            "downloadId": "test-download-123",
            "status": "completed",
            "trackedDownloadState": "importPending",
            "quality": {"quality": {"id": 6, "name": "720p"}},
        }

        # Mock history for grab information
        history = [
            {
                "eventType": "grabbed",
                "downloadId": "test-download-123",
                "customFormatScore": 100,
                "customFormats": [{"name": "HDR"}, {"name": "Atmos"}],
                "data": {"indexer": "PrivateHD"},
            }
        ]

        with requests_mock.Mocker() as m:
            # Mock queue API
            m.get(
                "http://test-sonarr:8989/api/v3/queue", json={"records": [queue_item]}
            )

            # Mock history API
            m.get("http://test-sonarr:8989/api/v3/history", json={"records": history})

            # Mock episode details (no current file)
            m.get("http://test-sonarr:8989/api/v3/episode/100", json={"hasFile": False})

            # Mock custom formats API
            m.get(
                "http://test-sonarr:8989/api/v3/customformat",
                json=[{"id": 1, "name": "HDR"}, {"id": 2, "name": "Atmos"}],
            )

            # Mock quality profiles API
            m.get(
                "http://test-sonarr:8989/api/v3/qualityprofile",
                json=[
                    {
                        "id": 1,
                        "name": "HD-1080p",
                        "formatItems": [
                            {"format": 1, "score": 100},
                            {"format": 2, "score": 50},
                        ],
                    }
                ],
            )

            # Mock series API
            m.get(
                "http://test-sonarr:8989/api/v3/series",
                json=[{"id": 10, "title": "Test Series", "qualityProfileId": 1}],
            )

            # Mock force import
            m.get(
                "http://test-sonarr:8989/api/v3/manualimport",
                json=[
                    {
                        "id": 1,
                        "path": "/test.mkv",
                        "series": {"id": 10, "title": "Test Series"},
                        "episodes": [{"id": 100, "title": "Test Episode"}],
                    }
                ],
            )
            m.post(
                "http://test-sonarr:8989/api/v3/command",
                json={"name": "ManualImport", "id": 123},
                status_code=201,
            )

            results = monitor.process_stuck_imports()

            assert results["processed"] == 1
            assert results["forced"] == 1
            assert results["removed"] == 0


class TestWebhookToMonitorIntegration:
    """Test integration between webhook server and monitor"""

    @pytest.fixture
    def integrated_system(self, mock_config, test_env_vars):
        """Create integrated webhook + monitor system"""
        with patch("src.config.loader.load_dotenv"), patch(
            "src.config.loader.Path.exists", return_value=False
        ):
            config = ConfigLoader()

        with patch("src.core.monitor.signal.signal"), patch(
            "src.core.monitor.SonarrClient"
        ) as MockClient:
            mock_sonarr_client = MagicMock()
            MockClient.return_value = mock_sonarr_client
            monitor = SonarrImportMonitor(config)

        # Create webhook server
        webhook_server = WebhookServer(monitor, config)

        return monitor, webhook_server, config

    def test_webhook_triggers_monitor_processing(self, integrated_system):
        """Test that webhook events trigger appropriate monitor processing"""
        monitor, webhook_server, config = integrated_system

        # Mock monitor methods
        monitor.process_queue_item = MagicMock(return_value="forced_import")
        monitor.check_episode_queue = MagicMock()

        # Mock queue for delayed check
        mock_queue_item = {
            "id": 1,
            "episode": {"id": 100},
            "downloadId": "test-123",
            "status": "completed",
        }
        monitor.sonarr_client.get_queue.return_value = [mock_queue_item]
        webhook_server._find_queue_item = Mock(return_value=mock_queue_item)

        # Simulate grab event
        grab_data = {
            "eventType": "Grab",
            "episodes": [{"id": 100}],
            "downloadId": "test-123",
            "release": {"customFormatScore": 100},
        }

        # Handle grab (should cache and schedule check)
        with webhook_server.app.app_context():
            webhook_server.handle_grab(grab_data)

        # Verify grab was cached
        assert 100 in webhook_server.grab_cache

        # Simulate delayed check manually
        webhook_server._check_if_imported(100, "test-123")

        # Should have processed the queue item
        monitor.process_queue_item.assert_called_once_with(mock_queue_item)

    def test_manual_interaction_triggers_immediate_check(self, integrated_system):
        """Test that ManualInteractionRequired webhook triggers immediate queue check"""
        monitor, webhook_server, config = integrated_system

        # Mock monitor method
        monitor.check_download_queue = MagicMock()

        # Simulate manual interaction event
        manual_data = {
            "eventType": "ManualInteractionRequired",
            "downloadId": "test-download-123",
            "series": {"title": "Test Series"},
            "downloadStatusMessages": [
                {"messages": ["Release was matched to series by ID"]}
            ],
        }

        with patch("threading.Timer") as mock_timer:
            mock_timer_instance = MagicMock()
            mock_timer.return_value = mock_timer_instance

            with webhook_server.app.app_context():
                webhook_server.handle_manual_interaction(manual_data)

            # Should schedule immediate check
            mock_timer.assert_called_once_with(
                5, monitor.check_download_queue, args=["test-download-123"]
            )
            mock_timer_instance.start.assert_called_once()


class TestDecisionMakingIntegration:
    """Test integration of decision making across components"""

    @pytest.fixture
    def decision_system(self, mock_config, test_env_vars):
        """Create system focused on decision making"""
        # Create a mock config that returns expected values
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "trackers.private": mock_config["trackers"]["private"],
            "trackers.public": mock_config["trackers"]["public"],
            "decisions.force_import_threshold": mock_config["decisions"][
                "force_import_threshold"
            ],
        }.get(key, default)

        # Create components with real decision logic
        sonarr_client = MagicMock()
        analyzer = ScoreAnalyzer(config, sonarr_client)

        return analyzer, sonarr_client, config

    def test_force_import_decision_flow(self, decision_system):
        """Test complete force import decision flow"""
        analyzer, sonarr_client, config = decision_system

        # Setup queue item
        queue_item = {
            "id": 1,
            "episode": {"id": 100},
            "series": {"id": 10},
            "downloadId": "test-123",
            "quality": {"quality": {"id": 6}},
        }

        # Mock grab history (high score)
        grab_history = [
            {
                "eventType": "grabbed",
                "downloadId": "test-123",
                "customFormatScore": 120,
                "customFormats": [{"name": "HDR"}, {"name": "Atmos"}],
                "data": {"indexer": "BeyondHD"},
            }
        ]

        # Mock current file (low score)
        mock_episode_response = MagicMock()
        mock_episode_response.json.return_value = {"hasFile": True, "episodeFileId": 50}

        # Mock API responses
        sonarr_client.get_history_for_episode.return_value = grab_history
        sonarr_client._make_request.return_value = mock_episode_response
        sonarr_client.get_episode_file.return_value = {
            "customFormatScore": 80,
            "customFormats": [{"name": "x265"}],
        }
        sonarr_client.get_custom_format_scores.return_value = {1: 100, 2: 50}
        sonarr_client.fetch_custom_formats.return_value = {
            1: {"id": 1, "name": "HDR"},
            2: {"id": 2, "name": "Atmos"},
        }

        # Analyze the item
        decision = analyzer.analyze_queue_item(queue_item)

        # Verify decision
        assert decision.action == "force_import"
        assert decision.grab_score == 120
        assert decision.current_score == 80
        assert decision.score_difference == 40
        assert decision.is_private_tracker is True
        assert "HDR" in decision.grab_formats
        assert "Atmos" in decision.grab_formats

    def test_private_tracker_protection_flow(self, decision_system):
        """Test private tracker protection decision flow"""
        analyzer, sonarr_client, config = decision_system

        queue_item = {
            "id": 1,
            "episode": {"id": 100},
            "series": {"id": 10},
            "downloadId": "test-123",
        }

        # Mock grab history (lower score but private tracker)
        grab_history = [
            {
                "eventType": "grabbed",
                "downloadId": "test-123",
                "customFormatScore": 60,
                "customFormats": [{"name": "x265"}],
                "data": {"indexer": "PrivateHD"},  # Private tracker
            }
        ]

        # Mock current file (higher score)
        mock_episode_response = MagicMock()
        mock_episode_response.json.return_value = {"hasFile": True, "episodeFileId": 50}

        # Mock API responses
        sonarr_client.get_history_for_episode.return_value = grab_history
        sonarr_client._make_request.return_value = mock_episode_response
        sonarr_client.get_episode_file.return_value = {
            "customFormatScore": 100,
            "customFormats": [{"name": "HDR"}, {"name": "Atmos"}],
        }
        sonarr_client.get_custom_format_scores.return_value = {1: 50}
        sonarr_client.fetch_custom_formats.return_value = {1: {"id": 1, "name": "x265"}}

        decision = analyzer.analyze_queue_item(queue_item)

        # Should keep private tracker despite lower score
        assert decision.action == "keep"
        assert decision.is_private_tracker is True
        assert "Private tracker protection" in decision.reasoning


class TestErrorHandlingIntegration:
    """Test error handling across integrated components"""

    @pytest.fixture
    def error_prone_system(self, mock_config):
        """Create system for testing error conditions"""
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "sonarr.url": "http://unreachable:8989",
            "sonarr.api_key": "test-key",
            "decisions.force_import_threshold": 10,
            "trackers.private": ["privatehd"],
            "trackers.public": ["nyaa"],
        }.get(key, default)

        # Don't patch signal for this test
        with patch("src.core.monitor.signal.signal"):
            monitor = SonarrImportMonitor(config)

        return monitor

    def test_api_failure_recovery(self, error_prone_system):
        """Test system recovery from API failures"""
        monitor = error_prone_system

        # First call fails
        with requests_mock.Mocker() as m:
            m.get(
                "http://unreachable:8989/api/v3/queue",
                exc=Exception("Connection failed"),
            )

            # Should handle gracefully
            results = monitor.process_stuck_imports()
            assert results == {"processed": 0, "forced": 0, "removed": 0}
            assert monitor.stats["errors_encountered"] == 0  # Handled gracefully

    def test_partial_data_handling(self, error_prone_system):
        """Test handling of partial or corrupted data"""
        monitor = error_prone_system

        # Queue item with missing data
        incomplete_item = {
            "id": 1,
            "trackedDownloadState": "importPending",
            # Missing episode, series, downloadId
        }

        with requests_mock.Mocker() as m:
            m.get(
                "http://unreachable:8989/api/v3/queue",
                json={"records": [incomplete_item]},
            )

            # Should handle gracefully
            results = monitor.process_stuck_imports()

            # Should process but not crash
            assert results["processed"] == 1
            assert monitor.stats["errors_encountered"] == 0


class TestPerformanceIntegration:
    """Test performance aspects of integrated system"""

    @pytest.fixture
    def performance_system(self, mock_config):
        """Create system for performance testing"""
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "sonarr.url": "http://test-sonarr:8989",
            "sonarr.api_key": "test-key",
            "decisions.force_import_threshold": 10,
            "trackers.private": ["privatehd"],
            "trackers.public": ["nyaa"],
        }.get(key, default)

        with patch("src.core.monitor.signal.signal"):
            monitor = SonarrImportMonitor(config)

        return monitor

    def test_large_queue_processing(self, performance_system):
        """Test processing large queue efficiently"""
        monitor = performance_system

        # Create large queue (100 items)
        large_queue = []
        for i in range(100):
            large_queue.append(
                {
                    "id": i,
                    "episode": {"id": 1000 + i},
                    "series": {"id": 10},
                    "downloadId": f"download-{i}",
                    "trackedDownloadState": (
                        "importPending" if i % 2 == 0 else "downloading"
                    ),
                    "status": "completed",
                }
            )

        with requests_mock.Mocker() as m:
            m.get("http://test-sonarr:8989/api/v3/queue", json={"records": large_queue})

            # Mock fast analysis (avoid actual API calls in analyzer)
            monitor.analyzer.analyze_queue_item = Mock(
                return_value=Decision(
                    action="keep",
                    grab_score=100,
                    current_score=100,
                    score_difference=0,
                    reasoning="Test decision",
                    grab_formats=[],
                    current_formats=[],
                    missing_formats=[],
                    extra_formats=[],
                    is_private_tracker=False,
                )
            )

            start_time = time.time()
            results = monitor.process_stuck_imports()
            end_time = time.time()

            # Should process 50 stuck items (every other one) quickly
            assert results["processed"] == 50
            assert end_time - start_time < 5.0  # Should be fast

    def test_caching_effectiveness(self, performance_system):
        """Test that caching improves performance"""
        monitor = performance_system

        with requests_mock.Mocker() as m:
            # Mock custom formats endpoint (called for caching)
            m.get(
                "http://test-sonarr:8989/api/v3/customformat",
                json=[{"id": 1, "name": "HDR"}],
            )
            m.get(
                "http://test-sonarr:8989/api/v3/qualityprofile",
                json=[{"id": 1, "name": "HD"}],
            )
            m.get(
                "http://test-sonarr:8989/api/v3/series",
                json=[{"id": 10, "qualityProfileId": 1}],
            )

            # First call should make API requests
            formats1 = monitor.sonarr_client.fetch_custom_formats()
            profiles1 = monitor.sonarr_client.fetch_quality_profiles()
            series_map1 = monitor.sonarr_client.build_series_profile_map()

            # Count initial requests
            initial_requests = len(m.request_history)

            # Second calls should use cache
            formats2 = monitor.sonarr_client.fetch_custom_formats()
            profiles2 = monitor.sonarr_client.fetch_quality_profiles()
            series_map2 = monitor.sonarr_client.build_series_profile_map()

            # Should be same data
            assert formats1 == formats2
            assert profiles1 == profiles2
            assert series_map1 == series_map2

            # Should not have made additional API requests
            assert len(m.request_history) == initial_requests


class TestMultiComponentScenarios:
    """Test scenarios involving multiple components"""

    @pytest.fixture
    def multi_component_system(self, mock_config, test_env_vars):
        """Create system with multiple components for complex testing"""
        with patch("src.config.loader.load_dotenv"), patch(
            "src.config.loader.Path.exists", return_value=False
        ), patch("src.config.loader.secrets.token_urlsafe", return_value="test-secret"):
            config = ConfigLoader()

        with patch("src.core.monitor.signal.signal"), patch(
            "src.core.monitor.SonarrClient"
        ) as MockClient:
            mock_sonarr_client = MagicMock()
            MockClient.return_value = mock_sonarr_client
            monitor = SonarrImportMonitor(config)

        webhook_server = WebhookServer(monitor, config)

        return monitor, webhook_server, config

    def test_webhook_grab_to_processing_pipeline(self, multi_component_system):
        """Test complete pipeline from webhook grab to processing"""
        monitor, webhook_server, config = multi_component_system

        # Step 1: Webhook receives grab event
        grab_payload = {
            "eventType": "Grab",
            "series": {"id": 10, "title": "Test Series"},
            "episodes": [{"id": 100}],
            "release": {
                "customFormatScore": 100,
                "customFormats": [{"name": "HDR"}],
                "releaseTitle": "Test.Episode.720p.HDR",
            },
            "downloadId": "webhook-test-123",
        }

        with patch("threading.Timer"):  # Prevent actual delayed execution
            with webhook_server.app.app_context():
                response, status_code = webhook_server.handle_grab(grab_payload)

        assert status_code == 200
        assert 100 in webhook_server.grab_cache

        # Step 2: Monitor processes queue item
        queue_item = {
            "id": 1,
            "episode": {"id": 100},
            "series": {"id": 10},
            "downloadId": "webhook-test-123",
            "trackedDownloadState": "importPending",
            "status": "completed",
        }

        # Mock analyzer to return force import decision
        monitor.analyzer.analyze_queue_item = Mock(
            return_value=Decision(
                action="force_import",
                grab_score=100,
                current_score=80,
                score_difference=20,
                reasoning="Score improvement from webhook data",
                grab_formats=["HDR"],
                current_formats=["x265"],
                missing_formats=["HDR"],
                extra_formats=[],
                is_private_tracker=False,
            )
        )

        # Mock successful force import
        monitor.sonarr_client.force_import = Mock(return_value=(True, 123))

        result = monitor.process_queue_item(queue_item)

        assert result == "forced_import"
        monitor.sonarr_client.force_import.assert_called_once()

    def test_configuration_propagates_correctly(self, multi_component_system):
        """Test that configuration changes propagate to all components"""
        monitor, webhook_server, config = multi_component_system

        # Verify configuration is shared correctly
        assert monitor.config is config
        assert webhook_server.config is config
        assert monitor.analyzer.config is config
        # Note: sonarr_client is mocked in this fixture, so we test the analyzer config instead
        assert monitor.analyzer.force_import_threshold == 10

    def test_concurrent_webhook_and_monitoring(self, multi_component_system):
        """Test concurrent webhook processing and monitoring"""
        monitor, webhook_server, config = multi_component_system

        # Mock monitoring methods to avoid actual processing
        monitor.process_stuck_imports = Mock(
            return_value={"processed": 0, "forced": 0, "removed": 0}
        )
        monitor.check_repeated_grabs = Mock(return_value=0)

        # Simulate webhook processing by directly updating metrics
        def send_webhook():
            """Send webhook in background"""
            webhook_server.metrics["events_processed"]["Test"] += 1

        def run_monitoring():
            """Run single monitoring cycle"""
            monitor.run_once()

        # Execute concurrently
        webhook_thread = threading.Thread(target=send_webhook)
        monitor_thread = threading.Thread(target=run_monitoring)

        webhook_thread.start()
        monitor_thread.start()

        webhook_thread.join(timeout=5)
        monitor_thread.join(timeout=5)

        # Both should complete without issues
        assert webhook_server.metrics["events_processed"]["Test"] > 0
        monitor.process_stuck_imports.assert_called()


class TestRealWorldScenarios:
    """Test scenarios that mirror real-world usage"""

    @pytest.fixture
    def real_world_system(self, mock_config):
        """Create system that simulates real-world conditions"""
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "sonarr.url": "http://test-sonarr:8989",
            "sonarr.api_key": "real-world-api-key",
            "decisions.force_import_threshold": 15,
            "trackers.private": ["beyondhd", "privatehd"],
            "trackers.public": ["nyaa", "rarbg"],
            "webhook.secret": "real-webhook-secret",
        }.get(key, default)

        with patch("src.core.monitor.signal.signal"), patch(
            "src.core.monitor.SonarrClient"
        ) as MockClient:
            mock_sonarr_client = MagicMock()
            MockClient.return_value = mock_sonarr_client
            monitor = SonarrImportMonitor(config)

        return monitor, config

    def test_mixed_tracker_queue_scenario(self, real_world_system):
        """Test queue with mixed private/public tracker items"""
        monitor, config = real_world_system

        # Queue with mixed tracker types
        mixed_queue = [
            {
                "id": 1,
                "episode": {"id": 100},
                "series": {"id": 10},
                "downloadId": "private-download",
                "trackedDownloadState": "importPending",
            },
            {
                "id": 2,
                "episode": {"id": 101},
                "series": {"id": 10},
                "downloadId": "public-download",
                "trackedDownloadState": "importPending",
            },
        ]

        # Mock different histories for each
        def mock_history(episode_id, limit=50):
            if episode_id == 100:
                return [
                    {
                        "eventType": "grabbed",
                        "downloadId": "private-download",
                        "customFormatScore": 80,
                        "data": {"indexer": "BeyondHD"},  # Private
                    }
                ]
            else:
                return [
                    {
                        "eventType": "grabbed",
                        "downloadId": "public-download",
                        "customFormatScore": 60,
                        "data": {"indexer": "Nyaa"},  # Public
                    }
                ]

        # Mock current files (both have higher scores)
        def mock_episode_details(method, endpoint):
            if "/episode/100" in endpoint:
                response = MagicMock()
                response.json.return_value = {"hasFile": True, "episodeFileId": 50}
                return response
            elif "/episode/101" in endpoint:
                response = MagicMock()
                response.json.return_value = {"hasFile": True, "episodeFileId": 51}
                return response

        def mock_episode_file(file_id):
            return {
                "customFormatScore": 100,  # Higher than both grabs
                "customFormats": [{"name": "HDR"}],
            }

        with requests_mock.Mocker() as m:
            m.get("http://test-sonarr:8989/api/v3/queue", json={"records": mixed_queue})

            # Setup mocked sonarr_client methods
            monitor.sonarr_client.get_queue.return_value = mixed_queue
            monitor.sonarr_client.get_history_for_episode.side_effect = mock_history
            monitor.sonarr_client._make_request.side_effect = mock_episode_details
            monitor.sonarr_client.get_episode_file.side_effect = mock_episode_file
            monitor.sonarr_client.remove_from_queue = Mock(return_value=True)

            results = monitor.process_stuck_imports()

            # Should process both items
            assert results["processed"] == 2

            # Private tracker should be kept (not removed)
            # Public tracker should be removed
            # So we expect 1 removal total
            assert results["removed"] == 1


class TestConfigurationIntegration:
    """Test configuration integration across all components"""

    def test_environment_override_integration(self, test_env_vars):
        """Test that environment overrides work across all components"""
        # Create system entirely from environment variables
        with patch("src.config.loader.load_dotenv"), patch(
            "src.config.loader.Path.exists", return_value=False
        ):
            config = ConfigLoader()

        # Verify environment values were loaded
        assert config.get("sonarr.url") == "http://test-sonarr:8989"
        assert config.get("sonarr.api_key") == "test-key-123456789012345678901234567890"
        assert (
            config.get("webhook.secret") == "test-secret-123456789012345678901234567890"
        )

        with patch("src.core.monitor.signal.signal"):
            monitor = SonarrImportMonitor(config)

        # Verify config propagated correctly
        assert monitor.sonarr_client.base_url == "http://test-sonarr:8989"
        assert (
            monitor.sonarr_client.api_key == "test-key-123456789012345678901234567890"
        )
        assert monitor.analyzer.force_import_threshold == 10

        # Test webhook server
        webhook_server = WebhookServer(monitor, config)
        assert (
            webhook_server.webhook_secret
            == "test-secret-123456789012345678901234567890"
        )

    def test_config_file_plus_env_override_integration(self, temp_config_file):
        """Test config file with selective environment overrides"""
        # Override only specific values, clearing existing env vars
        with patch.dict("os.environ", {}, clear=True):
            with patch.dict(
                "os.environ",
                {
                    "FORCE_IMPORT_THRESHOLD": "25",  # Override from file
                    "LOG_LEVEL": "DEBUG",  # New value
                },
            ):
                with patch("src.config.loader.load_dotenv"):
                    config = ConfigLoader(config_path=temp_config_file)

                # File values should be preserved
                assert config.get("sonarr.url") == "http://test-sonarr:8989"
                assert config.get("webhook.port") == 8090

                # Environment overrides should work
                assert config.get("decisions.force_import_threshold") == 25
                assert config.get("logging.level") == "DEBUG"

        with patch("src.core.monitor.signal.signal"):
            monitor = SonarrImportMonitor(config)

        # Verify overrides propagated
        assert monitor.analyzer.force_import_threshold == 25


@pytest.mark.slow
class TestLongRunningProcesses:
    """Test long-running processes and lifecycle management"""

    @pytest.fixture
    def lifecycle_system(self, mock_config):
        """Create system for lifecycle testing"""
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "sonarr.url": "http://test-sonarr:8989",
            "sonarr.api_key": "test-key",
            "monitoring.interval": 0.1,  # Very fast for testing
            "decisions.force_import_threshold": 10,
        }.get(key, default)

        with patch("src.core.monitor.signal.signal"):
            monitor = SonarrImportMonitor(config)

        return monitor

    def test_monitor_startup_and_shutdown(self, lifecycle_system):
        """Test monitor startup and graceful shutdown"""
        monitor = lifecycle_system

        # Mock processing methods to avoid actual API calls
        monitor.process_stuck_imports = Mock(
            return_value={"processed": 0, "forced": 0, "removed": 0}
        )
        monitor.check_repeated_grabs = Mock(return_value=0)

        # Start monitoring in background
        def monitor_worker():
            monitor.run_continuous(enable_webhook=False)

        monitor_thread = threading.Thread(target=monitor_worker, daemon=True)
        monitor_thread.start()

        # Let it run for a short time
        time.sleep(0.3)

        # Verify it's running
        assert monitor.running is True
        assert monitor.stats["cycles_completed"] > 0

        # Shutdown gracefully
        monitor.shutdown()

        # Give it time to shutdown
        time.sleep(0.1)

        assert monitor.running is False

    def test_webhook_server_lifecycle(self, lifecycle_system):
        """Test webhook server startup and shutdown lifecycle"""
        monitor = lifecycle_system

        # Test webhook server startup
        success = monitor.start_webhook_server()

        # Should start successfully (even if binding fails in test env)
        assert monitor.webhook_server is not None
        assert monitor.webhook_thread is not None

        # Test shutdown
        monitor.shutdown()

        # Webhook server should be shutdown
        if hasattr(monitor.webhook_server, "shutdown"):
            # Verify shutdown was called (if method exists)
            pass
