import pytest
from unittest.mock import Mock, MagicMock, patch
import yaml
from datetime import datetime
import os
import tempfile

@pytest.fixture
def mock_config():
    """Provide test configuration"""
    return {
        'sonarr': {
            'url': 'http://test-sonarr:8989',
            'api_key': 'test-key-123456789012345678901234567890'
        },
        'webhook': {
            'secret': 'test-secret-123456789012345678901234567890',
            'port': 8090,
            'enabled': True
        },
        'decisions': {
            'force_import_threshold': 10,
            'remove_stuck_threshold_hours': 24
        },
        'trackers': {
            'private': ['privatehd', 'beyondhd', 'passthepopcorn'],
            'public': ['nyaa', 'rarbg', 'animetosho']
        },
        'logging': {
            'level': 'DEBUG',
            'format': 'text'
        }
    }

@pytest.fixture
def temp_config_file(mock_config):
    """Create temporary config file for testing"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump(mock_config, f)
        temp_path = f.name
    
    yield temp_path
    
    # Cleanup
    os.unlink(temp_path)

@pytest.fixture
def mock_sonarr_queue_item():
    """Mock Sonarr queue item"""
    return {
        'id': 1,
        'episode': {'id': 100, 'title': 'Test Episode'},
        'series': {'id': 10, 'title': 'Test Series'},
        'downloadId': 'abc123def456',
        'status': 'completed',
        'trackedDownloadState': 'importPending',
        'size': 1073741824,  # 1GB
        'downloadClient': 'qBittorrent',
        'indexer': 'TestTracker',
        'quality': {
            'quality': {'id': 6, 'name': '720p HDTV'},
            'customFormats': []
        }
    }

@pytest.fixture
def mock_sonarr_history_item():
    """Mock Sonarr history item"""
    return {
        'id': 1,
        'episodeId': 100,
        'seriesId': 10,
        'sourceTitle': 'Test.Episode.720p.HDTV.x264-GROUP',
        'quality': {
            'quality': {'id': 6, 'name': '720p HDTV'},
            'customFormats': [{'id': 1, 'name': 'TestFormat', 'score': 50}]
        },
        'customFormatScore': 50,
        'indexer': 'TestTracker',
        'downloadId': 'abc123def456',
        'eventType': 'grabbed',
        'data': {'indexer': 'TestTracker'}
    }

@pytest.fixture
def mock_sonarr_api():
    """Mock Sonarr API client with common responses"""
    mock = MagicMock()
    
    # Queue API responses
    mock.get_queue.return_value = []
    mock.get_queue_details.return_value = None
    
    # History API responses
    mock.get_history.return_value = []
    
    # Import API responses
    mock.force_import.return_value = {'success': True}
    mock.remove_from_queue.return_value = {'success': True}
    
    # Series API responses
    mock.get_series.return_value = {'id': 10, 'title': 'Test Series'}
    
    # Episode API responses
    mock.get_episode.return_value = {'id': 100, 'title': 'Test Episode'}
    
    # Custom formats
    mock.get_custom_formats.return_value = [
        {'id': 1, 'name': 'TestFormat', 'score': 50}
    ]
    
    return mock

@pytest.fixture
def mock_logger():
    """Mock logger for tests"""
    return MagicMock()

@pytest.fixture
def sample_webhook_payload():
    """Sample webhook payload from Sonarr"""
    return {
        'eventType': 'Grab',
        'series': {
            'id': 10,
            'title': 'Test Series',
            'path': '/data/tv/Test Series'
        },
        'episodes': [{
            'id': 100,
            'title': 'Test Episode',
            'seasonNumber': 1,
            'episodeNumber': 1
        }],
        'release': {
            'quality': 'HDTV-720p',
            'customFormats': ['TestFormat'],
            'customFormatScore': 50,
            'indexer': 'TestTracker',
            'size': 1073741824,
            'downloadClient': 'qBittorrent',
            'downloadId': 'abc123def456'
        }
    }

@pytest.fixture
def mock_requests():
    """Mock requests for API calls"""
    with patch('requests.Session') as mock_session:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        mock_response.raise_for_status.return_value = None
        
        mock_session.return_value.get.return_value = mock_response
        mock_session.return_value.post.return_value = mock_response
        mock_session.return_value.put.return_value = mock_response
        mock_session.return_value.delete.return_value = mock_response
        
        yield mock_session

@pytest.fixture
def mock_flask_app():
    """Mock Flask app for webhook testing"""
    from flask import Flask
    app = Flask(__name__)
    app.config['TESTING'] = True
    return app

@pytest.fixture
def test_env_vars():
    """Set up test environment variables"""
    test_vars = {
        'SONARR_URL': 'http://test-sonarr:8989',
        'SONARR_API_KEY': 'test-key-123456789012345678901234567890',
        'WEBHOOK_SECRET': 'test-secret-123456789012345678901234567890',
        'LOG_LEVEL': 'DEBUG'
    }
    
    # Store original values
    original_values = {}
    for key, value in test_vars.items():
        original_values[key] = os.environ.get(key)
        os.environ[key] = value
    
    yield test_vars
    
    # Restore original values
    for key, original_value in original_values.items():
        if original_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = original_value