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

    def test_process_validated_mismatch_uses_the_selected_candidate(
        self, monitor_with_mocks, mock_sonarr_queue_item
    ):
        candidate = {
            "path": "/downloads/correct.mkv",
            "series": {"id": 10},
            "episodes": [{"id": 100}],
            "customFormatScore": 110,
        }
        decision = Decision(
            action="force_import",
            grab_score=110,
            current_score=100,
            score_difference=10,
            reasoning="Validated cross-series candidate",
            grab_formats=[],
            current_formats=[],
            missing_formats=[],
            extra_formats=[],
            is_private_tracker=False,
            validated_import_candidate=candidate,
        )
        with patch.object(
            monitor_with_mocks, "analyzer"
        ) as mock_analyzer, patch.object(
            monitor_with_mocks, "_execute_force_import", return_value=True
        ) as mock_execute:
            mock_analyzer.analyze_queue_item.return_value = decision

            assert (
                monitor_with_mocks.process_queue_item(mock_sonarr_queue_item)
                == "forced_import"
            )
            mock_execute.assert_called_once_with(mock_sonarr_queue_item, candidate)

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

        with patch.object(
            monitor_with_mocks, "analyzer"
        ) as mock_analyzer, patch.object(
            monitor_with_mocks, "_execute_keep_action", return_value=True
        ) as mock_execute:
            mock_analyzer.analyze_queue_item.return_value = decision

            result = monitor_with_mocks.process_queue_item(mock_sonarr_queue_item)

            assert result == "kept"
            mock_execute.assert_called_once_with(mock_sonarr_queue_item)

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

    @staticmethod
    def _grab_event(download_id="ABC123DEF456", series_id=10, episode_id=100):
        return {
            "eventType": "grabbed",
            "date": "2026-07-22T02:45:01Z",
            "downloadId": download_id,
            "seriesId": series_id,
            "episodeId": episode_id,
        }

    @staticmethod
    def _import_event(download_id="ABC123DEF456", series_id=10, episode_id=100):
        return {
            "id": 45648,
            "eventType": "downloadFolderImported",
            "date": "2026-07-22T02:49:39Z",
            "downloadId": download_id,
            "seriesId": series_id,
            "episodeId": episode_id,
        }

    def _configure_successful_import(
        self, monitor, queue_item, queue_after_command=None
    ):
        grab = self._grab_event(download_id=queue_item["downloadId"].upper())
        imported = self._import_event(download_id=queue_item["downloadId"].upper())
        monitor.sonarr_client.get_history_for_download.side_effect = [
            [grab],
            [imported, grab],
        ]
        monitor.sonarr_client.get_active_manual_import_command.return_value = None
        monitor.sonarr_client.force_import.return_value = (True, 123)
        monitor.sonarr_client.get_command.return_value = {
            "id": 123,
            "name": "ManualImport",
            "status": "completed",
            "result": "successful",
            "queued": "2026-07-22T02:49:38Z",
            "ended": "2026-07-22T02:49:39Z",
        }
        monitor.sonarr_client.get_queue.return_value = (
            [] if queue_after_command is None else queue_after_command
        )

    def test_execute_force_import_success(self, monitor, mock_sonarr_queue_item):
        """A successful command plus target history confirms the import."""
        self._configure_successful_import(monitor, mock_sonarr_queue_item)

        with patch("src.core.monitor.time.sleep"):
            result = monitor._execute_force_import(mock_sonarr_queue_item)

        assert result is True
        monitor.sonarr_client.force_import.assert_called_once_with(
            "abc123def456",
            mock_sonarr_queue_item["quality"],
            output_path="/downloads/complete/tv/Test.Episode.S01E01",
            content_id=10,
        )
        monitor.sonarr_client.remove_from_queue.assert_not_called()

    def test_execute_force_import_uses_validated_candidate_without_requery(
        self, monitor, mock_sonarr_queue_item
    ):
        candidate = {
            "path": "/downloads/correct.mkv",
            "series": {"id": 10},
            "episodes": [{"id": 100}],
        }
        grab = self._grab_event()
        imported = self._import_event()
        monitor.sonarr_client.get_history_for_download.side_effect = [
            [grab],
            [imported, grab],
        ]
        monitor.sonarr_client.get_active_manual_import_command.return_value = None
        monitor.sonarr_client.force_import_candidate.return_value = (True, 123)
        monitor.sonarr_client.get_command.return_value = {
            "id": 123,
            "status": "completed",
            "result": "successful",
            "queued": "2026-07-22T02:49:38Z",
        }
        monitor.sonarr_client.get_queue.return_value = []

        with patch("src.core.monitor.time.sleep"):
            result = monitor._execute_force_import(mock_sonarr_queue_item, candidate)

        assert result is True
        monitor.sonarr_client.force_import_candidate.assert_called_once_with(
            candidate, mock_sonarr_queue_item["quality"]
        )
        monitor.sonarr_client.force_import.assert_not_called()
        monitor.sonarr_client.remove_from_queue.assert_not_called()

    def test_validated_force_import_stuck_cleanup_keeps_download_client(
        self, monitor, mock_sonarr_queue_item
    ):
        """Confirmed validated imports use queue-only cleanup."""
        candidate = {
            "path": "/downloads/correct.mkv",
            "series": {"id": 10},
            "episodes": [{"id": 100}],
        }
        grab = self._grab_event()
        imported = self._import_event()
        monitor.sonarr_client.get_history_for_download.side_effect = [
            [grab],
            [imported, grab],
        ]
        monitor.sonarr_client.get_active_manual_import_command.return_value = None
        monitor.sonarr_client.force_import_candidate.return_value = (True, 123)
        monitor.sonarr_client.get_command.return_value = {
            "id": 123,
            "status": "completed",
            "result": "successful",
            "queued": "2026-07-22T02:49:38Z",
        }
        monitor.sonarr_client.get_queue.return_value = [
            {"id": 1, "trackedDownloadState": "importPending"}
        ]
        monitor.sonarr_client.cleanup_post_import_queue_item.return_value = False
        monitor.sonarr_client.remove_from_queue.return_value = True

        with patch("src.core.monitor.time.sleep"):
            result = monitor._execute_force_import(mock_sonarr_queue_item, candidate)

        assert result is True
        monitor.sonarr_client.remove_from_queue.assert_called_once_with(
            1, remove_from_client=False, blocklist=False
        )

    def test_matched_by_id_stale_queue_cleanup_keeps_torrent(
        self, monitor, mock_sonarr_queue_item
    ):
        """Regression for 02c567: successful import plus stale queue is safe."""
        self._configure_successful_import(
            monitor,
            mock_sonarr_queue_item,
            [{"id": 1, "trackedDownloadState": "importBlocked"}],
        )
        monitor.sonarr_client.cleanup_post_import_queue_item.return_value = False
        monitor.sonarr_client.remove_from_queue.return_value = True

        with patch("src.core.monitor.time.sleep"):
            result = monitor._execute_force_import(mock_sonarr_queue_item)

        assert result is True
        monitor.sonarr_client.remove_from_queue.assert_called_once_with(
            1, remove_from_client=False, blocklist=False
        )

    def test_existing_import_history_skips_duplicate_command(
        self, monitor, mock_sonarr_queue_item
    ):
        grab = self._grab_event()
        imported = self._import_event()
        monitor.sonarr_client.get_history_for_download.return_value = [
            imported,
            grab,
        ]
        monitor.sonarr_client.get_queue.return_value = [mock_sonarr_queue_item]
        monitor.sonarr_client.remove_from_queue.return_value = True

        result = monitor._execute_force_import(mock_sonarr_queue_item)

        assert result is True
        monitor.sonarr_client.force_import.assert_not_called()
        monitor.sonarr_client.get_active_manual_import_command.assert_not_called()
        monitor.sonarr_client.remove_from_queue.assert_called_once_with(
            1, remove_from_client=False, blocklist=False
        )

    def test_active_manual_import_is_reused_without_resubmission(
        self, monitor, mock_sonarr_queue_item
    ):
        grab = self._grab_event()
        imported = self._import_event()
        monitor.sonarr_client.get_history_for_download.side_effect = [
            [grab],
            [imported, grab],
        ]
        monitor.sonarr_client.get_active_manual_import_command.return_value = {
            "id": 321,
            "name": "ManualImport",
            "status": "started",
            "queued": "2026-07-22T02:49:38Z",
        }
        monitor.sonarr_client.get_command.return_value = {
            "id": 321,
            "status": "completed",
            "result": "successful",
            "queued": "2026-07-22T02:49:38Z",
        }
        monitor.sonarr_client.get_queue.return_value = []

        with patch("src.core.monitor.time.sleep"):
            assert monitor._execute_force_import(mock_sonarr_queue_item) is True

        monitor.sonarr_client.force_import.assert_not_called()
        monitor.sonarr_client.force_import_candidate.assert_not_called()

    @pytest.mark.parametrize(
        ("status", "result"),
        [
            ("completed", "unsuccessful"),
            ("failed", "unsuccessful"),
            ("aborted", "unsuccessful"),
            ("cancelled", "unsuccessful"),
            ("orphaned", "indeterminate"),
        ],
    )
    def test_terminal_command_failure_leaves_queue_and_torrent(
        self, monitor, mock_sonarr_queue_item, status, result
    ):
        monitor.sonarr_client.get_history_for_download.return_value = [
            self._grab_event()
        ]
        monitor.sonarr_client.get_active_manual_import_command.return_value = None
        monitor.sonarr_client.force_import.return_value = (True, 123)
        monitor.sonarr_client.get_command.return_value = {
            "id": 123,
            "status": status,
            "result": result,
            "message": "Import failed",
        }

        assert monitor._execute_force_import(mock_sonarr_queue_item) is False
        monitor.sonarr_client.remove_from_queue.assert_not_called()

    def test_command_timeout_leaves_queue_and_torrent(
        self, monitor, mock_sonarr_queue_item
    ):
        monitor.sonarr_client.get_history_for_download.return_value = [
            self._grab_event()
        ]
        monitor.sonarr_client.get_active_manual_import_command.return_value = None
        monitor.sonarr_client.force_import.return_value = (True, 123)
        monitor.sonarr_client.get_command.return_value = {
            "id": 123,
            "status": "started",
            "result": "unknown",
        }

        with patch(
            "src.core.monitor.FORCE_IMPORT_COMMAND_TIMEOUT_SECONDS", 0, create=True
        ), patch("src.core.monitor.time.sleep"):
            assert monitor._execute_force_import(mock_sonarr_queue_item) is False

        monitor.sonarr_client.remove_from_queue.assert_not_called()

    def test_success_without_matching_history_leaves_queue_and_torrent(
        self, monitor, mock_sonarr_queue_item
    ):
        grab = self._grab_event()
        monitor.sonarr_client.get_history_for_download.return_value = [grab]
        monitor.sonarr_client.get_active_manual_import_command.return_value = None
        monitor.sonarr_client.force_import.return_value = (True, 123)
        monitor.sonarr_client.get_command.return_value = {
            "id": 123,
            "status": "completed",
            "result": "successful",
            "queued": "2026-07-22T02:49:38Z",
        }

        with patch(
            "src.core.monitor.FORCE_IMPORT_HISTORY_TIMEOUT_SECONDS", 0, create=True
        ), patch("src.core.monitor.time.sleep"):
            assert monitor._execute_force_import(mock_sonarr_queue_item) is False

        monitor.sonarr_client.remove_from_queue.assert_not_called()

    def test_conflicting_import_history_leaves_queue_and_torrent(
        self, monitor, mock_sonarr_queue_item
    ):
        grab = self._grab_event()
        wrong_import = self._import_event(series_id=999, episode_id=9999)
        monitor.sonarr_client.get_history_for_download.side_effect = [
            [grab],
            [wrong_import, grab],
        ]
        monitor.sonarr_client.get_active_manual_import_command.return_value = None
        monitor.sonarr_client.force_import.return_value = (True, 123)
        monitor.sonarr_client.get_command.return_value = {
            "id": 123,
            "status": "completed",
            "result": "successful",
            "queued": "2026-07-22T02:49:38Z",
        }

        with patch("src.core.monitor.time.sleep"):
            assert monitor._execute_force_import(mock_sonarr_queue_item) is False

        monitor.sonarr_client.remove_from_queue.assert_not_called()

    def test_command_polling_waits_for_success(self, monitor, mock_sonarr_queue_item):
        grab = self._grab_event()
        imported = self._import_event()
        monitor.sonarr_client.get_history_for_download.side_effect = [
            [grab],
            [imported, grab],
        ]
        monitor.sonarr_client.get_active_manual_import_command.return_value = None
        monitor.sonarr_client.force_import.return_value = (True, 123)
        monitor.sonarr_client.get_command.side_effect = [
            {"id": 123, "status": "queued", "result": "unknown"},
            {
                "id": 123,
                "status": "completed",
                "result": "successful",
                "queued": "2026-07-22T02:49:38Z",
            },
        ]
        monitor.sonarr_client.get_queue.return_value = []

        with patch("src.core.monitor.time.sleep") as mock_sleep:
            assert monitor._execute_force_import(mock_sonarr_queue_item) is True

        mock_sleep.assert_called_once_with(1.0)
        assert monitor.sonarr_client.get_command.call_count == 2

    def test_season_pack_history_accepts_target_episode_without_conflict(
        self, monitor, mock_sonarr_queue_item
    ):
        grab = self._grab_event()
        pack_episode = self._import_event(episode_id=101)
        target_episode = self._import_event(episode_id=100)
        monitor.sonarr_client.get_history_for_download.side_effect = [
            [grab],
            [pack_episode, target_episode, grab],
        ]
        monitor.sonarr_client.get_active_manual_import_command.return_value = None
        monitor.sonarr_client.force_import.return_value = (True, 123)
        monitor.sonarr_client.get_command.return_value = {
            "id": 123,
            "status": "completed",
            "result": "successful",
            "queued": "2026-07-22T02:49:38Z",
        }
        monitor.sonarr_client.get_queue.return_value = []

        assert monitor._execute_force_import(mock_sonarr_queue_item) is True
        monitor.sonarr_client.remove_from_queue.assert_not_called()

    def test_history_preflight_api_failure_is_safe(
        self, monitor, mock_sonarr_queue_item, caplog
    ):
        monitor.sonarr_client.get_history_for_download.side_effect = Exception(
            "history unavailable"
        )

        assert monitor._execute_force_import(mock_sonarr_queue_item) is False
        monitor.sonarr_client.force_import.assert_not_called()
        monitor.sonarr_client.remove_from_queue.assert_not_called()
        assert "queue and torrent left untouched" in caplog.text

    def test_missing_grab_history_does_not_submit_a_duplicate(
        self, monitor, mock_sonarr_queue_item
    ):
        monitor.sonarr_client.get_history_for_download.return_value = []
        monitor.sonarr_client.get_active_manual_import_command.return_value = None

        assert monitor._execute_force_import(mock_sonarr_queue_item) is False
        monitor.sonarr_client.force_import.assert_not_called()
        monitor.sonarr_client.force_import_candidate.assert_not_called()
        monitor.sonarr_client.remove_from_queue.assert_not_called()

    def test_active_command_discovery_api_failure_is_safe(
        self, monitor, mock_sonarr_queue_item
    ):
        monitor.sonarr_client.get_history_for_download.return_value = [
            self._grab_event()
        ]
        monitor.sonarr_client.get_active_manual_import_command.side_effect = Exception(
            "commands unavailable"
        )

        assert monitor._execute_force_import(mock_sonarr_queue_item) is False
        monitor.sonarr_client.force_import.assert_not_called()
        monitor.sonarr_client.remove_from_queue.assert_not_called()

    def test_command_lookup_api_failure_is_safe(self, monitor, mock_sonarr_queue_item):
        monitor.sonarr_client.get_history_for_download.return_value = [
            self._grab_event()
        ]
        monitor.sonarr_client.get_active_manual_import_command.return_value = None
        monitor.sonarr_client.force_import.return_value = (True, 123)
        monitor.sonarr_client.get_command.side_effect = Exception("command unavailable")

        assert monitor._execute_force_import(mock_sonarr_queue_item) is False
        monitor.sonarr_client.remove_from_queue.assert_not_called()

    def test_post_command_history_api_failure_is_safe(
        self, monitor, mock_sonarr_queue_item
    ):
        monitor.sonarr_client.get_history_for_download.side_effect = [
            [self._grab_event()],
            Exception("history unavailable"),
        ]
        monitor.sonarr_client.get_active_manual_import_command.return_value = None
        monitor.sonarr_client.force_import.return_value = (True, 123)
        monitor.sonarr_client.get_command.return_value = {
            "id": 123,
            "status": "completed",
            "result": "successful",
            "queued": "2026-07-22T02:49:38Z",
        }

        assert monitor._execute_force_import(mock_sonarr_queue_item) is False
        monitor.sonarr_client.remove_from_queue.assert_not_called()

    def test_cleanup_queue_api_failure_is_safe(self, monitor, mock_sonarr_queue_item):
        monitor.sonarr_client.get_history_for_download.return_value = [
            self._import_event(),
            self._grab_event(),
        ]
        monitor.sonarr_client.get_queue.side_effect = Exception("queue unavailable")

        assert monitor._execute_force_import(mock_sonarr_queue_item) is False
        monitor.sonarr_client.force_import.assert_not_called()
        monitor.sonarr_client.remove_from_queue.assert_not_called()

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

    def test_execute_force_import_unimportable_leaves_queue_and_torrent(
        self, monitor, mock_sonarr_queue_item
    ):
        """No candidates is evidence failure, not permission to delete."""
        monitor.sonarr_client.get_history_for_download.return_value = [
            self._grab_event()
        ]
        monitor.sonarr_client.get_active_manual_import_command.return_value = None
        monitor.sonarr_client.force_import.return_value = (False, -1)

        result = monitor._execute_force_import(mock_sonarr_queue_item)

        assert result is False
        monitor.sonarr_client.remove_from_queue.assert_not_called()

    def test_execute_force_import_passes_extra_params(
        self, monitor, mock_sonarr_queue_item
    ):
        """Test that outputPath and seriesId are passed to force_import"""
        monitor.sonarr_client.get_history_for_download.return_value = [
            self._grab_event()
        ]
        monitor.sonarr_client.get_active_manual_import_command.return_value = None
        monitor.sonarr_client.force_import.return_value = (False, -1)

        monitor._execute_force_import(mock_sonarr_queue_item)

        monitor.sonarr_client.force_import.assert_called_once_with(
            "abc123def456",
            mock_sonarr_queue_item["quality"],
            output_path="/downloads/complete/tv/Test.Episode.S01E01",
            content_id=10,
        )

    def test_concurrent_force_import_for_same_hash_is_not_duplicated(
        self, monitor, mock_sonarr_queue_item
    ):
        monitor._force_import_download_ids.add("abc123def456")

        result = monitor._execute_force_import(mock_sonarr_queue_item)

        assert result is False
        monitor.sonarr_client.get_history_for_download.assert_not_called()
        monitor.sonarr_client.force_import.assert_not_called()

    def test_execute_force_import_no_removal_if_import_worked(
        self, monitor, mock_sonarr_queue_item
    ):
        """Test that items successfully imported are not removed"""
        self._configure_successful_import(monitor, mock_sonarr_queue_item)

        with patch("src.core.monitor.time.sleep"):
            result = monitor._execute_force_import(mock_sonarr_queue_item)

        assert result is True
        monitor.sonarr_client.remove_from_queue.assert_not_called()

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

    def test_execute_keep_removes_only_arr_queue_entry(
        self, monitor, mock_sonarr_queue_item
    ):
        """Keep means ignore the Arr queue item without touching qBittorrent."""
        monitor.sonarr_client.remove_from_queue.return_value = True

        assert monitor._execute_keep_action(mock_sonarr_queue_item) is True
        monitor.sonarr_client.remove_from_queue.assert_called_once_with(
            1, remove_from_client=False, blocklist=False
        )


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

        # Verify logger was called with statistics (single-line format)
        call_args = [call.args[0] for call in mock_logger.info.call_args_list]

        logged_text = " ".join(call_args)
        assert "5 cycles" in logged_text
        assert "10 processed" in logged_text
        assert "3 imported" in logged_text
        assert "2 removed" in logged_text
        assert "1 errors" in logged_text


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

                    mock_client.get_queue.side_effect = [stuck_items, []]

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
                    mock_client.get_history_for_download.side_effect = [
                        [
                            {
                                "eventType": "grabbed",
                                "date": "2026-07-22T02:45:01Z",
                                "downloadId": "ABC123DEF456",
                                "seriesId": 10,
                                "episodeId": 100,
                            }
                        ],
                        [
                            {
                                "id": 45648,
                                "eventType": "downloadFolderImported",
                                "date": "2026-07-22T02:49:39Z",
                                "downloadId": "ABC123DEF456",
                                "seriesId": 10,
                                "episodeId": 100,
                            }
                        ],
                    ]
                    mock_client.get_active_manual_import_command.return_value = None
                    mock_client.force_import.return_value = (True, 123)
                    mock_client.get_command.return_value = {
                        "id": 123,
                        "status": "completed",
                        "result": "successful",
                        "queued": "2026-07-22T02:49:38Z",
                    }

                    results = monitor.process_stuck_imports()

                    assert results == {"processed": 1, "forced": 1, "removed": 0}
                    assert monitor.stats["forced_imports"] == 1
                    # Note: items_processed is only updated in run_once(), not in process_stuck_imports()


class TestRadarrMonitor:
    """Test monitor in Radarr mode"""

    @pytest.fixture
    def radarr_monitor(self, mock_radarr_config):
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "app.type": "radarr",
            "radarr.url": mock_radarr_config["radarr"]["url"],
            "monitoring.interval": 60,
            "monitoring.detect_repeated_grabs": True,
            "decisions.force_import_threshold": 10,
            "webhook.enabled": False,
        }.get(key, default)

        with patch("src.core.monitor.SonarrClient"), patch(
            "src.core.monitor.ScoreAnalyzer"
        ), patch("src.core.monitor.signal.signal"):
            monitor = SonarrImportMonitor(config)

        return monitor

    def test_radarr_app_type(self, radarr_monitor):
        """Test that Radarr monitor has correct app_type"""
        assert radarr_monitor.app_type == "radarr"

    def test_radarr_webhook_skipped(self, radarr_monitor):
        """Test that webhook is not started for Radarr"""
        result = radarr_monitor.start_webhook_server()
        assert result is False

    def test_radarr_force_import_extracts_movie_id(
        self, radarr_monitor, mock_radarr_queue_item
    ):
        """Test that Radarr force import uses movie.id"""
        radarr_monitor.sonarr_client.get_history_for_download.side_effect = [
            [
                {
                    "eventType": "grabbed",
                    "date": "2026-07-22T02:45:01Z",
                    "downloadId": "ABC123DEF456",
                    "movieId": 50,
                }
            ],
            [
                {
                    "id": 9001,
                    "eventType": "downloadFolderImported",
                    "date": "2026-07-22T02:49:39Z",
                    "downloadId": "ABC123DEF456",
                    "movieId": 50,
                }
            ],
        ]
        radarr_monitor.sonarr_client.get_active_manual_import_command.return_value = (
            None
        )
        radarr_monitor.sonarr_client.force_import.return_value = (True, 123)
        radarr_monitor.sonarr_client.get_command.return_value = {
            "id": 123,
            "status": "completed",
            "result": "successful",
            "queued": "2026-07-22T02:49:38Z",
        }
        radarr_monitor.sonarr_client.get_queue.return_value = []

        assert radarr_monitor._execute_force_import(mock_radarr_queue_item) is True

        radarr_monitor.sonarr_client.force_import.assert_called_once_with(
            "abc123def456",
            mock_radarr_queue_item["quality"],
            output_path="/downloads/complete/movies/Test.Movie.2024",
            content_id=50,
        )
        radarr_monitor.sonarr_client.remove_from_queue.assert_not_called()

    def test_radarr_identify_stuck_matched_by_movie_id(self, radarr_monitor):
        """Test detection of 'matched to movie by id' pattern"""
        queue = [
            {
                "id": 1,
                "statusMessages": [
                    {"messages": ["Matched to movie by ID. Automatic import disabled"]}
                ],
            }
        ]
        stuck = radarr_monitor._identify_stuck_items(queue)
        assert len(stuck) == 1

    def test_radarr_test_specific_movie(self, radarr_monitor):
        """Test test_specific_movie method"""
        mock_movie = {
            "id": 50,
            "title": "The Matrix",
            "year": 1999,
            "hasFile": True,
        }
        radarr_monitor.sonarr_client.get_series_by_title.return_value = mock_movie
        radarr_monitor.analyzer.get_current_file_details.return_value = (80, ["x265"])
        radarr_monitor.sonarr_client.get_queue.return_value = []
        radarr_monitor.sonarr_client.get_history_for_episode.return_value = []

        # Should not raise
        radarr_monitor.test_specific_movie("The Matrix")

        radarr_monitor.sonarr_client.get_series_by_title.assert_called_once_with(
            "The Matrix"
        )


class TestSupersededStalledDownloads:
    """Stalled Radarr downloads whose movie already has an equal/better file."""

    @pytest.fixture
    def radarr_monitor(self):
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "app.type": "radarr",
        }.get(key, default)

        with patch("src.core.monitor.SonarrClient"), patch(
            "src.core.monitor.ScoreAnalyzer"
        ), patch("src.core.monitor.signal.signal"):
            monitor = SonarrImportMonitor(config)

        monitor.sonarr_client.remove_from_queue.return_value = True
        monitor.analyzer._get_current_file_evidence.return_value = (6300, [], None)
        return monitor

    @staticmethod
    def _stalled_item(**overrides):
        item = {
            "id": 7,
            "movie": {"id": 1196},
            "status": "warning",
            "trackedDownloadStatus": "ok",
            "trackedDownloadState": "downloading",
            "errorMessage": "The download is stalled with no connections",
            "customFormatScore": 5800,
            "added": "2020-01-01T00:00:00Z",
        }
        item.update(overrides)
        return item

    def test_removes_stalled_item_when_current_file_is_better(self, radarr_monitor):
        removed = radarr_monitor.remove_superseded_stalled([self._stalled_item()])

        assert removed == 1
        radarr_monitor.sonarr_client.remove_from_queue.assert_called_once_with(
            7, remove_from_client=True, blocklist=False
        )

    def test_keeps_stalled_item_when_it_would_be_an_upgrade(self, radarr_monitor):
        radarr_monitor.analyzer._get_current_file_evidence.return_value = (100, [], None)

        assert radarr_monitor.remove_superseded_stalled([self._stalled_item()]) == 0
        radarr_monitor.sonarr_client.remove_from_queue.assert_not_called()

    def test_keeps_stalled_item_when_movie_has_no_file(self, radarr_monitor):
        radarr_monitor.analyzer._get_current_file_evidence.return_value = (None, [], None)

        assert radarr_monitor.remove_superseded_stalled([self._stalled_item()]) == 0
        radarr_monitor.sonarr_client.remove_from_queue.assert_not_called()

    def test_keeps_item_when_file_lookup_fails(self, radarr_monitor):
        radarr_monitor.analyzer._get_current_file_evidence.return_value = (
            None,
            [],
            "boom",
        )

        assert radarr_monitor.remove_superseded_stalled([self._stalled_item()]) == 0
        radarr_monitor.sonarr_client.remove_from_queue.assert_not_called()

    def test_ignores_item_that_is_not_stalled(self, radarr_monitor):
        item = self._stalled_item(errorMessage=None)

        assert radarr_monitor.remove_superseded_stalled([item]) == 0
        radarr_monitor.sonarr_client.remove_from_queue.assert_not_called()

    def test_ignores_recently_added_stalled_item(self, radarr_monitor):
        item = self._stalled_item(
            added=datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        )

        assert radarr_monitor.remove_superseded_stalled([item]) == 0
        radarr_monitor.sonarr_client.remove_from_queue.assert_not_called()

    def test_ignores_item_without_queue_score(self, radarr_monitor):
        item = self._stalled_item(customFormatScore=None)

        assert radarr_monitor.remove_superseded_stalled([item]) == 0
        radarr_monitor.sonarr_client.remove_from_queue.assert_not_called()

    def test_dry_run_does_not_remove(self, radarr_monitor):
        radarr_monitor.dry_run = True

        assert radarr_monitor.remove_superseded_stalled([self._stalled_item()]) == 1
        radarr_monitor.sonarr_client.remove_from_queue.assert_not_called()

    def test_sonarr_is_never_touched(self, radarr_monitor):
        radarr_monitor.app_type = "sonarr"

        assert radarr_monitor.remove_superseded_stalled([self._stalled_item()]) == 0
        radarr_monitor.sonarr_client.remove_from_queue.assert_not_called()

    def test_process_stuck_imports_runs_the_stalled_cleanup(self, radarr_monitor):
        radarr_monitor.sonarr_client.get_queue.return_value = [self._stalled_item()]

        results = radarr_monitor.process_stuck_imports()

        radarr_monitor.sonarr_client.remove_from_queue.assert_called_once()
        assert results["removed"] == 1


class TestForceImportSafetyBlock:
    """Items Sonarr/Radarr blocked as dangerous must never be force-imported."""

    @pytest.fixture
    def monitor(self):
        config = MagicMock()
        config.get.return_value = None

        with patch("src.core.monitor.SonarrClient"), patch(
            "src.core.monitor.ScoreAnalyzer"
        ), patch("src.core.monitor.signal.signal"):
            return SonarrImportMonitor(config)

    @staticmethod
    def _item(**overrides):
        item = {
            "id": 1,
            "downloadId": "DEF734B87A530577C91B2B8065260DCFE7A07A6A",
            "series": {"id": 225},
            "episode": {"id": 10798},
            "trackedDownloadState": "importBlocked",
            "quality": {"quality": {"name": "WEBDL-1080p"}},
            "outputPath": "/torrents/Some.Release.S02E05",
            "statusMessages": [],
        }
        item.update(overrides)
        return item

    def _assert_blocked(self, monitor, item):
        assert monitor._execute_force_import(item) is False
        monitor.sonarr_client.get_history_for_download.assert_not_called()
        monitor.sonarr_client.force_import.assert_not_called()
        monitor.sonarr_client.force_import_candidate.assert_not_called()
        monitor.sonarr_client.remove_from_queue.assert_not_called()

    def test_blocks_executable_warning_message(self, monitor):
        item = self._item(
            statusMessages=[
                {
                    "title": "Some.Release.S02E05.exe",
                    "messages": [
                        "Caution: Found executable file with extension: '.exe'"
                    ],
                }
            ]
        )
        self._assert_blocked(monitor, item)

    def test_blocks_potentially_dangerous_file_message(self, monitor):
        item = self._item(
            statusMessages=[
                {
                    "title": "x",
                    "messages": [
                        "Caution: Found potentially dangerous file with extension: '.lnk'"
                    ],
                }
            ]
        )
        self._assert_blocked(monitor, item)

    @pytest.mark.parametrize("ext", [".exe", ".SCR", ".bat", ".msi", ".ps1", ".jar"])
    def test_blocks_dangerous_output_path_extension(self, monitor, ext):
        item = self._item(outputPath=f"/torrents/Some.Release.S02E05{ext}")
        self._assert_blocked(monitor, item)

    def test_blocks_dangerous_status_message_title(self, monitor):
        item = self._item(
            statusMessages=[{"title": "Some.Release.S02E05.exe", "messages": ["x"]}]
        )
        self._assert_blocked(monitor, item)

    def test_blocks_even_in_dry_run(self, monitor):
        monitor.dry_run = True
        item = self._item(outputPath="/torrents/Some.Release.S02E05.exe")
        assert monitor._execute_force_import(item) is False

    def test_normal_item_is_not_blocked_by_the_guard(self, monitor):
        monitor.sonarr_client.get_history_for_download.return_value = []
        monitor._execute_force_import(self._item())
        monitor.sonarr_client.get_history_for_download.assert_called_once()

    def test_process_queue_item_never_forces_blocked_item(self, monitor):
        monitor.analyzer.analyze_queue_item.return_value = Decision(
            action="force_import",
            reasoning="r",
            grab_score=50,
            current_score=0,
            score_difference=50,
            grab_formats=[],
            current_formats=[],
            missing_formats=[],
            extra_formats=[],
            is_private_tracker=False,
            validated_import_candidate=None,
        )
        item = self._item(outputPath="/torrents/Some.Release.S02E05.exe")

        assert monitor.process_queue_item(item) == "error"
        monitor.sonarr_client.force_import.assert_not_called()
