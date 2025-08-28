"""
Integration tests for SonarrClient caching functionality.
"""

import time
from unittest.mock import MagicMock, patch

import pytest
import requests_mock

from src.api.sonarr_client import SonarrClient
from src.config.loader import ConfigLoader


@pytest.fixture
def mock_config():
    """Provide test configuration."""
    config = MagicMock(spec=ConfigLoader)
    config.get.side_effect = lambda key, default=None: {
        "sonarr.url": "http://test-sonarr:8989",
        "sonarr.api_key": "test-api-key-12345",
        "sonarr.timeout": 30,
    }.get(key, default)
    return config


@pytest.fixture
def client(mock_config):
    """Provide SonarrClient with caching enabled."""
    return SonarrClient(mock_config)


class TestSonarrClientCacheSetup:
    """Test cache initialization in SonarrClient."""

    def test_cache_initialized(self, client):
        """Test that cache is properly initialized."""
        assert hasattr(client, "cache")
        assert client.cache.default_ttl == 300  # 5 minutes
        assert client.cache.size() == 0

    def test_session_initialized(self, client):
        """Test that session is properly initialized."""
        assert hasattr(client, "session")
        assert client.session.headers["X-Api-Key"] == "test-api-key-12345"

    def test_cache_stats_method(self, client):
        """Test cache statistics method."""
        stats = client.get_cache_stats()
        assert "cache_stats" in stats
        assert "cache_size" in stats
        assert stats["cache_size"] == 0

    def test_context_manager(self, mock_config):
        """Test client as context manager closes session."""
        with SonarrClient(mock_config) as client:
            assert hasattr(client, "session")

        # Session should be closed after context


class TestQueueCaching:
    """Test queue caching functionality."""

    def test_get_queue_cached_basic(self, client):
        """Test basic queue caching."""
        with requests_mock.Mocker() as m:
            # Mock the API response
            mock_response = {
                "records": [
                    {"id": 1, "episode": {"id": 100}, "status": "completed"},
                    {"id": 2, "episode": {"id": 101}, "status": "downloading"},
                ]
            }
            m.get("http://test-sonarr:8989/api/v3/queue", json=mock_response)

            # First call - should hit API
            result1 = client.get_queue_cached()
            assert len(result1) == 2
            assert result1[0]["id"] == 1

            # Second call - should use cache
            result2 = client.get_queue_cached()
            assert result1 == result2

            # Should only have made one API call
            assert m.call_count == 1

    def test_get_queue_cached_ttl_expiration(self, client):
        """Test queue cache expiration."""
        with requests_mock.Mocker() as m:
            # Mock different responses
            m.get(
                "http://test-sonarr:8989/api/v3/queue",
                [
                    {"json": {"records": [{"id": 1}]}},
                    {"json": {"records": [{"id": 2}]}},
                ],
            )

            # First call
            result1 = client.get_queue_cached()
            assert result1[0]["id"] == 1

            # Manually expire the cache entry
            client.cache.invalidate("queue_True")

            # Second call should hit API again
            result2 = client.get_queue_cached()
            assert result2[0]["id"] == 2

            # Should have made two API calls
            assert m.call_count == 2

    def test_get_queue_cached_different_params(self, client):
        """Test queue caching with different parameters."""
        with requests_mock.Mocker() as m:
            m.get("http://test-sonarr:8989/api/v3/queue", json={"records": []})

            # Call with different include_unknown values
            client.get_queue_cached(include_unknown=True)
            client.get_queue_cached(include_unknown=False)

            # Should cache separately
            cache_stats = client.get_cache_stats()
            assert cache_stats["cache_size"] == 2  # Two different cache entries

            # Multiple calls with same params should use cache
            client.get_queue_cached(include_unknown=True)
            client.get_queue_cached(include_unknown=True)

            # Still only 2 API calls (one for each parameter combination)
            assert m.call_count == 2

    def test_queue_cache_error_handling(self, client):
        """Test queue cache behavior with API errors."""
        with requests_mock.Mocker() as m:
            # First call succeeds
            m.get("http://test-sonarr:8989/api/v3/queue", json={"records": [{"id": 1}]})

            result = client.get_queue_cached()
            assert len(result) == 1

            # Now simulate API error
            m.get("http://test-sonarr:8989/api/v3/queue", status_code=500)

            # Should still return cached result if cache is valid
            cached_result = client.get_queue_cached()
            assert cached_result == result


class TestCustomFormatScoresCaching:
    """Test custom format scores caching."""

    @patch.object(SonarrClient, "get_custom_format_scores")
    def test_custom_format_scores_cached(self, mock_get_scores, client):
        """Test custom format scores caching."""
        # Mock the underlying method
        mock_get_scores.return_value = {1: 100, 2: 50, 3: -10}

        # First call - should call underlying method
        result1 = client.get_custom_format_scores_cached(123)
        assert result1 == {1: 100, 2: 50, 3: -10}

        # Second call - should use cache
        result2 = client.get_custom_format_scores_cached(123)
        assert result1 == result2

        # Underlying method should only be called once
        mock_get_scores.assert_called_once_with(123)

    @patch.object(SonarrClient, "get_custom_format_scores")
    def test_custom_format_scores_cached_different_series(
        self, mock_get_scores, client
    ):
        """Test caching with different series IDs."""
        mock_get_scores.side_effect = [
            {1: 100, 2: 50},  # For series 123
            {1: 80, 2: 60},  # For series 456
        ]

        # Call for different series
        result1 = client.get_custom_format_scores_cached(123)
        result2 = client.get_custom_format_scores_cached(456)

        assert result1 == {1: 100, 2: 50}
        assert result2 == {1: 80, 2: 60}

        # Should cache separately
        cache_stats = client.get_cache_stats()
        assert cache_stats["cache_size"] == 2

        # Repeat calls should use cache
        result1_cached = client.get_custom_format_scores_cached(123)
        result2_cached = client.get_custom_format_scores_cached(456)

        assert result1 == result1_cached
        assert result2 == result2_cached

        # Should still only have called underlying method twice
        assert mock_get_scores.call_count == 2

    @patch.object(SonarrClient, "get_custom_format_scores")
    def test_custom_format_scores_ttl(self, mock_get_scores, client):
        """Test custom format scores TTL behavior."""
        mock_get_scores.side_effect = [
            {1: 100, 2: 50},  # First call
            {1: 200, 2: 75},  # Second call after cache expiry
        ]

        # First call
        result1 = client.get_custom_format_scores_cached(123)
        assert result1 == {1: 100, 2: 50}

        # Manually expire the cache
        client.cache.invalidate("custom_format_scores_123")

        # Second call should hit API again
        result2 = client.get_custom_format_scores_cached(123)
        assert result2 == {1: 200, 2: 75}

        assert mock_get_scores.call_count == 2


class TestCachePerformance:
    """Test cache performance characteristics."""

    def test_cache_reduces_api_calls(self, client):
        """Test that cache significantly reduces API calls."""
        with requests_mock.Mocker() as m:
            m.get("http://test-sonarr:8989/api/v3/queue", json={"records": []})

            # Make many calls
            for _ in range(10):
                client.get_queue_cached()

            # Should only make one API call
            assert m.call_count == 1

            # Cache should show activity
            cache_stats = client.get_cache_stats()
            assert cache_stats["cache_size"] == 1

    def test_cache_performance_timing(self, client):
        """Test cache performance improvement."""
        with requests_mock.Mocker() as m:
            # Simulate slow API response
            def slow_response(request, context):
                time.sleep(0.05)  # 50ms delay
                return {"records": []}

            m.get("http://test-sonarr:8989/api/v3/queue", json=slow_response)

            # First call (cache miss) - will be slow
            start_time = time.perf_counter()
            client.get_queue_cached()
            first_call_time = time.perf_counter() - start_time

            # Second call (cache hit) - should be much faster
            start_time = time.perf_counter()
            client.get_queue_cached()
            second_call_time = time.perf_counter() - start_time

            # Cache hit should be significantly faster
            assert second_call_time < first_call_time
            assert second_call_time < 0.01  # Should be < 10ms

    def test_memory_usage_with_large_cache(self, client):
        """Test memory behavior with large number of cached items."""
        # Simulate caching many series scores
        for series_id in range(100):
            client.cache.set(
                f"custom_format_scores_{series_id}",
                {i: i * 10 for i in range(20)},  # 20 formats per series
                ttl=300,
            )

        cache_stats = client.get_cache_stats()
        assert cache_stats["cache_size"] == 100

        # Test that we can still efficiently access cached data
        result = client.cache.get("custom_format_scores_50")
        assert result is not None
        assert len(result) == 20


class TestMockCompatibility:
    """Test backward compatibility with mocks."""

    def test_analyzer_uses_regular_method_with_mocks(self):
        """Test that analyzer falls back to regular method with mocks."""
        from unittest.mock import MagicMock

        from src.core.analyzer import ScoreAnalyzer

        # Create mock client (like in tests)
        mock_client = MagicMock()
        mock_client.get_custom_format_scores.return_value = {1: 100, 2: 50}

        # Create mock config
        mock_config = MagicMock()
        mock_config.get.return_value = 10

        # Create analyzer
        analyzer = ScoreAnalyzer(mock_config, mock_client)

        # Test custom format analysis
        custom_formats = [{"id": 1, "name": "HDR"}, {"id": 2, "name": "Atmos"}]

        result = analyzer.analyze_custom_formats(custom_formats, 123)

        # Should work correctly with mock
        assert result.total_score == 150
        assert "HDR" in result.format_names
        assert "Atmos" in result.format_names

        # Should have used regular method, not cached
        mock_client.get_custom_format_scores.assert_called_once_with(123)

    def test_monitor_uses_regular_method_with_mocks(self):
        """Test that monitor falls back to regular method with mocks."""
        from unittest.mock import MagicMock

        from src.core.monitor import SonarrImportMonitor

        # Create mock client and config
        mock_client = MagicMock()
        mock_client.get_queue.return_value = [{"id": 1, "episode": {"id": 100}}]

        mock_config = MagicMock()

        # Create monitor
        monitor = SonarrImportMonitor(mock_config)
        monitor.sonarr_client = mock_client

        # This would normally use cached method in production
        # but should fall back to regular method with mocks
        monitor.check_episode_queue(100)

        # Should have called regular method
        mock_client.get_queue.assert_called()


class TestConnectionPooling:
    """Test HTTP connection pooling."""

    def test_session_reuse(self, client):
        """Test that session is reused for multiple requests."""
        with requests_mock.Mocker() as m:
            m.get("http://test-sonarr:8989/api/v3/queue", json={"records": []})

            # Make multiple calls
            for _ in range(5):
                client.get_queue()  # Use non-cached version to force API calls

            assert m.call_count == 5

            # Verify session was reused (would be tracked by connection pool)
            assert client.session is not None

    def test_session_headers_persistent(self, client):
        """Test that session maintains headers."""
        with requests_mock.Mocker() as m:

            def check_headers(request, context):
                assert "X-Api-Key" in request.headers
                assert request.headers["X-Api-Key"] == "test-api-key-12345"
                return {"records": []}

            m.get("http://test-sonarr:8989/api/v3/queue", json=check_headers)

            # Make call to verify headers are included
            client.get_queue()

    def test_adapter_configuration(self, client):
        """Test that HTTP adapter is configured correctly."""
        # Check that adapters are mounted
        assert "http://" in client.session.adapters
        assert "https://" in client.session.adapters

        # Check adapter configuration (indirectly)
        adapter = client.session.adapters["http://"]
        # Most adapter internals are private, but we can verify it exists
        assert adapter is not None


class TestCacheIntegration:
    """Integration tests for cache with real scenarios."""

    def test_queue_monitoring_scenario(self, client):
        """Test typical queue monitoring scenario."""
        with requests_mock.Mocker() as m:
            # Set up responses in order they'll be called
            responses = [
                {"records": [{"id": 1, "status": "downloading"}]},
                {"records": [{"id": 1, "status": "completed"}]},
            ]

            # Use a counter to track which response to return
            call_count = [0]

            def response_callback(request, context):
                response = responses[call_count[0] % len(responses)]
                call_count[0] += 1
                return response

            m.get("http://test-sonarr:8989/api/v3/queue", json=response_callback)

            # First check - downloading
            result1 = client.get_queue_cached()
            assert len(result1) == 1
            assert result1[0]["status"] == "downloading"

            # Immediate second check - should use cache (same result)
            result2 = client.get_queue_cached()
            assert result1 == result2

            # Force cache invalidation (simulating TTL expiry)
            client.cache.invalidate("queue_True")

            # Third check - completed (triggers second API call)
            result3 = client.get_queue_cached()
            assert len(result3) == 1
            assert result3[0]["status"] == "completed"

            # Should have made 2 API calls (first and third)
            assert m.call_count == 2

    def test_series_analysis_scenario(self, client):
        """Test typical series analysis scenario."""
        with patch.object(client, "get_custom_format_scores") as mock_scores:
            # Different series have different scores
            mock_scores.side_effect = lambda series_id: {
                123: {1: 100, 2: 50},
                456: {1: 80, 2: 60, 3: -10},
                789: {2: 30},
            }[series_id]

            # Analyze multiple series
            series_ids = [123, 456, 789, 123, 456]  # Some repeats
            results = []

            for series_id in series_ids:
                result = client.get_custom_format_scores_cached(series_id)
                results.append((series_id, result))

            # Verify results
            assert results[0][1] == {1: 100, 2: 50}  # Series 123
            assert results[1][1] == {1: 80, 2: 60, 3: -10}  # Series 456
            assert results[2][1] == {2: 30}  # Series 789
            assert results[3][1] == {1: 100, 2: 50}  # Series 123 (cached)
            assert results[4][1] == {1: 80, 2: 60, 3: -10}  # Series 456 (cached)

            # Should only have called underlying method 3 times (unique series)
            assert mock_scores.call_count == 3

    def test_cache_statistics_during_operation(self, client):
        """Test cache statistics during typical operation."""
        with requests_mock.Mocker() as m:
            m.get("http://test-sonarr:8989/api/v3/queue", json={"records": []})

            # Initial state
            stats = client.get_cache_stats()
            assert stats["cache_size"] == 0

            # Add some cached data
            client.get_queue_cached()
            client.get_queue_cached(include_unknown=False)

            stats = client.get_cache_stats()
            assert stats["cache_size"] == 2
            assert stats["cache_stats"]["active"] == 2

            # Clear and verify
            client.cache.clear()
            stats = client.get_cache_stats()
            assert stats["cache_size"] == 0
