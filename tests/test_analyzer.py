"""
Unit tests for ScoreAnalyzer and decision making logic.
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
from src.core.analyzer import ScoreAnalyzer, Decision, FormatAnalysis
from src.config.loader import ConfigLoader


class TestFormatAnalysis:
    """Test FormatAnalysis NamedTuple"""
    
    def test_format_analysis_creation(self):
        """Test FormatAnalysis creation with score and formats"""
        analysis = FormatAnalysis(100, ['HDR', 'Atmos'])
        assert analysis.total_score == 100
        assert analysis.format_names == ['HDR', 'Atmos']


@pytest.fixture
def analyzer(mock_config):
    """Create ScoreAnalyzer instance for testing"""
    config = MagicMock()
    config.get.side_effect = lambda key, default=None: {
        'decisions.force_import_threshold': mock_config['decisions']['force_import_threshold'],
        'trackers.private': mock_config['trackers']['private'],
        'trackers.public': mock_config['trackers']['public']
    }.get(key, default)
    
    sonarr_client = MagicMock()
    return ScoreAnalyzer(config, sonarr_client)


class TestScoreAnalyzer:
    """Test ScoreAnalyzer class"""
    
    def test_init(self, analyzer, mock_config):
        """Test analyzer initialization"""
        assert analyzer.force_import_threshold == 10
        assert 'privatehd' in analyzer.private_trackers
        assert 'nyaa' in analyzer.public_trackers
    
    def test_analyze_custom_formats_empty(self, analyzer):
        """Test analyzing empty custom formats list"""
        result = analyzer.analyze_custom_formats([], 1)
        assert result.total_score == 0
        assert result.format_names == []
    
    def test_analyze_custom_formats_with_scores(self, analyzer):
        """Test analyzing custom formats with scores"""
        custom_formats = [
            {'id': 1, 'name': 'HDR'},
            {'id': 2, 'name': 'Atmos'}
        ]
        
        analyzer.sonarr_client.get_custom_format_scores.return_value = {1: 100, 2: 50}
        
        result = analyzer.analyze_custom_formats(custom_formats, 1)
        
        assert result.total_score == 150
        assert 'HDR' in result.format_names
        assert 'Atmos' in result.format_names
    
    def test_is_private_tracker_true(self, analyzer):
        """Test private tracker detection - positive case"""
        assert analyzer.is_private_tracker('PrivateHD') is True
        assert analyzer.is_private_tracker('BeyondHD-API') is True
        assert analyzer.is_private_tracker('PassThePopcorn') is True
    
    def test_is_private_tracker_false(self, analyzer):
        """Test private tracker detection - negative case"""
        assert analyzer.is_private_tracker('Nyaa') is False
        assert analyzer.is_private_tracker('RARBG') is False
        assert analyzer.is_private_tracker('') is False
        assert analyzer.is_private_tracker(None) is False
    
    def test_find_grab_info_found(self, analyzer):
        """Test finding grab info from history"""
        history = [
            {
                'eventType': 'grabbed',
                'downloadId': 'test123',
                'customFormatScore': 75,
                'customFormats': [{'name': 'HDR'}, {'name': 'x265'}]
            },
            {
                'eventType': 'downloadFolderImported',
                'downloadId': 'test123'
            }
        ]
        
        score, formats = analyzer.find_grab_info(history, 'test123')
        
        assert score == 75
        assert 'HDR' in formats
        assert 'x265' in formats
    
    def test_find_grab_info_not_found(self, analyzer):
        """Test finding grab info when not in history"""
        history = [
            {
                'eventType': 'downloadFolderImported',
                'downloadId': 'test123'
            }
        ]
        
        score, formats = analyzer.find_grab_info(history, 'test123')
        
        assert score is None
        assert formats == []
    
    def test_find_grab_info_calculate_score(self, analyzer):
        """Test calculating score when not in history"""
        history = [
            {
                'eventType': 'grabbed',
                'downloadId': 'test123',
                'customFormatScore': 0,
                'customFormats': [{'id': 1, 'name': 'HDR'}]
            }
        ]
        
        analyzer.sonarr_client.get_custom_format_scores.return_value = {1: 100}
        
        score, formats = analyzer.find_grab_info(history, 'test123', series_id=10)
        
        assert score == 100
        assert 'HDR' in formats
    
    def test_get_current_file_details_no_file(self, analyzer):
        """Test getting current file details when no file exists"""
        # Mock episode with no file
        mock_response = MagicMock()
        mock_response.json.return_value = {'hasFile': False}
        analyzer.sonarr_client._make_request.return_value = mock_response
        
        score, formats = analyzer.get_current_file_details(100)
        
        assert score is None
        assert formats == []
    
    def test_get_current_file_details_with_file(self, analyzer):
        """Test getting current file details when file exists"""
        # Mock episode with file
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'hasFile': True,
            'episodeFileId': 50
        }
        analyzer.sonarr_client._make_request.return_value = mock_response
        
        # Mock file data
        analyzer.sonarr_client.get_episode_file.return_value = {
            'customFormatScore': 80,
            'customFormats': [{'name': 'x265'}, {'name': 'HDR'}]
        }
        
        score, formats = analyzer.get_current_file_details(100)
        
        assert score == 80
        assert 'x265' in formats
        assert 'HDR' in formats
    
    @patch('src.core.analyzer.logger')
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
            grab_formats=['HDR', 'Atmos'],
            current_formats=['x265'],
            missing_formats=['HDR', 'Atmos'],
            extra_formats=[]
        )
        
        assert decision['action'] == 'force_import'
        assert 'higher than current file' in decision['reasoning']
    
    def test_make_decision_keep_private_tracker(self, analyzer):
        """Test decision to keep private tracker despite lower score"""
        decision = analyzer._make_decision(
            grab_score=80,
            current_score=100,
            is_private_tracker=True,
            grab_formats=['x265'],
            current_formats=['HDR', 'Atmos'],
            missing_formats=[],
            extra_formats=['HDR', 'Atmos']
        )
        
        assert decision['action'] == 'keep'
        assert 'Private tracker protection' in decision['reasoning']
    
    def test_make_decision_remove_public_tracker(self, analyzer):
        """Test decision to remove public tracker with lower score"""
        decision = analyzer._make_decision(
            grab_score=80,
            current_score=100,
            is_private_tracker=False,
            grab_formats=['x265'],
            current_formats=['HDR', 'Atmos'],
            missing_formats=[],
            extra_formats=['HDR', 'Atmos']
        )
        
        assert decision['action'] == 'remove'
        assert 'Public tracker with lower score' in decision['reasoning']
    
    def test_make_decision_wait_within_threshold(self, analyzer):
        """Test decision to wait when score difference is within threshold"""
        decision = analyzer._make_decision(
            grab_score=85,
            current_score=80,
            is_private_tracker=False,
            grab_formats=['x265'],
            current_formats=['x264'],
            missing_formats=[],
            extra_formats=[]
        )
        
        assert decision['action'] == 'wait'
        assert 'within tolerance threshold' in decision['reasoning']
    
    def test_make_decision_monitor_missing_scores(self, analyzer):
        """Test decision to monitor when scores are missing"""
        decision = analyzer._make_decision(
            grab_score=None,
            current_score=80,
            is_private_tracker=False,
            grab_formats=[],
            current_formats=['x265'],
            missing_formats=[],
            extra_formats=[]
        )
        
        assert decision['action'] == 'monitor'
        assert 'Unable to determine scores' in decision['reasoning']
    
    def test_detect_repeated_grabs_normal(self, analyzer):
        """Test detecting repeated grabs - normal case"""
        history = [
            {'eventType': 'grabbed', 'downloadId': 'grab1'},
            {'eventType': 'downloadFolderImported', 'downloadId': 'grab1'}
        ]
        
        analyzer.sonarr_client.get_history_for_episode.return_value = history
        
        result = analyzer.detect_repeated_grabs(100)
        assert result == []
    
    def test_detect_repeated_grabs_excessive(self, analyzer):
        """Test detecting repeated grabs - excessive case"""
        history = [
            {'eventType': 'grabbed', 'downloadId': 'grab1'},
            {'eventType': 'grabbed', 'downloadId': 'grab2'},
            {'eventType': 'grabbed', 'downloadId': 'grab3'},
            {'eventType': 'downloadFolderImported', 'downloadId': 'grab1'}
        ]
        
        analyzer.sonarr_client.get_history_for_episode.return_value = history
        
        result = analyzer.detect_repeated_grabs(100)
        
        # Should find 2 unimported grabs
        assert len(result) == 2
        assert any(grab['downloadId'] == 'grab2' for grab in result)
        assert any(grab['downloadId'] == 'grab3' for grab in result)
    
    @patch('src.core.analyzer.logger')
    def test_detect_repeated_grabs_error(self, mock_logger, analyzer):
        """Test error handling in detect_repeated_grabs"""
        analyzer.sonarr_client.get_history_for_episode.side_effect = Exception("API Error")
        
        result = analyzer.detect_repeated_grabs(100)
        
        assert result == []
        mock_logger.error.assert_called_once()
    
    def test_analyze_queue_item_complete_flow(self, analyzer, mock_sonarr_queue_item):
        """Test complete analysis flow for a queue item"""
        # Setup mocks for complete flow
        analyzer.sonarr_client.get_history_for_episode.return_value = [
            {
                'eventType': 'grabbed',
                'downloadId': 'abc123def456',
                'customFormatScore': 100,
                'customFormats': [{'name': 'HDR'}, {'name': 'Atmos'}],
                'data': {'indexer': 'PrivateHD'}
            }
        ]
        
        # Mock current file details
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'hasFile': True,
            'episodeFileId': 50
        }
        analyzer.sonarr_client._make_request.return_value = mock_response
        analyzer.sonarr_client.get_episode_file.return_value = {
            'customFormatScore': 80,
            'customFormats': [{'name': 'x265'}]
        }
        
        decision = analyzer.analyze_queue_item(mock_sonarr_queue_item)
        
        assert isinstance(decision, Decision)
        assert decision.action == 'force_import'
        assert decision.grab_score == 100
        assert decision.current_score == 80
        assert decision.score_difference == 20
        assert decision.is_private_tracker is True
        assert 'HDR' in decision.grab_formats
        assert 'Atmos' in decision.grab_formats
        assert 'x265' in decision.current_formats
    
    def test_find_indexer_from_history_found(self, analyzer):
        """Test finding indexer from history"""
        history = [
            {
                'eventType': 'grabbed',
                'downloadId': 'test123',
                'data': {'indexer': 'BeyondHD'}
            }
        ]
        
        indexer = analyzer._find_indexer_from_history(history, 'test123')
        assert indexer == 'BeyondHD'
    
    def test_find_indexer_from_history_not_found(self, analyzer):
        """Test finding indexer from history when not found"""
        history = [
            {
                'eventType': 'downloadFolderImported',
                'downloadId': 'test123'
            }
        ]
        
        indexer = analyzer._find_indexer_from_history(history, 'test123')
        assert indexer == ''
    
    def test_find_indexer_from_history_no_download_id_filter(self, analyzer):
        """Test finding indexer without download ID filter"""
        history = [
            {
                'eventType': 'grabbed',
                'downloadId': 'different123',
                'data': {'indexer': 'Nyaa'}
            }
        ]
        
        indexer = analyzer._find_indexer_from_history(history, None)
        assert indexer == 'Nyaa'


class TestDecisionLogic:
    """Test decision making logic in detail"""
    
    @pytest.fixture
    def decision_analyzer(self, mock_config):
        """Analyzer specifically for decision testing"""
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            'decisions.force_import_threshold': 15,  # Higher threshold for testing
            'trackers.private': ['privatehd'],
            'trackers.public': ['nyaa']
        }.get(key, default)
        
        sonarr_client = MagicMock()
        return ScoreAnalyzer(config, sonarr_client)
    
    def test_force_import_exactly_at_threshold(self, decision_analyzer):
        """Test force import when exactly at threshold"""
        decision = decision_analyzer._make_decision(
            grab_score=95,
            current_score=80,  # Difference = 15 (exactly at threshold)
            is_private_tracker=False,
            grab_formats=['HDR'],
            current_formats=['x265'],
            missing_formats=['HDR'],
            extra_formats=[]
        )
        
        assert decision['action'] == 'force_import'
        assert '15 points higher' in decision['reasoning']
    
    def test_force_import_above_threshold(self, decision_analyzer):
        """Test force import when above threshold"""
        decision = decision_analyzer._make_decision(
            grab_score=100,
            current_score=80,  # Difference = 20 (above threshold)
            is_private_tracker=False,
            grab_formats=['HDR', 'Atmos'],
            current_formats=['x265'],
            missing_formats=['HDR', 'Atmos'],
            extra_formats=[]
        )
        
        assert decision['action'] == 'force_import'
        assert '20 points higher' in decision['reasoning']
        assert 'Missing formats' in decision['reasoning']
    
    def test_remove_public_tracker_exactly_at_negative_threshold(self, decision_analyzer):
        """Test removing public tracker exactly at negative threshold"""
        decision = decision_analyzer._make_decision(
            grab_score=65,
            current_score=80,  # Difference = -15 (exactly at negative threshold)
            is_private_tracker=False,
            grab_formats=['x265'],
            current_formats=['HDR'],
            missing_formats=[],
            extra_formats=['HDR']
        )
        
        # At exactly -15 threshold, it's within tolerance, so should wait
        assert decision['action'] == 'wait'
        assert 'within tolerance threshold' in decision['reasoning']
    
    def test_keep_private_tracker_below_threshold(self, decision_analyzer):
        """Test keeping private tracker despite low score"""
        decision = decision_analyzer._make_decision(
            grab_score=50,
            current_score=100,  # Difference = -50 (well below threshold)
            is_private_tracker=True,
            grab_formats=['x265'],
            current_formats=['HDR', 'Atmos'],
            missing_formats=[],
            extra_formats=['HDR', 'Atmos']
        )
        
        assert decision['action'] == 'keep'
        assert 'Private tracker protection' in decision['reasoning']
    
    def test_wait_just_below_threshold(self, decision_analyzer):
        """Test waiting when just below threshold"""
        decision = decision_analyzer._make_decision(
            grab_score=94,
            current_score=80,  # Difference = 14 (below 15 threshold)
            is_private_tracker=False,
            grab_formats=['HDR'],
            current_formats=['x265'],
            missing_formats=[],
            extra_formats=[]
        )
        
        assert decision['action'] == 'wait'
        assert 'within tolerance threshold' in decision['reasoning']
    
    def test_wait_just_above_negative_threshold(self, decision_analyzer):
        """Test waiting when just above negative threshold"""
        decision = decision_analyzer._make_decision(
            grab_score=66,
            current_score=80,  # Difference = -14 (above -15 threshold)
            is_private_tracker=False,
            grab_formats=['x265'],
            current_formats=['HDR'],
            missing_formats=[],
            extra_formats=[]
        )
        
        assert decision['action'] == 'wait'
        assert 'within tolerance threshold' in decision['reasoning']


class TestEdgeCases:
    """Test edge cases and error conditions"""
    
    def test_zero_scores(self, analyzer):
        """Test handling of zero scores"""
        decision = analyzer._make_decision(
            grab_score=0,
            current_score=0,
            is_private_tracker=False,
            grab_formats=[],
            current_formats=[],
            missing_formats=[],
            extra_formats=[]
        )
        
        assert decision['action'] == 'wait'
    
    def test_negative_scores(self, analyzer):
        """Test handling of negative scores"""
        decision = analyzer._make_decision(
            grab_score=-10,
            current_score=0,  # Difference = -10 (within tolerance)
            is_private_tracker=False,
            grab_formats=[],
            current_formats=[],
            missing_formats=[],
            extra_formats=[]
        )
        
        # -10 diff is within tolerance threshold (15), so should wait
        assert decision['action'] == 'wait'
        assert 'within tolerance threshold' in decision['reasoning']
    
    def test_very_high_scores(self, analyzer):
        """Test handling of very high scores"""
        decision = analyzer._make_decision(
            grab_score=1000,
            current_score=500,
            is_private_tracker=False,
            grab_formats=['HDR', 'Atmos', 'DV'],
            current_formats=['x265'],
            missing_formats=['HDR', 'Atmos', 'DV'],
            extra_formats=[]
        )
        
        assert decision['action'] == 'force_import'
    
    def test_missing_episode_data(self, analyzer):
        """Test handling queue item with missing episode data"""
        queue_item = {
            'id': 1,
            'downloadId': 'test123',
            'status': 'completed'
        }
        
        analyzer.sonarr_client.get_history_for_episode.return_value = []
        
        decision = analyzer.analyze_queue_item(queue_item)
        
        assert isinstance(decision, Decision)
        assert decision.action == 'monitor'


@pytest.mark.unit
class TestScoreAnalyzerIntegration:
    """Integration-style tests within the analyzer"""
    
    def test_complete_analysis_workflow(self, analyzer, mock_sonarr_queue_item):
        """Test complete analysis workflow with all components"""
        # Setup complex scenario
        analyzer.sonarr_client.get_history_for_episode.return_value = [
            {
                'eventType': 'grabbed',
                'downloadId': 'abc123def456',
                'customFormatScore': 120,
                'customFormats': [
                    {'id': 1, 'name': 'HDR10'},
                    {'id': 2, 'name': 'Atmos'},
                    {'id': 3, 'name': 'x265'}
                ],
                'data': {'indexer': 'BeyondHD'}
            }
        ]
        
        # Mock current file (lower quality)
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'hasFile': True,
            'episodeFileId': 50
        }
        analyzer.sonarr_client._make_request.return_value = mock_response
        analyzer.sonarr_client.get_episode_file.return_value = {
            'customFormatScore': 50,
            'customFormats': [{'name': 'x264'}]
        }
        
        decision = analyzer.analyze_queue_item(mock_sonarr_queue_item)
        
        # Verify decision
        assert decision.action == 'force_import'
        assert decision.grab_score == 120
        assert decision.current_score == 50
        assert decision.score_difference == 70
        assert decision.is_private_tracker is True
        assert 'HDR10' in decision.grab_formats
        assert 'Atmos' in decision.grab_formats
        assert 'x265' in decision.grab_formats
        assert 'x264' in decision.current_formats
        assert 'HDR10' in decision.missing_formats
        assert 'Atmos' in decision.missing_formats