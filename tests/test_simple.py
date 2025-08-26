"""
Simple tests to verify the testing framework works.
"""

import pytest
from unittest.mock import patch, MagicMock
from src.core.analyzer import Decision, FormatAnalysis


def test_decision_creation():
    """Test that Decision dataclass works correctly"""
    decision = Decision(
        action='force_import',
        grab_score=100,
        current_score=80,
        score_difference=20,
        reasoning='Test reasoning',
        grab_formats=['HDR'],
        current_formats=['x265'],
        missing_formats=['HDR'],
        extra_formats=[],
        is_private_tracker=False
    )
    
    assert decision.action == 'force_import'
    assert decision.grab_score == 100
    assert decision.score_difference == 20


def test_format_analysis():
    """Test FormatAnalysis creation"""
    analysis = FormatAnalysis(100, ['HDR', 'Atmos'])
    assert analysis.total_score == 100
    assert analysis.format_names == ['HDR', 'Atmos']


def test_mock_config_fixture(mock_config):
    """Test that mock_config fixture works"""
    assert mock_config['sonarr']['url'] == 'http://test-sonarr:8989'
    assert mock_config['webhook']['port'] == 8090


def test_environment_isolation():
    """Test that we can isolate environment properly"""
    with patch.dict('os.environ', {'TEST_VAR': 'test_value'}, clear=True):
        import os
        assert os.environ.get('TEST_VAR') == 'test_value'
        assert os.environ.get('SONARR_URL') is None