"""
Core functionality tests that work with the current codebase.
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
import requests_mock
from src.api.sonarr_client import SonarrClient, SonarrAPIError
from src.core.analyzer import ScoreAnalyzer, Decision


class TestWorkingSonarrClient:
    """Test SonarrClient functionality that we know works"""
    
    def test_sonarr_client_initialization(self, mock_config):
        """Test SonarrClient can be initialized"""
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            'sonarr.url': 'http://test:8989',
            'sonarr.api_key': 'test-key-123',
            'sonarr.timeout': 30
        }.get(key, default)
        
        client = SonarrClient(config)
        assert client.base_url == 'http://test:8989'
        assert client.api_key == 'test-key-123'
    
    def test_make_request_success(self, mock_config):
        """Test successful API request"""
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            'sonarr.url': 'http://test:8989',
            'sonarr.api_key': 'test-key',
            'sonarr.timeout': 30
        }.get(key, default)
        
        client = SonarrClient(config)
        
        with requests_mock.Mocker() as m:
            m.get('http://test:8989/api/v3/system/status', json={'version': '3.0.0'})
            
            response = client._make_request('GET', '/system/status')
            assert response.status_code == 200
    
    def test_api_error_handling(self, mock_config):
        """Test API error handling"""
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            'sonarr.url': 'http://test:8989',
            'sonarr.api_key': 'test-key',
            'sonarr.timeout': 30
        }.get(key, default)
        
        client = SonarrClient(config)
        
        with requests_mock.Mocker() as m:
            m.get('http://test:8989/api/v3/test', status_code=404)
            
            with pytest.raises(SonarrAPIError):
                client._make_request('GET', '/test')


class TestWorkingAnalyzer:
    """Test ScoreAnalyzer functionality"""
    
    def test_analyzer_initialization(self, mock_config):
        """Test ScoreAnalyzer can be initialized"""
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            'decisions.force_import_threshold': 10,
            'trackers.private': ['privatehd'],
            'trackers.public': ['nyaa']
        }.get(key, default)
        
        sonarr_client = MagicMock()
        analyzer = ScoreAnalyzer(config, sonarr_client)
        
        assert analyzer.force_import_threshold == 10
        assert 'privatehd' in analyzer.private_trackers
    
    def test_private_tracker_detection(self, mock_config):
        """Test private tracker detection works"""
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            'decisions.force_import_threshold': 10,
            'trackers.private': ['privatehd', 'beyondhd'],
            'trackers.public': ['nyaa']
        }.get(key, default)
        
        sonarr_client = MagicMock()
        analyzer = ScoreAnalyzer(config, sonarr_client)
        
        assert analyzer.is_private_tracker('PrivateHD') is True
        assert analyzer.is_private_tracker('BeyondHD-API') is True
        assert analyzer.is_private_tracker('Nyaa') is False
        assert analyzer.is_private_tracker('') is False
    
    def test_decision_making_force_import(self, mock_config):
        """Test force import decision logic"""
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            'decisions.force_import_threshold': 10,
            'trackers.private': ['privatehd'],
            'trackers.public': ['nyaa']
        }.get(key, default)
        
        sonarr_client = MagicMock()
        analyzer = ScoreAnalyzer(config, sonarr_client)
        
        decision = analyzer._make_decision(
            grab_score=100,
            current_score=80,  # Difference = 20 (above threshold of 10)
            is_private_tracker=False,
            is_public_tracker=True,
            is_unknown_tracker=False,
            grab_formats=['HDR'],
            current_formats=['x265'],
            missing_formats=['HDR'],
            extra_formats=[],
            indexer='public_tracker'
        )
        
        assert decision['action'] == 'force_import'
        assert '20 points higher' in decision['reasoning']
    
    def test_decision_making_private_tracker_protection(self, mock_config):
        """Test private tracker protection"""
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            'decisions.force_import_threshold': 10,
            'trackers.private': ['privatehd'],
            'trackers.public': ['nyaa']
        }.get(key, default)
        
        sonarr_client = MagicMock()
        analyzer = ScoreAnalyzer(config, sonarr_client)
        
        decision = analyzer._make_decision(
            grab_score=60,
            current_score=100,  # Grab is worse
            is_private_tracker=True,
            is_public_tracker=False,
            is_unknown_tracker=False,
            grab_formats=['x265'],
            current_formats=['HDR'],
            missing_formats=[],
            extra_formats=[],
            indexer='private_tracker'
        )
        
        assert decision['action'] == 'keep'
        assert 'Private tracker protection' in decision['reasoning']


class TestWorkingWebhookServer:
    """Test WebhookServer basic functionality"""
    
    def test_webhook_server_initialization(self, mock_config):
        """Test WebhookServer can be initialized"""
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            'webhook.secret': 'test-secret',
            'webhook.import_check_delay': 600
        }.get(key, default)
        
        monitor = MagicMock()
        
        with patch('src.api.webhook_server.RateLimiter'):
            from src.api.webhook_server import WebhookServer
            server = WebhookServer(monitor, config)
        
        assert server.webhook_secret == 'test-secret'
        assert server.max_requests_per_minute == 30
    
    def test_health_endpoint_works(self, mock_config):
        """Test health endpoint functionality"""
        config = MagicMock()
        config.get.return_value = None
        monitor = MagicMock()
        
        with patch('src.api.webhook_server.RateLimiter'):
            from src.api.webhook_server import WebhookServer
            server = WebhookServer(monitor, config)
            client = server.app.test_client()
        
        response = client.get('/health')
        assert response.status_code == 200
        
        data = response.get_json()
        assert data['status'] == 'healthy'
        assert data['service'] == 'Sonarr Import Monitor Webhook'


class TestBasicIntegration:
    """Test basic integration between components"""
    
    def test_analyzer_with_client(self, mock_config):
        """Test analyzer works with mocked client"""
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            'decisions.force_import_threshold': 10,
            'trackers.private': ['privatehd'],
            'trackers.public': ['nyaa']
        }.get(key, default)
        
        # Mock Sonarr client
        sonarr_client = MagicMock()
        sonarr_client.get_custom_format_scores.return_value = {1: 100, 2: 50}
        
        analyzer = ScoreAnalyzer(config, sonarr_client)
        
        # Test format analysis
        custom_formats = [
            {'id': 1, 'name': 'HDR'},
            {'id': 2, 'name': 'Atmos'}
        ]
        
        result = analyzer.analyze_custom_formats(custom_formats, 1)
        assert result.total_score == 150
        assert 'HDR' in result.format_names


def test_imports_work():
    """Test that all main modules can be imported"""
    from src.config.loader import ConfigLoader
    from src.api.sonarr_client import SonarrClient, SonarrAPIError
    from src.core.analyzer import ScoreAnalyzer, Decision
    from src.utils.decorators import retry, RateLimiter
    
    # If we get here, imports work
    assert True