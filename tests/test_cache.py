"""
Comprehensive tests for TTL Cache functionality.
"""

import time
import threading
from unittest.mock import patch

import pytest

from src.utils.cache import TTLCache


class TestTTLCacheBasics:
    """Test basic TTL cache operations."""

    def test_cache_initialization_default_ttl(self):
        """Test cache initializes with default TTL."""
        cache = TTLCache()
        assert cache.default_ttl == 300  # 5 minutes
        assert cache.size() == 0
        assert isinstance(cache.cache, dict)

    def test_cache_initialization_custom_ttl(self):
        """Test cache initializes with custom TTL."""
        cache = TTLCache(default_ttl=60)
        assert cache.default_ttl == 60
        assert cache.size() == 0

    def test_set_and_get_basic(self):
        """Test basic set and get operations."""
        cache = TTLCache(default_ttl=60)
        
        # Set a value
        cache.set("key1", "value1")
        
        # Get the value
        result = cache.get("key1")
        assert result == "value1"
        assert cache.size() == 1

    def test_get_nonexistent_key(self):
        """Test getting a key that doesn't exist."""
        cache = TTLCache()
        result = cache.get("nonexistent")
        assert result is None

    def test_set_with_custom_ttl(self):
        """Test setting value with custom TTL."""
        cache = TTLCache(default_ttl=300)
        
        # Set with custom TTL
        cache.set("key1", "value1", ttl=10)
        
        # Verify it's cached
        assert cache.get("key1") == "value1"
        
        # Check the expiration time is set correctly
        entry = cache.cache["key1"]
        expected_expiry = time.time() + 10
        # Allow 1 second tolerance for execution time
        assert abs(entry['expires'] - expected_expiry) < 1

    def test_cache_different_data_types(self):
        """Test caching different data types."""
        cache = TTLCache()
        
        test_data = {
            "string": "hello",
            "integer": 42,
            "float": 3.14,
            "list": [1, 2, 3],
            "dict": {"nested": "value"},
            "boolean": True,
            "none": None
        }
        
        for key, value in test_data.items():
            cache.set(key, value)
            assert cache.get(key) == value


class TestTTLExpiration:
    """Test TTL expiration behavior."""

    def test_immediate_expiration(self):
        """Test cache with 0 TTL expires immediately."""
        cache = TTLCache()
        
        cache.set("key1", "value1", ttl=0)
        
        # Small delay to ensure expiration
        time.sleep(0.01)
        
        # Should be expired immediately
        result = cache.get("key1")
        assert result is None
        assert cache.size() == 0

    def test_expiration_after_delay(self):
        """Test cache expires after TTL delay."""
        cache = TTLCache()
        
        # Set with very short TTL
        cache.set("key1", "value1", ttl=1)
        
        # Should be available immediately
        assert cache.get("key1") == "value1"
        
        # Wait for expiration
        time.sleep(1.1)
        
        # Should be expired now
        assert cache.get("key1") is None
        assert cache.size() == 0

    def test_partial_expiration(self):
        """Test that only expired items are removed."""
        cache = TTLCache()
        
        # Set items with different TTL
        cache.set("short", "value1", ttl=1)
        cache.set("long", "value2", ttl=60)
        
        # Both should be available
        assert cache.get("short") == "value1"
        assert cache.get("long") == "value2"
        assert cache.size() == 2
        
        # Wait for short TTL to expire
        time.sleep(1.1)
        
        # Short should be gone, long should remain
        assert cache.get("short") is None
        assert cache.get("long") == "value2"
        assert cache.size() == 1

    def test_expiration_edge_cases(self):
        """Test expiration edge cases."""
        cache = TTLCache()
        
        # Set with very small positive TTL
        cache.set("edge1", "value1", ttl=0.1)
        time.sleep(0.05)
        assert cache.get("edge1") == "value1"  # Should still be valid
        
        time.sleep(0.1)
        assert cache.get("edge1") is None  # Should be expired


class TestCacheManagement:
    """Test cache management operations."""

    def test_invalidate_specific_key(self):
        """Test invalidating specific keys."""
        cache = TTLCache()
        
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        assert cache.size() == 2
        
        # Invalidate one key
        cache.invalidate("key1")
        
        assert cache.get("key1") is None
        assert cache.get("key2") == "value2"
        assert cache.size() == 1

    def test_invalidate_nonexistent_key(self):
        """Test invalidating a key that doesn't exist."""
        cache = TTLCache()
        cache.set("key1", "value1")
        
        # Should not raise exception
        cache.invalidate("nonexistent")
        
        assert cache.get("key1") == "value1"
        assert cache.size() == 1

    def test_clear_all_entries(self):
        """Test clearing all cache entries."""
        cache = TTLCache()
        
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.set("key3", "value3")
        assert cache.size() == 3
        
        cache.clear()
        
        assert cache.size() == 0
        assert cache.get("key1") is None
        assert cache.get("key2") is None
        assert cache.get("key3") is None

    def test_cleanup_expired_entries(self):
        """Test explicit cleanup of expired entries."""
        cache = TTLCache()
        
        # Set items with different TTL
        cache.set("expired1", "value1", ttl=1)
        cache.set("expired2", "value2", ttl=1)
        cache.set("valid", "value3", ttl=60)
        
        # Wait for some to expire
        time.sleep(1.1)
        
        # Manual cleanup
        cleaned_count = cache.cleanup_expired()
        
        assert cleaned_count == 2
        assert cache.size() == 1
        assert cache.get("valid") == "value3"

    def test_cleanup_no_expired(self):
        """Test cleanup when no entries are expired."""
        cache = TTLCache()
        
        cache.set("key1", "value1", ttl=60)
        cache.set("key2", "value2", ttl=60)
        
        cleaned_count = cache.cleanup_expired()
        
        assert cleaned_count == 0
        assert cache.size() == 2


class TestCacheStatistics:
    """Test cache statistics and monitoring."""

    def test_size_tracking(self):
        """Test cache size tracking."""
        cache = TTLCache()
        
        assert cache.size() == 0
        
        cache.set("key1", "value1")
        assert cache.size() == 1
        
        cache.set("key2", "value2")
        assert cache.size() == 2
        
        cache.invalidate("key1")
        assert cache.size() == 1
        
        cache.clear()
        assert cache.size() == 0

    def test_stats_active_entries(self):
        """Test statistics for active entries."""
        cache = TTLCache()
        
        cache.set("key1", "value1", ttl=60)
        cache.set("key2", "value2", ttl=60)
        
        stats = cache.stats()
        
        assert stats['size'] == 2
        assert stats['active'] == 2
        assert stats['expired'] == 0

    def test_stats_with_expired_entries(self):
        """Test statistics with expired entries."""
        cache = TTLCache()
        
        cache.set("expired", "value1", ttl=1)
        cache.set("valid", "value2", ttl=60)
        
        # Wait for expiration
        time.sleep(1.1)
        
        stats = cache.stats()
        
        # Note: expired entries are still in cache until accessed or cleaned
        assert stats['size'] == 2
        assert stats['active'] == 1
        assert stats['expired'] == 1

    def test_stats_after_cleanup(self):
        """Test statistics after cleanup."""
        cache = TTLCache()
        
        cache.set("expired", "value1", ttl=1)
        cache.set("valid", "value2", ttl=60)
        
        time.sleep(1.1)
        cache.cleanup_expired()
        
        stats = cache.stats()
        
        assert stats['size'] == 1
        assert stats['active'] == 1
        assert stats['expired'] == 0


class TestCachePerformance:
    """Test cache performance characteristics."""

    def test_large_dataset(self):
        """Test cache with large number of entries."""
        cache = TTLCache()
        
        # Add many entries
        for i in range(1000):
            cache.set(f"key_{i}", f"value_{i}")
        
        assert cache.size() == 1000
        
        # Test random access
        assert cache.get("key_500") == "value_500"
        assert cache.get("key_999") == "value_999"
        
        # Test statistics
        stats = cache.stats()
        assert stats['size'] == 1000
        assert stats['active'] == 1000

    def test_overwrite_existing_key(self):
        """Test overwriting existing cache keys."""
        cache = TTLCache()
        
        cache.set("key1", "original_value")
        assert cache.get("key1") == "original_value"
        assert cache.size() == 1
        
        # Overwrite
        cache.set("key1", "new_value")
        assert cache.get("key1") == "new_value"
        assert cache.size() == 1  # Size should not increase

    def test_memory_efficiency(self):
        """Test that expired entries are removed from memory."""
        cache = TTLCache()
        
        # Fill cache with short TTL
        for i in range(100):
            cache.set(f"key_{i}", f"large_value_{'x' * 100}_{i}", ttl=1)
        
        assert cache.size() == 100
        
        # Wait for expiration
        time.sleep(1.1)
        
        # Access one key to trigger cleanup
        cache.get("key_1")
        
        # Verify expired entries are cleaned up on access
        remaining_size = cache.size()
        assert remaining_size < 100  # Should have cleaned up accessed expired entry


class TestConcurrency:
    """Test cache behavior under concurrent access."""

    def test_concurrent_reads(self):
        """Test concurrent read operations."""
        cache = TTLCache()
        cache.set("shared_key", "shared_value")
        
        results = []
        
        def read_worker():
            for _ in range(10):
                value = cache.get("shared_key")
                results.append(value)
        
        # Start multiple threads
        threads = [threading.Thread(target=read_worker) for _ in range(5)]
        
        for thread in threads:
            thread.start()
        
        for thread in threads:
            thread.join()
        
        # All reads should succeed
        assert len(results) == 50
        assert all(result == "shared_value" for result in results)

    def test_concurrent_writes(self):
        """Test concurrent write operations."""
        cache = TTLCache()
        
        def write_worker(thread_id):
            for i in range(10):
                cache.set(f"key_{thread_id}_{i}", f"value_{thread_id}_{i}")
        
        # Start multiple threads
        threads = [threading.Thread(target=write_worker, args=(i,)) for i in range(5)]
        
        for thread in threads:
            thread.start()
        
        for thread in threads:
            thread.join()
        
        # Check final state
        assert cache.size() == 50
        
        # Verify all values are present
        for thread_id in range(5):
            for i in range(10):
                key = f"key_{thread_id}_{i}"
                expected_value = f"value_{thread_id}_{i}"
                assert cache.get(key) == expected_value


class TestEdgeCases:
    """Test edge cases and error conditions."""

    def test_negative_ttl(self):
        """Test behavior with negative TTL."""
        cache = TTLCache()
        
        # Negative TTL should be treated as expired
        cache.set("key1", "value1", ttl=-1)
        
        result = cache.get("key1")
        assert result is None
        assert cache.size() == 0

    def test_very_large_ttl(self):
        """Test with very large TTL values."""
        cache = TTLCache()
        
        # Set with very large TTL (100 years)
        large_ttl = 60 * 60 * 24 * 365 * 100
        cache.set("key1", "value1", ttl=large_ttl)
        
        assert cache.get("key1") == "value1"
        
        # Check expiration is far in the future
        entry = cache.cache["key1"]
        assert entry['expires'] > time.time() + (60 * 60 * 24 * 365)  # At least 1 year

    def test_empty_string_keys_and_values(self):
        """Test with empty string keys and values."""
        cache = TTLCache()
        
        # Empty string key
        cache.set("", "value_for_empty_key")
        assert cache.get("") == "value_for_empty_key"
        
        # Empty string value
        cache.set("key_for_empty_value", "")
        assert cache.get("key_for_empty_value") == ""

    def test_none_values(self):
        """Test caching None values."""
        cache = TTLCache()
        
        cache.set("none_key", None)
        
        # None should be cached (different from missing key)
        result = cache.get("none_key")
        assert result is None
        assert "none_key" in cache.cache  # Key should exist in cache
        
        # Missing key should also return None but not be in cache
        missing_result = cache.get("missing_key")
        assert missing_result is None
        assert "missing_key" not in cache.cache

    @patch('time.time')
    def test_time_manipulation(self, mock_time):
        """Test cache behavior with manipulated time."""
        cache = TTLCache()
        
        # Start at time 1000
        mock_time.return_value = 1000
        cache.set("key1", "value1", ttl=60)
        
        # Should be available at same time
        assert cache.get("key1") == "value1"
        
        # Move time forward by 30 seconds
        mock_time.return_value = 1030
        assert cache.get("key1") == "value1"  # Still valid
        
        # Move time forward by 61 seconds total
        mock_time.return_value = 1061
        assert cache.get("key1") is None  # Should be expired


class TestCacheIntegration:
    """Integration tests for cache behavior."""

    def test_cache_hit_miss_pattern(self):
        """Test typical cache hit/miss patterns."""
        cache = TTLCache()
        
        # Miss - set value
        result = cache.get("user_123")
        assert result is None
        
        cache.set("user_123", {"name": "John", "age": 30})
        
        # Hit - get cached value
        result = cache.get("user_123")
        assert result == {"name": "John", "age": 30}
        
        # Multiple hits
        for _ in range(10):
            result = cache.get("user_123")
            assert result == {"name": "John", "age": 30}

    def test_cache_update_pattern(self):
        """Test updating cached values."""
        cache = TTLCache()
        
        # Initial value
        cache.set("config", {"version": 1, "enabled": True})
        assert cache.get("config")["version"] == 1
        
        # Update value
        cache.set("config", {"version": 2, "enabled": False})
        assert cache.get("config")["version"] == 2
        assert cache.get("config")["enabled"] is False
        
        # Cache size should remain 1
        assert cache.size() == 1