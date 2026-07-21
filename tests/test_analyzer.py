"""
Unit tests for ScoreAnalyzer and decision making logic.
"""

from unittest.mock import MagicMock, Mock, patch

import pytest

from src.config.loader import ConfigLoader
from src.core.analyzer import Decision, FormatAnalysis, ScoreAnalyzer


class TestFormatAnalysis:
    """Test FormatAnalysis NamedTuple"""

    def test_format_analysis_creation(self):
        """Test FormatAnalysis creation with score and formats"""
        analysis = FormatAnalysis(100, ["HDR", "Atmos"])
        assert analysis.total_score == 100
        assert analysis.format_names == ["HDR", "Atmos"]


@pytest.fixture
def analyzer(mock_config):
    """Create ScoreAnalyzer instance for testing"""
    config = MagicMock()
    config.get.side_effect = lambda key, default=None: {
        "decisions.force_import_threshold": mock_config["decisions"][
            "force_import_threshold"
        ],
        "trackers.private": mock_config["trackers"]["private"],
        "trackers.public": mock_config["trackers"]["public"],
    }.get(key, default)

    sonarr_client = MagicMock()
    return ScoreAnalyzer(config, sonarr_client)


class TestScoreAnalyzer:
    """Test ScoreAnalyzer class"""

    def test_init(self, analyzer, mock_config):
        """Test analyzer initialization"""
        assert analyzer.force_import_threshold == 10
        assert "privatehd" in analyzer.private_trackers
        assert "nyaa" in analyzer.public_trackers

    def test_analyze_custom_formats_empty(self, analyzer):
        """Test analyzing empty custom formats list"""
        result = analyzer.analyze_custom_formats([], 1)
        assert result.total_score == 0
        assert result.format_names == []

    def test_analyze_custom_formats_with_scores(self, analyzer):
        """Test analyzing custom formats with scores"""
        custom_formats = [{"id": 1, "name": "HDR"}, {"id": 2, "name": "Atmos"}]

        analyzer.sonarr_client.get_custom_format_scores.return_value = {1: 100, 2: 50}

        result = analyzer.analyze_custom_formats(custom_formats, 1)

        assert result.total_score == 150
        assert "HDR" in result.format_names
        assert "Atmos" in result.format_names

    def test_is_private_tracker_true(self, analyzer):
        """Test private tracker detection - positive case"""
        assert analyzer.is_private_tracker("PrivateHD") is True
        assert analyzer.is_private_tracker("BeyondHD-API") is True
        assert analyzer.is_private_tracker("PassThePopcorn") is True

    def test_is_private_tracker_false(self, analyzer):
        """Test private tracker detection - negative case"""
        assert analyzer.is_private_tracker("Nyaa") is False
        assert analyzer.is_private_tracker("RARBG") is False
        assert analyzer.is_private_tracker("") is False
        assert analyzer.is_private_tracker(None) is False

    def test_find_grab_info_found(self, analyzer):
        """Test finding grab info from history"""
        history = [
            {
                "eventType": "grabbed",
                "downloadId": "test123",
                "customFormatScore": 75,
                "customFormats": [{"name": "HDR"}, {"name": "x265"}],
            },
            {"eventType": "downloadFolderImported", "downloadId": "test123"},
        ]

        score, formats = analyzer.find_grab_info(history, "test123")

        assert score == 75
        assert "HDR" in formats
        assert "x265" in formats

    def test_find_grab_info_not_found(self, analyzer):
        """Test finding grab info when not in history"""
        history = [{"eventType": "downloadFolderImported", "downloadId": "test123"}]

        score, formats = analyzer.find_grab_info(history, "test123")

        assert score is None
        assert formats == []

    def test_find_grab_info_calculate_score(self, analyzer):
        """Test calculating score when not in history"""
        history = [
            {
                "eventType": "grabbed",
                "downloadId": "test123",
                "customFormatScore": 0,
                "customFormats": [{"id": 1, "name": "HDR"}],
            }
        ]

        analyzer.sonarr_client.get_custom_format_scores.return_value = {1: 100}

        score, formats = analyzer.find_grab_info(history, "test123", series_id=10)

        assert score == 100
        assert "HDR" in formats

    def test_get_current_file_details_no_file(self, analyzer):
        """Test getting current file details when no file exists"""
        # Mock episode with no file
        mock_response = MagicMock()
        mock_response.json.return_value = {"hasFile": False}
        analyzer.sonarr_client._make_request.return_value = mock_response

        score, formats = analyzer.get_current_file_details(100)

        assert score is None
        assert formats == []

    def test_get_current_file_details_with_file(self, analyzer):
        """Test getting current file details when file exists"""
        # Mock episode with file
        mock_response = MagicMock()
        mock_response.json.return_value = {"hasFile": True, "episodeFileId": 50}
        analyzer.sonarr_client._make_request.return_value = mock_response

        # Mock file data
        analyzer.sonarr_client.get_episode_file.return_value = {
            "customFormatScore": 80,
            "customFormats": [{"name": "x265"}, {"name": "HDR"}],
        }

        score, formats = analyzer.get_current_file_details(100)

        assert score == 80
        assert "x265" in formats
        assert "HDR" in formats

    @patch("src.core.analyzer.logger")
    def test_get_current_file_details_error(self, mock_logger, analyzer):
        """Test error handling in get_current_file_details"""
        analyzer.sonarr_client._make_request.side_effect = Exception("API Error")

        score, formats = analyzer.get_current_file_details(100)

        assert score is None
        assert formats == []
        mock_logger.error.assert_called_once()

    def test_make_decision_force_import(self, analyzer):
        """Test decision to force import due to higher score"""
        decision = analyzer._make_decision(
            grab_score=100,
            current_score=80,
            is_private_tracker=False,
            is_public_tracker=True,
            is_unknown_tracker=False,
            grab_formats=["HDR", "Atmos"],
            current_formats=["x265"],
            missing_formats=["HDR", "Atmos"],
            extra_formats=[],
            indexer="public_tracker",
        )

        assert decision["action"] == "force_import"
        assert "higher than current file" in decision["reasoning"]

    def test_make_decision_keep_private_tracker(self, analyzer):
        """Test decision to keep private tracker despite lower score"""
        decision = analyzer._make_decision(
            grab_score=80,
            current_score=100,
            is_private_tracker=True,
            is_public_tracker=False,
            is_unknown_tracker=False,
            grab_formats=["x265"],
            current_formats=["HDR", "Atmos"],
            missing_formats=[],
            extra_formats=["HDR", "Atmos"],
            indexer="private_tracker",
        )

        assert decision["action"] == "keep"
        assert "Private tracker protection" in decision["reasoning"]

    def test_make_decision_remove_public_tracker(self, analyzer):
        """Test decision to remove public tracker with lower score"""
        decision = analyzer._make_decision(
            grab_score=80,
            current_score=100,
            is_private_tracker=False,
            is_public_tracker=True,
            is_unknown_tracker=False,
            grab_formats=["x265"],
            current_formats=["HDR", "Atmos"],
            missing_formats=[],
            extra_formats=["HDR", "Atmos"],
            indexer="public_tracker",
        )

        assert decision["action"] == "remove"
        assert "Public tracker with lower score" in decision["reasoning"]

    def test_make_decision_wait_within_threshold(self, analyzer):
        """Test decision to wait when score difference is within threshold"""
        decision = analyzer._make_decision(
            grab_score=85,
            current_score=80,
            is_private_tracker=False,
            is_public_tracker=True,
            is_unknown_tracker=False,
            grab_formats=["x265"],
            current_formats=["x264"],
            missing_formats=[],
            extra_formats=[],
            indexer="public_tracker",
        )

        assert decision["action"] == "wait"
        assert "within tolerance threshold" in decision["reasoning"]

    def test_make_decision_monitor_missing_scores(self, analyzer):
        """Test decision to monitor when scores are missing"""
        decision = analyzer._make_decision(
            grab_score=None,
            current_score=80,
            is_private_tracker=False,
            is_public_tracker=True,
            is_unknown_tracker=False,
            grab_formats=[],
            current_formats=["x265"],
            missing_formats=[],
            extra_formats=[],
            indexer="public_tracker",
        )

        assert decision["action"] == "monitor"
        assert "Unable to determine scores" in decision["reasoning"]

    def test_make_decision_keep_unknown_tracker(self, analyzer):
        """Test decision to keep unknown tracker despite lower score for safety"""
        decision = analyzer._make_decision(
            grab_score=80,
            current_score=100,
            is_private_tracker=False,
            is_public_tracker=False,
            is_unknown_tracker=True,
            grab_formats=["x265"],
            current_formats=["HDR", "Atmos"],
            missing_formats=[],
            extra_formats=["HDR", "Atmos"],
            indexer="unknown_tracker",
        )

        assert decision["action"] == "keep"
        assert "Unknown tracker" in decision["reasoning"]
        assert "keeping for safety" in decision["reasoning"]

    def test_is_public_tracker_true(self, analyzer):
        """Test is_public_tracker returns True for public tracker"""
        assert analyzer.is_public_tracker("rarbg")
        assert analyzer.is_public_tracker("RARBG Torrents")

    def test_is_public_tracker_false(self, analyzer):
        """Test is_public_tracker returns False for non-public tracker"""
        assert not analyzer.is_public_tracker("private_site")
        assert not analyzer.is_public_tracker("")
        assert not analyzer.is_public_tracker(None)

    def test_detect_repeated_grabs_normal(self, analyzer):
        """Test detecting repeated grabs - normal case"""
        history = [
            {"eventType": "grabbed", "downloadId": "grab1"},
            {"eventType": "downloadFolderImported", "downloadId": "grab1"},
        ]

        analyzer.sonarr_client.get_history_for_episode.return_value = history

        result = analyzer.detect_repeated_grabs(100)
        assert result == []

    def test_detect_repeated_grabs_excessive(self, analyzer):
        """Test detecting repeated grabs - excessive case"""
        history = [
            {"eventType": "grabbed", "downloadId": "grab1"},
            {"eventType": "grabbed", "downloadId": "grab2"},
            {"eventType": "grabbed", "downloadId": "grab3"},
            {"eventType": "downloadFolderImported", "downloadId": "grab1"},
        ]

        analyzer.sonarr_client.get_history_for_episode.return_value = history

        result = analyzer.detect_repeated_grabs(100)

        # Should find 2 unimported grabs
        assert len(result) == 2
        assert any(grab["downloadId"] == "grab2" for grab in result)
        assert any(grab["downloadId"] == "grab3" for grab in result)

    @patch("src.core.analyzer.logger")
    def test_detect_repeated_grabs_error(self, mock_logger, analyzer):
        """Test error handling in detect_repeated_grabs"""
        analyzer.sonarr_client.get_history_for_episode.side_effect = Exception(
            "API Error"
        )

        result = analyzer.detect_repeated_grabs(100)

        assert result == []
        mock_logger.error.assert_called_once()

    def test_analyze_queue_item_complete_flow(self, analyzer, mock_sonarr_queue_item):
        """Test complete analysis flow for a queue item"""
        # Setup mocks for complete flow
        analyzer.sonarr_client.get_history_for_episode.return_value = [
            {
                "eventType": "grabbed",
                "downloadId": "abc123def456",
                "customFormatScore": 100,
                "customFormats": [{"name": "HDR"}, {"name": "Atmos"}],
                "data": {"indexer": "PrivateHD"},
            }
        ]

        # Mock current file details
        mock_response = MagicMock()
        mock_response.json.return_value = {"hasFile": True, "episodeFileId": 50}
        analyzer.sonarr_client._make_request.return_value = mock_response
        analyzer.sonarr_client.get_episode_file.return_value = {
            "customFormatScore": 80,
            "customFormats": [{"name": "x265"}],
        }

        decision = analyzer.analyze_queue_item(mock_sonarr_queue_item)

        assert isinstance(decision, Decision)
        assert decision.action == "force_import"
        assert decision.grab_score == 100
        assert decision.current_score == 80
        assert decision.score_difference == 20
        assert decision.is_private_tracker is True
        assert "HDR" in decision.grab_formats
        assert "Atmos" in decision.grab_formats
        assert "x265" in decision.current_formats

    def test_find_indexer_from_history_found(self, analyzer):
        """Test finding indexer from history"""
        history = [
            {
                "eventType": "grabbed",
                "downloadId": "test123",
                "data": {"indexer": "BeyondHD"},
            }
        ]

        indexer = analyzer._find_indexer_from_history(history, "test123")
        assert indexer == "BeyondHD"

    def test_find_indexer_from_history_not_found(self, analyzer):
        """Test finding indexer from history when not found"""
        history = [{"eventType": "downloadFolderImported", "downloadId": "test123"}]

        indexer = analyzer._find_indexer_from_history(history, "test123")
        assert indexer == ""

    def test_find_indexer_from_history_no_download_id_filter(self, analyzer):
        """Test finding indexer without download ID filter"""
        history = [
            {
                "eventType": "grabbed",
                "downloadId": "different123",
                "data": {"indexer": "Nyaa"},
            }
        ]

        indexer = analyzer._find_indexer_from_history(history, None)
        assert indexer == "Nyaa"


class TestDecisionLogic:
    """Test decision making logic in detail"""

    @pytest.fixture
    def decision_analyzer(self, mock_config):
        """Analyzer specifically for decision testing"""
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "decisions.force_import_threshold": 15,  # Higher threshold for testing
            "trackers.private": ["privatehd"],
            "trackers.public": ["nyaa"],
        }.get(key, default)

        sonarr_client = MagicMock()
        return ScoreAnalyzer(config, sonarr_client)

    def test_force_import_exactly_at_threshold(self, decision_analyzer):
        """Test force import when exactly at threshold"""
        decision = decision_analyzer._make_decision(
            grab_score=95,
            current_score=80,  # Difference = 15 (exactly at threshold)
            is_private_tracker=False,
            is_public_tracker=True,
            is_unknown_tracker=False,
            grab_formats=["HDR"],
            current_formats=["x265"],
            missing_formats=["HDR"],
            extra_formats=[],
            indexer="public_tracker",
        )

        assert decision["action"] == "force_import"
        assert "15 points higher" in decision["reasoning"]

    def test_force_import_above_threshold(self, decision_analyzer):
        """Test force import when above threshold"""
        decision = decision_analyzer._make_decision(
            grab_score=100,
            current_score=80,  # Difference = 20 (above threshold)
            is_private_tracker=False,
            is_public_tracker=True,
            is_unknown_tracker=False,
            grab_formats=["HDR", "Atmos"],
            current_formats=["x265"],
            missing_formats=["HDR", "Atmos"],
            extra_formats=[],
            indexer="public_tracker",
        )

        assert decision["action"] == "force_import"
        assert "20 points higher" in decision["reasoning"]
        assert "Missing formats" in decision["reasoning"]

    def test_remove_public_tracker_exactly_at_negative_threshold(
        self, decision_analyzer
    ):
        """Test removing public tracker exactly at negative threshold"""
        decision = decision_analyzer._make_decision(
            grab_score=65,
            current_score=80,  # Difference = -15 (exactly at negative threshold)
            is_private_tracker=False,
            is_public_tracker=True,
            is_unknown_tracker=False,
            grab_formats=["x265"],
            current_formats=["HDR"],
            missing_formats=[],
            extra_formats=["HDR"],
            indexer="public_tracker",
        )

        # At exactly -15 threshold, it's within tolerance, so should wait
        assert decision["action"] == "wait"
        assert "within tolerance threshold" in decision["reasoning"]

    def test_keep_private_tracker_below_threshold(self, decision_analyzer):
        """Test keeping private tracker despite low score"""
        decision = decision_analyzer._make_decision(
            grab_score=50,
            current_score=100,  # Difference = -50 (well below threshold)
            is_private_tracker=True,
            is_public_tracker=False,
            is_unknown_tracker=False,
            grab_formats=["x265"],
            current_formats=["HDR", "Atmos"],
            missing_formats=[],
            extra_formats=["HDR", "Atmos"],
            indexer="private_tracker",
        )

        assert decision["action"] == "keep"
        assert "Private tracker protection" in decision["reasoning"]

    def test_wait_just_below_threshold(self, decision_analyzer):
        """Test waiting when just below threshold"""
        decision = decision_analyzer._make_decision(
            grab_score=94,
            current_score=80,  # Difference = 14 (below 15 threshold)
            is_private_tracker=False,
            is_public_tracker=True,
            is_unknown_tracker=False,
            grab_formats=["HDR"],
            current_formats=["x265"],
            missing_formats=[],
            extra_formats=[],
            indexer="public_tracker",
        )

        assert decision["action"] == "wait"
        assert "within tolerance threshold" in decision["reasoning"]

    def test_wait_just_above_negative_threshold(self, decision_analyzer):
        """Test waiting when just above negative threshold"""
        decision = decision_analyzer._make_decision(
            grab_score=66,
            current_score=80,  # Difference = -14 (above -15 threshold)
            is_private_tracker=False,
            is_public_tracker=True,
            is_unknown_tracker=False,
            grab_formats=["x265"],
            current_formats=["HDR"],
            missing_formats=[],
            extra_formats=[],
            indexer="public_tracker",
        )

        assert decision["action"] == "wait"
        assert "within tolerance threshold" in decision["reasoning"]


class TestEdgeCases:
    """Test edge cases and error conditions"""

    def test_zero_scores(self, analyzer):
        """Test handling of zero scores"""
        decision = analyzer._make_decision(
            grab_score=0,
            current_score=0,
            is_private_tracker=False,
            is_public_tracker=True,
            is_unknown_tracker=False,
            grab_formats=[],
            current_formats=[],
            missing_formats=[],
            extra_formats=[],
            indexer="public_tracker",
        )

        assert decision["action"] == "wait"

    def test_negative_scores(self, analyzer):
        """Test handling of negative scores"""
        decision = analyzer._make_decision(
            grab_score=-10,
            current_score=0,  # Difference = -10 (within tolerance)
            is_private_tracker=False,
            is_public_tracker=True,
            is_unknown_tracker=False,
            grab_formats=[],
            current_formats=[],
            missing_formats=[],
            extra_formats=[],
            indexer="public_tracker",
        )

        # -10 diff is within tolerance threshold (15), so should wait
        assert decision["action"] == "wait"
        assert "within tolerance threshold" in decision["reasoning"]

    def test_very_high_scores(self, analyzer):
        """Test handling of very high scores"""
        decision = analyzer._make_decision(
            grab_score=1000,
            current_score=500,
            is_private_tracker=False,
            is_public_tracker=True,
            is_unknown_tracker=False,
            grab_formats=["HDR", "Atmos", "DV"],
            current_formats=["x265"],
            missing_formats=["HDR", "Atmos", "DV"],
            extra_formats=[],
            indexer="public_tracker",
        )

        assert decision["action"] == "force_import"

    def test_missing_episode_data(self, analyzer):
        """Test handling queue item with missing episode data"""
        queue_item = {"id": 1, "downloadId": "test123", "status": "completed"}

        analyzer.sonarr_client.get_history_for_episode.return_value = []

        decision = analyzer.analyze_queue_item(queue_item)

        assert isinstance(decision, Decision)
        assert decision.action == "monitor"


@pytest.mark.unit
class TestScoreAnalyzerIntegration:
    """Integration-style tests within the analyzer"""

    def test_complete_analysis_workflow(self, analyzer, mock_sonarr_queue_item):
        """Test complete analysis workflow with all components"""
        # Setup complex scenario
        analyzer.sonarr_client.get_history_for_episode.return_value = [
            {
                "eventType": "grabbed",
                "downloadId": "abc123def456",
                "customFormatScore": 120,
                "customFormats": [
                    {"id": 1, "name": "HDR10"},
                    {"id": 2, "name": "Atmos"},
                    {"id": 3, "name": "x265"},
                ],
                "data": {"indexer": "BeyondHD"},
            }
        ]

        # Mock current file (lower quality)
        mock_response = MagicMock()
        mock_response.json.return_value = {"hasFile": True, "episodeFileId": 50}
        analyzer.sonarr_client._make_request.return_value = mock_response
        analyzer.sonarr_client.get_episode_file.return_value = {
            "customFormatScore": 50,
            "customFormats": [{"name": "x264"}],
        }

        decision = analyzer.analyze_queue_item(mock_sonarr_queue_item)

        # Verify decision
        assert decision.action == "force_import"
        assert decision.grab_score == 120
        assert decision.current_score == 50
        assert decision.score_difference == 70
        assert decision.is_private_tracker is True
        assert "HDR10" in decision.grab_formats
        assert "Atmos" in decision.grab_formats
        assert "x265" in decision.grab_formats
        assert "x264" in decision.current_formats
        assert "HDR10" in decision.missing_formats
        assert "Atmos" in decision.missing_formats

    def test_analyze_matched_by_id_force_import(self, analyzer):
        """Test force import when release matched by ID"""
        queue_item = {
            "id": 1,
            "downloadId": "test-download-123",
            "episode": {"id": 100},
            "series": {"id": 1},
            "statusMessages": [{"messages": ["Release was matched to series by ID"]}],
        }

        decision = analyzer.analyze_queue_item(queue_item)

        assert decision.action == "force_import"
        assert "identified by ID" in decision.reasoning

    def test_analyze_import_blocked_detection(self, analyzer):
        """Test that importBlocked state is detected as stuck"""
        queue_item = {
            "id": 1,
            "trackedDownloadState": "importBlocked",
            "episode": {"id": 100},
            "series": {"id": 1},
            "statusMessages": [{"messages": ["Series title mismatch"]}],
        }

        # This would be caught by _identify_stuck_items, but let's test the analysis
        decision = analyzer.analyze_queue_item(queue_item)

        # Should not force import for title mismatch without more context
        assert (
            decision.action in ["monitor", "wait"]
            or "mismatch" in decision.reasoning.lower()
        )


class TestCrossContentMismatchAnalysis:
    """Regression coverage for downloads grabbed against the wrong owner."""

    @staticmethod
    def _queue(download_id="B9DA0D66DAF9808DABD98D13FA4DFE0AAC4BCBCA"):
        return {
            "id": 534525973,
            "downloadId": download_id,
            "outputPath": "/downloads/Ghost.in.the.Shell.2026.S01E03",
            "series": {"id": 316, "title": "THE GHOST IN THE SHELL"},
            "episode": {
                "id": 10782,
                "seasonNumber": 1,
                "episodeNumber": 3,
            },
            "statusMessages": [],
        }

    @staticmethod
    def _wrong_history(download_id=None):
        return [
            {
                "eventType": "grabbed",
                "downloadId": download_id or "b9da0d66daf9808dabd98d13fa4dfe0aac4bcbca",
                "seriesId": 52,
                "episodeId": 1992,
                "customFormatScore": 1404,
                "customFormats": [{"name": "Wrong target score"}],
                "data": {"indexer": "SeedPool"},
            }
        ]

    @staticmethod
    def _candidate(score=1404):
        return {
            "path": "/downloads/Ghost.in.the.Shell.2026.S01E03/episode.mkv",
            "series": {"id": 316},
            "episodes": [{"id": 10782}],
            "customFormatScore": score,
            "customFormats": [{"name": "Correct target score"}],
        }

    def _prepare(self, analyzer, score=1404, current_score=1404):
        analyzer.sonarr_client.get_history_for_episode.return_value = []
        analyzer.sonarr_client.get_history_for_download.return_value = (
            self._wrong_history()
        )
        analyzer.sonarr_client.get_manual_import_candidates.return_value = [
            self._candidate(score)
        ]
        return patch.object(
            analyzer,
            "_get_current_file_evidence",
            return_value=(current_score, ["Current format"], None),
        )

    def test_ghost_in_the_shell_equal_score_clears_only_arr_queue(self, analyzer):
        """The observed cross-series hash uses the correct candidate score."""
        with self._prepare(analyzer):
            decision = analyzer.analyze_queue_item(self._queue())

        assert decision.action == "keep"
        assert decision.grab_score == 1404
        assert decision.current_score == 1404
        assert decision.score_difference == 0
        assert "cross-series mismatch" in decision.reasoning.lower()
        assert "52" in decision.reasoning and "316" in decision.reasoning

    def test_better_sonarr_candidate_forces_import(self, analyzer):
        with self._prepare(analyzer, score=1414, current_score=1404):
            decision = analyzer.analyze_queue_item(self._queue())

        assert decision.action == "force_import"
        assert decision.grab_score == 1414
        assert decision.score_difference == 10
        assert decision.grab_formats == ["Correct target score"]

    def test_download_id_matching_is_case_insensitive(self, analyzer):
        queue = self._queue(download_id="b9da0d66daf9808dabd98d13fa4dfe0aac4bcbca")
        analyzer.sonarr_client.get_history_for_episode.return_value = (
            self._wrong_history(download_id=queue["downloadId"].upper())
        )
        with patch.object(
            analyzer, "_get_current_file_evidence", return_value=(1404, [], None)
        ):
            decision = analyzer.analyze_queue_item(queue)

        assert decision.action == "wait"
        analyzer.sonarr_client.get_history_for_download.assert_not_called()

    def test_same_owner_global_history_is_normal_fallback(self, analyzer):
        queue = self._queue()
        analyzer.sonarr_client.get_history_for_episode.return_value = []
        analyzer.sonarr_client.get_history_for_download.return_value = [
            {
                "eventType": "grabbed",
                "downloadId": queue["downloadId"].lower(),
                "seriesId": 316,
                "episodeId": 99999,
                "customFormatScore": 1414,
                "customFormats": [{"name": "Season pack"}],
                "data": {"indexer": "SeedPool"},
            }
        ]
        with patch.object(
            analyzer, "_get_current_file_evidence", return_value=(1404, [], None)
        ):
            decision = analyzer.analyze_queue_item(queue)

        assert decision.action == "force_import"
        assert "cross-series mismatch" not in decision.reasoning.lower()
        analyzer.sonarr_client.get_manual_import_candidates.assert_not_called()

    def test_multiple_matching_season_pack_candidates_are_exceptional(
        self, analyzer, caplog
    ):
        analyzer.sonarr_client.get_history_for_episode.return_value = []
        analyzer.sonarr_client.get_history_for_download.return_value = (
            self._wrong_history()
        )
        analyzer.sonarr_client.get_manual_import_candidates.return_value = [
            self._candidate(1414),
            {**self._candidate(1420), "path": "/downloads/second.mkv"},
        ]
        with patch.object(
            analyzer, "_get_current_file_evidence", return_value=(1404, [], None)
        ), caplog.at_level("WARNING"):
            decision = analyzer.analyze_queue_item(self._queue())

        assert decision.action == "monitor"
        assert "2 matching manual-import candidates" in decision.reasoning
        assert "2 matching manual-import candidates" in caplog.text

    def test_multiple_wrong_owner_history_rows_are_exceptional(self, analyzer, caplog):
        analyzer.sonarr_client.get_history_for_episode.return_value = []
        history = self._wrong_history()
        history.append(
            {
                **history[0],
                "episodeId": 1993,
                "sourceTitle": "Wrong.Series.S01.Season.Pack",
            }
        )
        analyzer.sonarr_client.get_history_for_download.return_value = history
        with patch.object(
            analyzer, "_get_current_file_evidence", return_value=(1404, [], None)
        ), caplog.at_level("WARNING"):
            decision = analyzer.analyze_queue_item(self._queue())

        assert decision.action == "monitor"
        assert "multiple grabbed history" in decision.reasoning.lower()
        assert "season pack" in decision.reasoning.lower()

    def test_null_candidate_owner_is_exceptional(self, analyzer, caplog):
        analyzer.sonarr_client.get_history_for_episode.return_value = []
        analyzer.sonarr_client.get_history_for_download.return_value = (
            self._wrong_history()
        )
        analyzer.sonarr_client.get_manual_import_candidates.return_value = [
            {**self._candidate(), "series": None}
        ]
        with patch.object(
            analyzer, "_get_current_file_evidence", return_value=(1404, [], None)
        ), caplog.at_level("WARNING"):
            decision = analyzer.analyze_queue_item(self._queue())

        assert decision.action == "monitor"
        assert "0 matching manual-import candidates" in decision.reasoning

    def test_current_file_api_error_is_exceptional(self, analyzer, caplog):
        analyzer.sonarr_client.get_history_for_episode.return_value = []
        analyzer.sonarr_client.get_history_for_download.return_value = (
            self._wrong_history()
        )
        analyzer.sonarr_client.get_manual_import_candidates.return_value = [
            self._candidate(2000)
        ]
        analyzer.sonarr_client._make_request.side_effect = RuntimeError(
            "current file unavailable"
        )

        with caplog.at_level("WARNING"):
            decision = analyzer.analyze_queue_item(self._queue())

        assert decision.action == "monitor"
        assert "current file lookup failed" in decision.reasoning.lower()
        assert "current file unavailable" in caplog.text

    @pytest.mark.parametrize("candidate_score", [None, "1404", True])
    def test_nonnumeric_candidate_score_is_exceptional(self, analyzer, candidate_score):
        with self._prepare(analyzer, score=candidate_score):
            decision = analyzer.analyze_queue_item(self._queue())

        assert decision.action == "monitor"
        assert "nonnumeric" in decision.reasoning.lower()

    def test_missing_download_history_is_exceptional(self, analyzer, caplog):
        analyzer.sonarr_client.get_history_for_episode.return_value = []
        analyzer.sonarr_client.get_history_for_download.return_value = []
        with patch.object(
            analyzer, "_get_current_file_evidence", return_value=(1404, [], None)
        ), caplog.at_level("WARNING"):
            decision = analyzer.analyze_queue_item(self._queue())

        assert decision.action == "monitor"
        assert "no grabbed history" in decision.reasoning.lower()
        assert "No grabbed history" in caplog.text

    def test_download_history_api_error_is_exceptional(self, analyzer, caplog):
        analyzer.sonarr_client.get_history_for_episode.return_value = []
        analyzer.sonarr_client.get_history_for_download.side_effect = RuntimeError(
            "history unavailable"
        )
        with patch.object(
            analyzer, "_get_current_file_evidence", return_value=(1404, [], None)
        ), caplog.at_level("WARNING"):
            decision = analyzer.analyze_queue_item(self._queue())

        assert decision.action == "monitor"
        assert "history lookup failed" in decision.reasoning.lower()
        assert "history unavailable" in caplog.text

    def test_manual_candidate_api_error_is_exceptional(self, analyzer, caplog):
        analyzer.sonarr_client.get_history_for_episode.return_value = []
        analyzer.sonarr_client.get_history_for_download.return_value = (
            self._wrong_history()
        )
        analyzer.sonarr_client.get_manual_import_candidates.side_effect = RuntimeError(
            "manual import unavailable"
        )
        with patch.object(
            analyzer, "_get_current_file_evidence", return_value=(1404, [], None)
        ), caplog.at_level("WARNING"):
            decision = analyzer.analyze_queue_item(self._queue())

        assert decision.action == "monitor"
        assert "candidate lookup failed" in decision.reasoning.lower()
        assert "manual import unavailable" in caplog.text


class TestRadarrAnalyzer:
    """Test ScoreAnalyzer in Radarr mode"""

    @pytest.fixture
    def radarr_analyzer(self, mock_radarr_config):
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "decisions.force_import_threshold": 10,
            "trackers.private": mock_radarr_config["trackers"]["private"],
            "trackers.public": mock_radarr_config["trackers"]["public"],
        }.get(key, default)

        sonarr_client = MagicMock()
        sonarr_client.app_type = "radarr"
        return ScoreAnalyzer(config, sonarr_client)

    def test_radarr_app_type(self, radarr_analyzer):
        """Test that Radarr analyzer has correct app_type"""
        assert radarr_analyzer.app_type == "radarr"

    def test_radarr_analyze_queue_item_extracts_movie(
        self, radarr_analyzer, mock_radarr_queue_item
    ):
        """Test that Radarr analyzes movie field instead of episode"""
        radarr_analyzer.sonarr_client.get_history_for_episode.return_value = []

        mock_response = MagicMock()
        mock_response.json.return_value = {"hasFile": False}
        radarr_analyzer.sonarr_client._make_request.return_value = mock_response

        decision = radarr_analyzer.analyze_queue_item(mock_radarr_queue_item)

        assert isinstance(decision, Decision)
        # Should use movieId=50 for history lookup
        radarr_analyzer.sonarr_client.get_history_for_episode.assert_called_once_with(
            50
        )

    def test_radarr_matched_by_movie_id(self, radarr_analyzer):
        """Test force import for 'matched to movie by id'"""
        queue_item = {
            "id": 1,
            "downloadId": "test-download-123",
            "movie": {"id": 50, "title": "Test Movie"},
            "statusMessages": [{"messages": ["Release was matched to movie by ID"]}],
        }

        decision = radarr_analyzer.analyze_queue_item(queue_item)

        assert decision.action == "force_import"
        assert "Movie identified by ID" in decision.reasoning

    def test_radarr_get_current_file_details(self, radarr_analyzer):
        """Test Radarr uses /movie/{id} and movieFileId"""
        mock_response = MagicMock()
        mock_response.json.return_value = {"hasFile": True, "movieFileId": 42}
        radarr_analyzer.sonarr_client._make_request.return_value = mock_response
        radarr_analyzer.sonarr_client.get_episode_file.return_value = {
            "customFormatScore": 90,
            "customFormats": [{"name": "Remux"}],
        }

        score, formats = radarr_analyzer.get_current_file_details(50)

        radarr_analyzer.sonarr_client._make_request.assert_called_once_with(
            "GET", "/movie/50"
        )
        radarr_analyzer.sonarr_client.get_episode_file.assert_called_once_with(42)
        assert score == 90
        assert "Remux" in formats

    @pytest.mark.parametrize(
        ("candidate_score", "current_score", "expected_action"),
        [(100, 100, "keep"), (90, 100, "keep"), (110, 100, "force_import")],
    )
    def test_radarr_cross_movie_mismatch_uses_correct_candidate_score(
        self,
        radarr_analyzer,
        mock_radarr_queue_item,
        candidate_score,
        current_score,
        expected_action,
    ):
        """Radarr mismatch decisions use the candidate for the queued movie."""
        radarr_analyzer.sonarr_client.get_history_for_episode.return_value = []
        radarr_analyzer.sonarr_client.get_history_for_download.return_value = [
            {
                "eventType": "grabbed",
                "downloadId": mock_radarr_queue_item["downloadId"].upper(),
                "movieId": 99,
                "customFormatScore": 9999,
                "customFormats": [{"name": "Wrong movie score"}],
                "data": {"indexer": "SeedPool"},
            }
        ]
        radarr_analyzer.sonarr_client.get_manual_import_candidates.return_value = [
            {
                "path": "/downloads/Test.Movie.2024/movie.mkv",
                "movie": {"id": 50},
                "customFormatScore": candidate_score,
                "customFormats": [{"name": "Correct movie score"}],
            }
        ]

        with patch.object(
            radarr_analyzer,
            "_get_current_file_evidence",
            return_value=(current_score, ["Current format"], None),
        ):
            decision = radarr_analyzer.analyze_queue_item(mock_radarr_queue_item)

        assert decision.action == expected_action
        assert decision.grab_score == candidate_score
        assert decision.grab_formats == ["Correct movie score"]
        assert "cross-movie mismatch" in decision.reasoning.lower()

    def test_conflicting_global_owner_history_is_exceptional(
        self, radarr_analyzer, mock_radarr_queue_item, caplog
    ):
        radarr_analyzer.sonarr_client.get_history_for_episode.return_value = []
        radarr_analyzer.sonarr_client.get_history_for_download.return_value = [
            {
                "eventType": "grabbed",
                "downloadId": mock_radarr_queue_item["downloadId"],
                "movieId": 98,
            },
            {
                "eventType": "grabbed",
                "downloadId": mock_radarr_queue_item["downloadId"],
                "movieId": 99,
            },
        ]
        with patch.object(
            radarr_analyzer,
            "_get_current_file_evidence",
            return_value=(100, [], None),
        ), caplog.at_level("WARNING"):
            decision = radarr_analyzer.analyze_queue_item(mock_radarr_queue_item)

        assert decision.action == "monitor"
        assert "conflicting" in decision.reasoning.lower()
        assert "conflicting" in caplog.text.lower()
