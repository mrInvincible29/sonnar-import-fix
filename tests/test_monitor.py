"""
Unit tests for SonarrImportMonitor main monitoring logic.
"""

import threading
from datetime import datetime
from unittest.mock import MagicMock, Mock, call, patch

import pytest

from src.core.analyzer import Decision
from src.core.monitor import SonarrImportMonitor


class TestSonarrImportMonitor:
    """Test SonarrImportMonitor class"""

    @pytest.fixture
    def monitor(self, mock_config):
        """Create monitor instance for testing"""
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "sonarr.url": mock_config["sonarr"]["url"],
            "monitoring.interval": 60,
            "monitoring.detect_repeated_grabs": True,
            "decisions.force_import_threshold": mock_config["decisions"][
                "force_import_threshold"
            ],
            "webhook.enabled": True,
            "webhook.host": "0.0.0.0",
            "webhook.port": 8090,
        }.get(key, default)

        with patch("src.core.monitor.SonarrClient"), patch(
            "src.core.monitor.ScoreAnalyzer"
        ), patch("src.core.monitor.signal.signal"):
            monitor = SonarrImportMonitor(config)

        return monitor

    def test_init(self, monitor, mock_config):
        """Test monitor initialization"""
        assert monitor.dry_run is False
        assert monitor.running is False
        assert monitor.webhook_server is None
        assert monitor.webhook_thread is None
        assert "start_time" in monitor.stats
        assert monitor.stats["cycles_completed"] == 0

    @patch("src.core.monitor.signal")
    def test_signal_handlers_setup(self, mock_signal, mock_config):
        """Test that signal handlers are set up correctly"""
        config = MagicMock()
        config.get.return_value = None

        with patch("src.core.monitor.SonarrClient"), patch(
            "src.core.monitor.ScoreAnalyzer"
        ):
            SonarrImportMonitor(config)

        # Verify signal handlers were set
        assert mock_signal.signal.call_count == 2


class TestConfigurationTesting:
    """Test configuration and connectivity testing"""

    @pytest.fixture
    def monitor(self, mock_config):
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "webhook.enabled": True,
            "webhook.secret": "test-secret",
        }.get(key, default)

        with patch("src.core.monitor.SonarrClient"), patch(
            "src.core.monitor.ScoreAnalyzer"
        ), patch("src.core.monitor.signal.signal"):
            monitor = SonarrImportMonitor(config)

        return monitor

    def test_test_configuration_success(self, monitor):
        """Test successful configuration test"""
        # Mock successful responses
        monitor.sonarr_client.test_connection.return_value = True
        monitor.sonarr_client.fetch_custom_formats.return_value = {"1": {"name": "HDR"}}
        monitor.sonarr_client.fetch_quality_profiles.return_value = {
            "1": {"name": "HD"}
        }
        monitor.sonarr_client.build_series_profile_map.return_value = {"10": 1}

        result = monitor.test_configuration()

        assert result is True
        monitor.sonarr_client.test_connection.assert_called_once()

    def test_test_configuration_connection_failure(self, monitor):
        """Test configuration test with connection failure"""
        monitor.sonarr_client.test_connection.return_value = False

        result = monitor.test_configuration()
        assert result is False

    def test_test_configuration_api_failure(self, monitor):
        """Test configuration test with API failure"""
        monitor.sonarr_client.test_connection.return_value = True
        monitor.sonarr_client.fetch_custom_formats.side_effect = Exception("API Error")

        result = monitor.test_configuration()
        assert result is False

    def test_test_configuration_webhook_warnings(self, monitor):
        """Test configuration test webhook warnings"""
        monitor.config.get.side_effect = lambda key, default=None: {
            "webhook.enabled": True,
            "webhook.secret": None,  # No secret configured
        }.get(key, default)

        monitor.sonarr_client.test_connection.return_value = True
        monitor.sonarr_client.fetch_custom_formats.return_value = {}
        monitor.sonarr_client.fetch_quality_profiles.return_value = {}
        monitor.sonarr_client.build_series_profile_map.return_value = {}

        result = monitor.test_configuration()
        assert result is True  # Should still pass but with warning


class TestWebhookServerManagement:
    """Test webhook server management"""

    @pytest.fixture
    def monitor(self, mock_config):
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "webhook.host": "0.0.0.0",
            "webhook.port": 8090,
        }.get(key, default)

        with patch("src.core.monitor.SonarrClient"), patch(
            "src.core.monitor.ScoreAnalyzer"
        ), patch("src.core.monitor.signal.signal"):
            monitor = SonarrImportMonitor(config)

        return monitor

    @patch("src.core.monitor.WebhookServer")
    @patch("threading.Thread")
    @patch("time.sleep")
    def test_start_webhook_server_success(
        self, mock_sleep, mock_thread, mock_webhook_class, monitor
    ):
        """Test successful webhook server startup"""
        mock_webhook_instance = MagicMock()
        mock_webhook_class.return_value = mock_webhook_instance

        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        result = monitor.start_webhook_server()

        assert result is True
        assert monitor.webhook_server is not None
        mock_webhook_class.assert_called_once_with(monitor, monitor.config)
        mock_thread_instance.start.assert_called_once()

    def test_start_webhook_server_already_running(self, monitor):
        """Test starting webhook server when already running"""
        monitor.webhook_server = MagicMock()

        result = monitor.start_webhook_server()
        assert result is True

    @patch("src.core.monitor.WebhookServer")
    def test_start_webhook_server_error(self, mock_webhook_class, monitor):
        """Test webhook server startup error"""
        mock_webhook_class.side_effect = Exception("Server error")

        result = monitor.start_webhook_server()
        assert result is False


class TestStuckImportProcessing:
    """Test stuck import identification and processing"""

    @pytest.fixture
    def monitor_with_mocks(self, mock_config):
        config = MagicMock()
        config.get.return_value = None

        with patch("src.core.monitor.SonarrClient"), patch(
            "src.core.monitor.ScoreAnalyzer"
        ), patch("src.core.monitor.signal.signal"):
            monitor = SonarrImportMonitor(config)

        return monitor

    def test_identify_stuck_items_import_pending(self, monitor_with_mocks):
        """Test identifying items with importPending state"""
        queue = [
            {"id": 1, "trackedDownloadState": "importPending", "episode": {"id": 100}},
            {"id": 2, "trackedDownloadState": "importing", "episode": {"id": 101}},
        ]

        stuck_items = monitor_with_mocks._identify_stuck_items(queue)

        assert len(stuck_items) == 1
        assert stuck_items[0]["id"] == 1

    def test_identify_stuck_items_completed_with_warning(self, monitor_with_mocks):
        """Test identifying completed items with warning status"""
        queue = [
            {
                "id": 1,
                "status": "completed",
                "trackedDownloadStatus": "warning",
                "episode": {"id": 100},
            }
        ]

        stuck_items = monitor_with_mocks._identify_stuck_items(queue)

        assert len(stuck_items) == 1
        assert stuck_items[0]["id"] == 1

    def test_identify_stuck_items_with_error_messages(self, monitor_with_mocks):
        """Test identifying items with error messages"""
        queue = [
            {
                "id": 1,
                "statusMessages": [
                    {"messages": ["File already exists", "Other message"]}
                ],
                "episode": {"id": 100},
            },
            {
                "id": 2,
                "statusMessages": [{"messages": ["Normal message"]}],
                "episode": {"id": 101},
            },
        ]

        stuck_items = monitor_with_mocks._identify_stuck_items(queue)

        assert len(stuck_items) == 1
        assert stuck_items[0]["id"] == 1

    def test_process_stuck_imports_empty_queue(self, monitor_with_mocks):
        """Test processing stuck imports with empty queue"""
        monitor_with_mocks.sonarr_client.get_queue.return_value = []

        results = monitor_with_mocks.process_stuck_imports()

        assert results == {"processed": 0, "forced": 0, "removed": 0}

    def test_process_stuck_imports_no_stuck_items(self, monitor_with_mocks):
        """Test processing when no items are stuck"""
        queue = [
            {"id": 1, "status": "downloading", "trackedDownloadState": "downloading"}
        ]

        monitor_with_mocks.sonarr_client.get_queue.return_value = queue

        results = monitor_with_mocks.process_stuck_imports()

        assert results == {"processed": 0, "forced": 0, "removed": 0}

    def test_process_stuck_imports_with_actions(self, monitor_with_mocks):
        """Test processing stuck imports with various actions"""
        stuck_queue = [
            {"id": 1, "trackedDownloadState": "importPending", "episode": {"id": 100}},
            {"id": 2, "trackedDownloadState": "importPending", "episode": {"id": 101}},
        ]

        monitor_with_mocks.sonarr_client.get_queue.return_value = stuck_queue

        # Mock the _identify_stuck_items method properly
        with patch.object(
            monitor_with_mocks, "_identify_stuck_items", return_value=stuck_queue
        ):
            # Mock process_queue_item returns
            with patch.object(
                monitor_with_mocks,
                "process_queue_item",
                side_effect=["forced_import", "removed"],
            ):
                results = monitor_with_mocks.process_stuck_imports()

                assert results == {"processed": 2, "forced": 1, "removed": 1}


class TestQueueItemProcessing:
    """Test individual queue item processing"""

    @pytest.fixture
    def monitor_with_mocks(self, mock_config):
        config = MagicMock()
        config.get.return_value = None

        with patch("src.core.monitor.SonarrClient"), patch(
            "src.core.monitor.ScoreAnalyzer"
        ), patch("src.core.monitor.signal.signal"):
            monitor = SonarrImportMonitor(config)

        return monitor

    def test_process_queue_item_force_import(
        self, monitor_with_mocks, mock_sonarr_queue_item
    ):
        """Test processing queue item with force import decision"""
        decision = Decision(
            action="force_import",
            grab_score=100,
            current_score=80,
            score_difference=20,
            reasoning="Score improvement",
            grab_formats=["HDR"],
            current_formats=["x265"],
            missing_formats=["HDR"],
            extra_formats=[],
            is_private_tracker=True,
        )

        with patch.object(monitor_with_mocks, "analyzer") as mock_analyzer:
            with patch.object(
                monitor_with_mocks, "_execute_force_import", return_value=True
            ) as mock_execute:
                with patch.object(monitor_with_mocks, "stats", {"forced_imports": 0}):
                    mock_analyzer.analyze_queue_item.return_value = decision

                    result = monitor_with_mocks.process_queue_item(
                        mock_sonarr_queue_item
                    )

                    assert result == "forced_import"
                    assert monitor_with_mocks.stats["forced_imports"] == 1
                    mock_execute.assert_called_once_with(mock_sonarr_queue_item)

    def test_process_queue_item_remove(
        self, monitor_with_mocks, mock_sonarr_queue_item
    ):
        """Test processing queue item with remove decision"""
        decision = Decision(
            action="remove",
            grab_score=50,
            current_score=100,
            score_difference=-50,
            reasoning="Public tracker with lower score",
            grab_formats=["x265"],
            current_formats=["HDR"],
            missing_formats=[],
            extra_formats=["HDR"],
            is_private_tracker=False,
        )

        with patch.object(monitor_with_mocks, "analyzer") as mock_analyzer:
            with patch.object(
                monitor_with_mocks, "_execute_removal", return_value=True
            ) as mock_execute:
                with patch.object(monitor_with_mocks, "stats", {"items_removed": 0}):
                    mock_analyzer.analyze_queue_item.return_value = decision

                    result = monitor_with_mocks.process_queue_item(
                        mock_sonarr_queue_item
                    )

                    assert result == "removed"
                    assert monitor_with_mocks.stats["items_removed"] == 1
                    mock_execute.assert_called_once_with(mock_sonarr_queue_item)

    def test_process_queue_item_keep(self, monitor_with_mocks, mock_sonarr_queue_item):
        """Test processing queue item with keep decision"""
        decision = Decision(
            action="keep",
            grab_score=80,
            current_score=100,
            score_difference=-20,
            reasoning="Private tracker protection",
            grab_formats=["x265"],
            current_formats=["HDR"],
            missing_formats=[],
            extra_formats=[],
            is_private_tracker=True,
        )

        with patch.object(monitor_with_mocks, "analyzer") as mock_analyzer:
            mock_analyzer.analyze_queue_item.return_value = decision

            result = monitor_with_mocks.process_queue_item(mock_sonarr_queue_item)

            assert result == "kept"
            # Should not call execution methods for keep action

    def test_process_queue_item_error(self, monitor_with_mocks, mock_sonarr_queue_item):
        """Test processing queue item with error"""
        with patch.object(monitor_with_mocks, "analyzer") as mock_analyzer:
            with patch.object(monitor_with_mocks, "stats", {"errors_encountered": 0}):
                mock_analyzer.analyze_queue_item.side_effect = Exception(
                    "Analysis error"
                )

                result = monitor_with_mocks.process_queue_item(mock_sonarr_queue_item)

                assert result == "error"
                assert monitor_with_mocks.stats["errors_encountered"] == 1


class TestActionExecution:
    """Test action execution methods"""

    @pytest.fixture
    def monitor(self, mock_config):
        config = MagicMock()
        config.get.return_value = None

        with patch("src.core.monitor.SonarrClient"), patch(
            "src.core.monitor.ScoreAnalyzer"
        ), patch("src.core.monitor.signal.signal"):
            monitor = SonarrImportMonitor(config)

        return monitor

    def test_execute_force_import_success(self, monitor, mock_sonarr_queue_item):
        """Test successful force import execution"""
        monitor.sonarr_client.force_import.return_value = (True, 123)
        monitor.sonarr_client.cleanup_post_import_queue_item.return_value = True

        result = monitor._execute_force_import(mock_sonarr_queue_item)

        assert result is True
        monitor.sonarr_client.force_import.assert_called_once_with(
            "abc123def456", mock_sonarr_queue_item["quality"]
        )

    def test_execute_force_import_missing_data(self, monitor):
        """Test force import with missing data"""
        queue_item = {
            "id": 1,
            "downloadId": None,  # Missing download ID
            "episode": {"id": 100},
        }

        result = monitor._execute_force_import(queue_item)
        assert result is False

    def test_execute_force_import_dry_run(self, monitor, mock_sonarr_queue_item):
        """Test force import in dry run mode"""
        monitor.dry_run = True

        result = monitor._execute_force_import(mock_sonarr_queue_item)

        assert result is True
        # Should not call actual API
        monitor.sonarr_client.force_import.assert_not_called()

    def test_execute_removal_success(self, monitor, mock_sonarr_queue_item):
        """Test successful removal execution"""
        monitor.sonarr_client.remove_from_queue.return_value = True

        result = monitor._execute_removal(mock_sonarr_queue_item)

        assert result is True
        monitor.sonarr_client.remove_from_queue.assert_called_once_with(
            1, remove_from_client=True, blocklist=False
        )

    def test_execute_removal_missing_id(self, monitor):
        """Test removal with missing queue ID"""
        queue_item = {"downloadId": "test123"}  # Missing ID

        result = monitor._execute_removal(queue_item)
        assert result is False

    def test_execute_removal_dry_run(self, monitor, mock_sonarr_queue_item):
        """Test removal in dry run mode"""
        monitor.dry_run = True

        result = monitor._execute_removal(mock_sonarr_queue_item)

        assert result is True
        monitor.sonarr_client.remove_from_queue.assert_not_called()


class TestRepeatedGrabDetection:
    """Test repeated grab detection functionality"""

    @pytest.fixture
    def monitor(self, mock_config):
        config = MagicMock()
        config.get.return_value = None

        with patch("src.core.monitor.SonarrClient"), patch(
            "src.core.monitor.ScoreAnalyzer"
        ), patch("src.core.monitor.signal.signal"):
            monitor = SonarrImportMonitor(config)

        return monitor

    def test_check_repeated_grabs_no_issues(self, monitor):
        """Test repeated grab check with no issues"""
        # Mock API response with normal history
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "records": [
                {"eventType": "grabbed", "episode": {"id": 100}},
                {"eventType": "downloadFolderImported", "episode": {"id": 100}},
            ]
        }
        monitor.sonarr_client._make_request.return_value = mock_response
        monitor.analyzer.detect_repeated_grabs.return_value = []

        result = monitor.check_repeated_grabs()

        assert result == 0

    def test_check_repeated_grabs_with_issues(self, monitor):
        """Test repeated grab check with issues found"""
        # Mock API response with repeated grabs
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "records": [
                {"eventType": "grabbed", "episode": {"id": 100}},
                {"eventType": "grabbed", "episode": {"id": 100}},
                {"eventType": "grabbed", "episode": {"id": 101}},
                {"eventType": "grabbed", "episode": {"id": 101}},
            ]
        }
        monitor.sonarr_client._make_request.return_value = mock_response

        # Mock analyzer to find issues
        monitor.analyzer.detect_repeated_grabs.side_effect = [
            [{"downloadId": "grab1"}],  # Episode 100 has issues
            [],  # Episode 101 has no issues
        ]

        # Mock check_episode_queue
        monitor.check_episode_queue = MagicMock()

        result = monitor.check_repeated_grabs()

        assert result == 1  # Only episode 100 had issues
        monitor.check_episode_queue.assert_called_once_with(100)

    def test_check_repeated_grabs_api_error(self, monitor):
        """Test repeated grab check with API error"""
        monitor.sonarr_client._make_request.side_effect = Exception("API Error")

        result = monitor.check_repeated_grabs()
        assert result == 0

    def test_check_episode_queue_found(self, monitor):
        """Test checking specific episode in queue"""
        queue = [{"id": 1, "episode": {"id": 100}}, {"id": 2, "episode": {"id": 101}}]

        monitor.sonarr_client.get_queue.return_value = queue
        monitor.process_queue_item = MagicMock()

        monitor.check_episode_queue(100)

        monitor.process_queue_item.assert_called_once_with(
            {"id": 1, "episode": {"id": 100}}
        )

    def test_check_episode_queue_not_found(self, monitor):
        """Test checking episode not in queue"""
        queue = [{"id": 1, "episode": {"id": 200}}]

        monitor.sonarr_client.get_queue.return_value = queue
        monitor.process_queue_item = MagicMock()

        monitor.check_episode_queue(100)

        monitor.process_queue_item.assert_not_called()


class TestSpecificEpisodeTesting:
    """Test specific episode testing functionality"""

    @pytest.fixture
    def monitor(self, mock_config):
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "decisions.force_import_threshold": 10
        }.get(key, default)

        with patch("src.core.monitor.SonarrClient"), patch(
            "src.core.monitor.ScoreAnalyzer"
        ), patch("src.core.monitor.signal.signal"):
            monitor = SonarrImportMonitor(config)

        return monitor

    def test_test_specific_episode_found(self, monitor):
        """Test testing specific episode that exists"""
        # Mock series lookup
        mock_series = {"id": 10, "title": "Breaking Bad"}
        monitor.sonarr_client.get_series_by_title.return_value = mock_series

        # Mock episode lookup
        mock_episode = {"id": 100, "hasFile": True, "episodeFileId": 50}
        monitor.sonarr_client.get_episode_info.return_value = mock_episode

        # Mock analyzer
        monitor.analyzer.get_current_file_details.return_value = (80, ["x265"])

        # Mock queue check
        monitor.sonarr_client.get_queue.return_value = []

        # Mock history
        monitor.sonarr_client.get_history_for_episode.return_value = [
            {"eventType": "grabbed", "customFormatScore": 100}
        ]
        monitor._show_history_analysis = MagicMock()

        # Should not raise exception
        monitor.test_specific_episode("Breaking Bad", 1, 1)

        monitor.sonarr_client.get_series_by_title.assert_called_once_with(
            "Breaking Bad"
        )
        monitor.sonarr_client.get_episode_info.assert_called_once_with(10, 1, 1)

    def test_test_specific_episode_series_not_found(self, monitor):
        """Test testing episode when series is not found"""
        monitor.sonarr_client.get_series_by_title.return_value = None

        # Should not raise exception
        monitor.test_specific_episode("Nonexistent Series", 1, 1)

        # Should stop after series lookup fails
        monitor.sonarr_client.get_episode_info.assert_not_called()

    def test_test_specific_episode_episode_not_found(self, monitor):
        """Test testing episode that doesn't exist"""
        monitor.sonarr_client.get_series_by_title.return_value = {
            "id": 10,
            "title": "Test Series",
        }
        monitor.sonarr_client.get_episode_info.return_value = None

        # Should not raise exception
        monitor.test_specific_episode("Test Series", 99, 99)

        # Should stop after episode lookup fails
        monitor.analyzer.get_current_file_details.assert_not_called()


class TestMonitoringLoop:
    """Test continuous monitoring functionality"""

    @pytest.fixture
    def monitor(self, mock_config):
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "monitoring.interval": 1,  # Short interval for testing
            "monitoring.detect_repeated_grabs": True,
        }.get(key, default)

        with patch("src.core.monitor.SonarrClient"), patch(
            "src.core.monitor.ScoreAnalyzer"
        ), patch("src.core.monitor.signal.signal"):
            monitor = SonarrImportMonitor(config)

        return monitor

    def test_run_once_success(self, monitor):
        """Test successful single run"""
        with patch.object(
            monitor,
            "process_stuck_imports",
            return_value={"processed": 1, "forced": 0, "removed": 1},
        ) as mock_process:
            with patch.object(
                monitor, "check_repeated_grabs", return_value=0
            ) as mock_check:
                with patch.object(monitor, "_log_statistics") as mock_log:
                    result = monitor.run_once()

                    assert result is True
                    mock_process.assert_called_once()
                    mock_check.assert_called_once()
                    mock_log.assert_called_once()

    def test_run_once_error(self, monitor):
        """Test single run with error"""
        with patch.object(
            monitor, "process_stuck_imports", side_effect=Exception("Processing error")
        ):
            result = monitor.run_once()
            assert result is False

    @patch("time.sleep")
    def test_run_continuous_single_cycle(self, mock_sleep, monitor):
        """Test continuous run with single cycle"""
        with patch.object(
            monitor,
            "process_stuck_imports",
            return_value={"processed": 0, "forced": 0, "removed": 0},
        ):
            with patch.object(monitor, "check_repeated_grabs", return_value=0):
                with patch.object(monitor, "_log_statistics"):
                    with patch.object(monitor, "stats", {"cycles_completed": 0}):
                        # Stop after first cycle
                        def stop_after_first(*args):
                            monitor.running = False

                        mock_sleep.side_effect = stop_after_first

                        result = monitor.run_continuous(enable_webhook=False)

                        assert result is True
                        assert monitor.stats["cycles_completed"] == 1

    @patch("time.sleep")
    def test_run_continuous_with_webhook(self, mock_sleep, monitor):
        """Test continuous run with webhook enabled"""
        with patch.object(
            monitor, "start_webhook_server", return_value=True
        ) as mock_start:
            with patch.object(
                monitor,
                "process_stuck_imports",
                return_value={"processed": 0, "forced": 0, "removed": 0},
            ):
                # Stop after first cycle
                def stop_after_first(*args):
                    monitor.running = False

                mock_sleep.side_effect = stop_after_first

                result = monitor.run_continuous(enable_webhook=True)

                assert result is True
                mock_start.assert_called_once()

    @patch("time.sleep")
    def test_run_continuous_webhook_startup_failure(self, mock_sleep, monitor):
        """Test continuous run with webhook startup failure"""
        with patch.object(monitor, "start_webhook_server", return_value=False):
            result = monitor.run_continuous(enable_webhook=True)

            assert result is False


class TestStatistics:
    """Test statistics tracking and logging"""

    @pytest.fixture
    def monitor(self, mock_config):
        config = MagicMock()
        config.get.return_value = None

        with patch("src.core.monitor.SonarrClient"), patch(
            "src.core.monitor.ScoreAnalyzer"
        ), patch("src.core.monitor.signal.signal"):
            monitor = SonarrImportMonitor(config)

        return monitor

    @patch("src.core.monitor.logger")
    def test_log_statistics(self, mock_logger, monitor):
        """Test statistics logging"""
        # Set some statistics
        monitor.stats.update(
            {
                "cycles_completed": 5,
                "items_processed": 10,
                "forced_imports": 3,
                "items_removed": 2,
                "errors_encountered": 1,
            }
        )

        monitor._log_statistics()

        # Verify logger was called with statistics
        call_args = [call.args[0] for call in mock_logger.info.call_args_list]

        # Check that all key statistics are logged
        logged_text = " ".join(call_args)
        assert "Statistics:" in logged_text
        assert "Cycles completed: 5" in logged_text
        assert "Items processed: 10" in logged_text
        assert "Forced imports: 3" in logged_text
        assert "Items removed: 2" in logged_text
        assert "Errors encountered: 1" in logged_text


class TestShutdown:
    """Test shutdown functionality"""

    @pytest.fixture
    def monitor(self, mock_config):
        config = MagicMock()
        config.get.return_value = None

        with patch("src.core.monitor.SonarrClient"), patch(
            "src.core.monitor.ScoreAnalyzer"
        ), patch("src.core.monitor.signal.signal"):
            monitor = SonarrImportMonitor(config)

        return monitor

    def test_shutdown_clean(self, monitor):
        """Test clean shutdown without webhook server"""
        monitor.shutdown()

        assert monitor.running is False
        monitor.sonarr_client.clear_cache.assert_called_once()

    def test_shutdown_with_webhook(self, monitor):
        """Test shutdown with active webhook server"""
        mock_webhook = MagicMock()
        monitor.webhook_server = mock_webhook

        monitor.shutdown()

        assert monitor.running is False
        mock_webhook.shutdown.assert_called_once()
        monitor.sonarr_client.clear_cache.assert_called_once()

    def test_shutdown_webhook_error(self, monitor):
        """Test shutdown when webhook server throws error"""
        mock_webhook = MagicMock()
        mock_webhook.shutdown.side_effect = Exception("Shutdown error")
        monitor.webhook_server = mock_webhook

        # Should not raise exception
        monitor.shutdown()

        assert monitor.running is False
        monitor.sonarr_client.clear_cache.assert_called_once()

    @patch("src.core.monitor.logger")
    def test_signal_handler(self, mock_logger, monitor):
        """Test signal handler calls shutdown"""
        monitor.shutdown = MagicMock()

        # Simulate SIGTERM
        monitor._signal_handler(15, None)

        monitor.shutdown.assert_called_once()
        mock_logger.info.assert_called()


@pytest.mark.unit
class TestCompleteWorkflow:
    """Test complete workflow scenarios"""

    @pytest.fixture
    def monitor(self, mock_config):
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "decisions.force_import_threshold": 10,
            "monitoring.detect_repeated_grabs": True,
        }.get(key, default)

        with patch("src.core.monitor.SonarrClient"), patch(
            "src.core.monitor.ScoreAnalyzer"
        ), patch("src.core.monitor.signal.signal"):
            monitor = SonarrImportMonitor(config)

        return monitor

    def test_complete_processing_cycle(self, monitor, mock_sonarr_queue_item):
        """Test complete processing cycle from queue to action"""
        # Setup queue with stuck item
        stuck_items = [mock_sonarr_queue_item]

        with patch.object(monitor, "sonarr_client") as mock_client:
            with patch.object(
                monitor, "_identify_stuck_items", return_value=stuck_items
            ):
                with patch.object(monitor, "analyzer") as mock_analyzer:
                    # Update existing stats dictionary instead of replacing
                    monitor.stats.update({"forced_imports": 0, "items_processed": 0})

                    mock_client.get_queue.return_value = stuck_items

                    # Mock analysis decision
                    decision = Decision(
                        action="force_import",
                        grab_score=100,
                        current_score=80,
                        score_difference=20,
                        reasoning="Score improvement",
                        grab_formats=["HDR"],
                        current_formats=["x265"],
                        missing_formats=["HDR"],
                        extra_formats=[],
                        is_private_tracker=False,
                    )
                    mock_analyzer.analyze_queue_item.return_value = decision

                    # Mock successful execution
                    mock_client.force_import.return_value = (True, 123)

                    results = monitor.process_stuck_imports()

                    assert results == {"processed": 1, "forced": 1, "removed": 0}
                    assert monitor.stats["forced_imports"] == 1
                    # Note: items_processed is only updated in run_once(), not in process_stuck_imports()
