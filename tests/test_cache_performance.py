"""
Performance benchmarks and load tests for cache functionality.
"""

import concurrent.futures
import statistics
import time
from unittest.mock import MagicMock, patch

import pytest
import requests_mock

from src.api.sonarr_client import SonarrClient
from src.utils.cache import TTLCache


class TestCachePerformanceBenchmarks:
    """Performance benchmarks for cache operations."""

    def test_cache_operation_speed(self):
        """Benchmark basic cache operations."""
        cache = TTLCache()

        # Measure set operations
        set_times = []
        for i in range(1000):
            start = time.perf_counter()
            cache.set(f"key_{i}", f"value_{i}")
            end = time.perf_counter()
            set_times.append(end - start)

        # Measure get operations
        get_times = []
        for i in range(1000):
            start = time.perf_counter()
            result = cache.get(f"key_{i}")
            end = time.perf_counter()
            get_times.append(end - start)
            assert result == f"value_{i}"

        # Analyze performance
        avg_set_time = statistics.mean(set_times)
        avg_get_time = statistics.mean(get_times)

        print(f"\nCache Performance Metrics:")
        print(f"Average SET time: {avg_set_time*1000:.3f}ms")
        print(f"Average GET time: {avg_get_time*1000:.3f}ms")
        print(f"Max SET time: {max(set_times)*1000:.3f}ms")
        print(f"Max GET time: {max(get_times)*1000:.3f}ms")

        # Performance assertions (should be very fast)
        assert avg_set_time < 0.001  # Less than 1ms
        assert avg_get_time < 0.001  # Less than 1ms

    def test_large_dataset_performance(self):
        """Test performance with large datasets."""
        cache = TTLCache()

        # Large dataset
        dataset_size = 10000
        large_value = "x" * 1000  # 1KB value

        # Measure bulk insertion
        start_time = time.perf_counter()
        for i in range(dataset_size):
            cache.set(f"large_key_{i}", f"{large_value}_{i}")
        insertion_time = time.perf_counter() - start_time

        print(f"\nLarge Dataset Performance:")
        print(f"Inserted {dataset_size} items in {insertion_time:.3f}s")
        print(f"Rate: {dataset_size/insertion_time:.0f} items/second")

        # Measure random access
        import random

        access_keys = [
            f"large_key_{random.randint(0, dataset_size-1)}" for _ in range(1000)
        ]

        start_time = time.perf_counter()
        for key in access_keys:
            result = cache.get(key)
            assert result is not None
        access_time = time.perf_counter() - start_time

        print(f"1000 random accesses in {access_time:.3f}s")
        print(f"Access rate: {1000/access_time:.0f} items/second")

        # Performance assertions
        assert insertion_time < 5.0  # Should insert 10K items in under 5 seconds
        assert access_time < 1.0  # Should access 1K items in under 1 second

    def test_memory_efficiency(self):
        """Test memory usage patterns."""
        try:
            import os

            import psutil
        except ImportError:
            pytest.skip("psutil not available for memory testing")

        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss

        cache = TTLCache()

        # Add many small items
        for i in range(10000):
            cache.set(f"mem_key_{i}", {"data": f"value_{i}", "index": i})

        memory_after_insert = process.memory_info().rss
        memory_increase = memory_after_insert - initial_memory

        print(f"\nMemory Usage:")
        print(f"Initial memory: {initial_memory / 1024 / 1024:.1f} MB")
        print(f"After 10K items: {memory_after_insert / 1024 / 1024:.1f} MB")
        print(f"Memory increase: {memory_increase / 1024 / 1024:.1f} MB")
        print(f"Per item: {memory_increase / 10000:.0f} bytes")

        # Clear cache and measure cleanup
        cache.clear()

        # Force garbage collection
        import gc

        gc.collect()

        memory_after_clear = process.memory_info().rss
        print(f"After clear: {memory_after_clear / 1024 / 1024:.1f} MB")

        # Memory should be reasonable (allow for Python overhead)
        assert memory_increase < 50 * 1024 * 1024  # Less than 50MB for 10K items

    def test_concurrent_performance(self):
        """Test cache performance under concurrent load."""
        cache = TTLCache()

        def worker_write(thread_id, iterations):
            times = []
            for i in range(iterations):
                start = time.perf_counter()
                cache.set(f"thread_{thread_id}_key_{i}", f"value_{thread_id}_{i}")
                times.append(time.perf_counter() - start)
            return times

        def worker_read(thread_id, iterations):
            times = []
            for i in range(iterations):
                start = time.perf_counter()
                cache.get(f"thread_{thread_id}_key_{i}")
                times.append(time.perf_counter() - start)
            return times

        # Pre-populate cache
        for thread_id in range(5):
            for i in range(100):
                cache.set(f"thread_{thread_id}_key_{i}", f"value_{thread_id}_{i}")

        # Concurrent reads
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            read_futures = [
                executor.submit(worker_read, thread_id, 100) for thread_id in range(5)
            ]
            write_futures = [
                executor.submit(worker_write, thread_id + 5, 100)
                for thread_id in range(5)
            ]

            # Collect results
            read_times = []
            for future in concurrent.futures.as_completed(read_futures):
                read_times.extend(future.result())

            write_times = []
            for future in concurrent.futures.as_completed(write_futures):
                write_times.extend(future.result())

        print(f"\nConcurrent Performance:")
        print(f"Concurrent read avg: {statistics.mean(read_times)*1000:.3f}ms")
        print(f"Concurrent write avg: {statistics.mean(write_times)*1000:.3f}ms")

        # Should maintain good performance under concurrency
        assert statistics.mean(read_times) < 0.005  # Less than 5ms
        assert statistics.mean(write_times) < 0.005  # Less than 5ms

    def test_expiration_cleanup_performance(self):
        """Test performance of expiration and cleanup operations."""
        cache = TTLCache()

        # Add items with different TTLs
        short_ttl_count = 1000
        long_ttl_count = 1000

        for i in range(short_ttl_count):
            cache.set(f"short_{i}", f"value_{i}", ttl=1)  # 1 second

        for i in range(long_ttl_count):
            cache.set(f"long_{i}", f"value_{i}", ttl=300)  # 5 minutes

        assert cache.size() == short_ttl_count + long_ttl_count

        # Wait for short TTL items to expire
        time.sleep(1.1)

        # Measure cleanup performance
        start_time = time.perf_counter()
        cleaned_count = cache.cleanup_expired()
        cleanup_time = time.perf_counter() - start_time

        print(f"\nCleanup Performance:")
        print(f"Cleaned {cleaned_count} items in {cleanup_time:.3f}s")
        print(f"Cleanup rate: {cleaned_count/cleanup_time:.0f} items/second")

        assert cleaned_count == short_ttl_count
        assert cache.size() == long_ttl_count
        assert cleanup_time < 1.0  # Should clean 1000 items in under 1 second


class TestSonarrClientPerformance:
    """Performance tests for SonarrClient caching."""

    @pytest.fixture
    def client(self):
        """Provide SonarrClient for performance testing."""
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "sonarr.url": "http://test-sonarr:8989",
            "sonarr.api_key": "test-api-key",
            "sonarr.timeout": 30,
        }.get(key, default)
        return SonarrClient(config)

    def test_api_call_reduction(self, client):
        """Test actual API call reduction through caching."""
        with requests_mock.Mocker() as m:
            # Mock slow API response
            def slow_api_response(request, context):
                time.sleep(0.1)  # 100ms delay per call
                return {"records": [{"id": 1, "status": "completed"}]}

            m.get("http://test-sonarr:8989/api/v3/queue", json=slow_api_response)

            # Measure without cache (using regular method)
            non_cached_times = []
            for _ in range(5):
                start = time.perf_counter()
                result = client.get_queue()
                non_cached_times.append(time.perf_counter() - start)

            non_cached_avg = statistics.mean(non_cached_times)
            total_non_cached_time = sum(non_cached_times)

            # Reset call count
            m.reset_mock()

            # Measure with cache
            cached_times = []
            for _ in range(5):
                start = time.perf_counter()
                result = client.get_queue_cached()
                cached_times.append(time.perf_counter() - start)

            cached_avg = statistics.mean(cached_times)
            total_cached_time = sum(cached_times)

            print(f"\nAPI Call Reduction Performance:")
            print(f"Non-cached calls: {len(non_cached_times)}")
            print(f"API calls made (non-cached): {len(non_cached_times)}")
            print(f"API calls made (cached): {m.call_count}")  # Should be 1
            print(f"Non-cached avg time: {non_cached_avg:.3f}s")
            print(f"Cached avg time: {cached_avg:.3f}s")
            print(f"Total time saved: {total_non_cached_time - total_cached_time:.3f}s")
            print(f"Speed improvement: {non_cached_avg / cached_avg:.1f}x")

            # Verify performance improvements
            assert m.call_count == 1  # Only first call should hit API
            assert cached_avg < non_cached_avg / 2  # At least 2x faster
            assert (
                total_cached_time < total_non_cached_time / 2
            )  # Significant time savings

    def test_custom_format_scores_caching_performance(self, client):
        """Test performance of custom format scores caching."""
        with patch.object(client, "get_custom_format_scores") as mock_scores:
            # Simulate expensive computation
            def expensive_computation(series_id):
                time.sleep(0.05)  # 50ms per call
                return {i: i * 10 for i in range(20)}  # 20 formats

            mock_scores.side_effect = expensive_computation

            series_ids = [123, 456, 789, 123, 456, 789, 123]  # Some repeats

            # Time the operations
            start_time = time.perf_counter()
            results = []
            for series_id in series_ids:
                result = client.get_custom_format_scores_cached(series_id)
                results.append(result)
            total_time = time.perf_counter() - start_time

            print(f"\nCustom Format Scores Caching:")
            print(f"Total calls: {len(series_ids)}")
            print(f"Unique series: {len(set(series_ids))}")
            print(f"API calls made: {mock_scores.call_count}")
            print(f"Total time: {total_time:.3f}s")
            print(f"Time per call: {total_time / len(series_ids):.3f}s")
            print(
                f"Cache hit ratio: {1 - (mock_scores.call_count / len(series_ids)):.2%}"
            )

            # Verify caching efficiency
            assert mock_scores.call_count == 3  # Only unique series
            assert len(results) == 7  # All calls returned results
            assert total_time < 0.3  # Should be much faster than 7 * 0.05 = 0.35s

    def test_concurrent_cache_access(self, client):
        """Test cache performance under concurrent access."""
        with requests_mock.Mocker() as m:
            m.get("http://test-sonarr:8989/api/v3/queue", json={"records": []})

            def concurrent_worker():
                times = []
                for _ in range(10):
                    start = time.perf_counter()
                    client.get_queue_cached()
                    times.append(time.perf_counter() - start)
                return times

            # Run concurrent workers
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                futures = [executor.submit(concurrent_worker) for _ in range(5)]

                all_times = []
                for future in concurrent.futures.as_completed(futures):
                    all_times.extend(future.result())

            avg_time = statistics.mean(all_times)

            print(f"\nConcurrent Cache Access:")
            print(f"Total operations: {len(all_times)}")
            print(f"API calls made: {m.call_count}")  # Should be 1
            print(f"Average time per operation: {avg_time*1000:.3f}ms")
            print(f"Max time: {max(all_times)*1000:.3f}ms")

            # Verify concurrent performance (allow race conditions in threading)
            assert m.call_count <= 5  # Allow up to 5 calls in concurrent scenario
            assert avg_time < 0.01  # Very fast cache hits

    def test_cache_memory_scaling(self, client):
        """Test cache memory usage as it scales."""
        try:
            import os

            import psutil
        except ImportError:
            pytest.skip("psutil not available for memory testing")

        process = psutil.Process(os.getpid())
        initial_memory = process.memory_info().rss

        # Simulate caching many series
        for series_id in range(1000):
            # Large format data per series
            format_data = {i: i * 10 for i in range(50)}  # 50 formats per series
            client.cache.set(f"custom_format_scores_{series_id}", format_data, ttl=300)

        after_cache_memory = process.memory_info().rss
        memory_increase = after_cache_memory - initial_memory

        print(f"\nCache Memory Scaling:")
        print(f"Cached 1000 series with 50 formats each")
        print(f"Memory increase: {memory_increase / 1024 / 1024:.1f} MB")
        print(f"Per series: {memory_increase / 1000:.0f} bytes")
        print(f"Cache size: {client.cache.size()}")

        # Test cache efficiency
        stats = client.get_cache_stats()
        print(f"Active entries: {stats['cache_stats']['active']}")

        # Memory usage should be reasonable
        assert memory_increase < 20 * 1024 * 1024  # Less than 20MB for 1000 series


class TestRealWorldScenarios:
    """Test cache performance in real-world usage scenarios."""

    @pytest.fixture
    def client(self):
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "sonarr.url": "http://test-sonarr:8989",
            "sonarr.api_key": "test-api-key",
            "sonarr.timeout": 30,
        }.get(key, default)
        return SonarrClient(config)

    def test_monitoring_loop_scenario(self, client):
        """Test performance during typical monitoring loop."""
        with requests_mock.Mocker() as m:
            # Simulate changing queue over time
            queue_responses = [
                {"records": [{"id": i} for i in range(5)]},  # 5 items
                {"records": [{"id": i} for i in range(3)]},  # 3 items
                {"records": [{"id": i} for i in range(1)]},  # 1 item
                {"records": []},  # Empty queue
            ]

            # Each call takes 100ms to simulate network latency
            def slow_response(request, context):
                time.sleep(0.1)
                return queue_responses.pop(0) if queue_responses else {"records": []}

            m.get("http://test-sonarr:8989/api/v3/queue", json=slow_response)

            # Simulate monitoring loop with cache
            total_calls = 0
            start_time = time.perf_counter()

            for cycle in range(4):  # 4 monitoring cycles
                # Each cycle makes multiple queue checks (realistic scenario)
                for check in range(3):  # 3 checks per cycle
                    result = client.get_queue_cached()
                    total_calls += 1

                # Simulate cache expiry between cycles
                if cycle < 3:
                    client.cache.invalidate("queue_True")
                    time.sleep(0.01)  # Small delay between cycles

            total_time = time.perf_counter() - start_time

            print(f"\nMonitoring Loop Scenario:")
            print(f"Total calls: {total_calls}")
            print(f"API calls made: {m.call_count}")
            print(f"Total time: {total_time:.3f}s")
            print(f"Time without cache would be: {total_calls * 0.1:.3f}s")
            print(f"Time saved: {(total_calls * 0.1) - total_time:.3f}s")
            print(f"Performance improvement: {(total_calls * 0.1) / total_time:.1f}x")

            # Should show significant improvement
            assert m.call_count == 4  # Only one API call per cycle
            assert total_time < total_calls * 0.1 / 2  # At least 2x faster

    def test_batch_series_analysis(self, client):
        """Test performance when analyzing many series at once."""
        with patch.object(client, "get_custom_format_scores") as mock_scores:
            # Simulate database lookup time
            def simulate_db_lookup(series_id):
                time.sleep(0.02)  # 20ms per lookup
                return {1: 100, 2: 50, 3: -10}

            mock_scores.side_effect = simulate_db_lookup

            # Simulate analyzing 100 series (some duplicates)
            series_list = list(range(50)) * 3  # 150 total, 50 unique

            start_time = time.perf_counter()
            results = []
            for series_id in series_list:
                result = client.get_custom_format_scores_cached(series_id)
                results.append(result)
            total_time = time.perf_counter() - start_time

            expected_time_without_cache = len(series_list) * 0.02

            print(f"\nBatch Series Analysis:")
            print(f"Total series processed: {len(series_list)}")
            print(f"Unique series: {len(set(series_list))}")
            print(f"API calls made: {mock_scores.call_count}")
            print(f"Total time: {total_time:.3f}s")
            print(f"Time without cache: {expected_time_without_cache:.3f}s")
            print(f"Time saved: {expected_time_without_cache - total_time:.3f}s")
            print(
                f"Performance improvement: {expected_time_without_cache / total_time:.1f}x"
            )

            # Verify significant performance gain
            assert mock_scores.call_count == 50  # Only unique series
            assert len(results) == 150  # All requests served
            assert total_time < expected_time_without_cache / 2  # At least 2x faster


if __name__ == "__main__":
    # Run performance tests standalone
    print("Running Cache Performance Benchmarks...")

    # Basic performance test
    test = TestCachePerformanceBenchmarks()
    test.test_cache_operation_speed()
    test.test_large_dataset_performance()
    test.test_concurrent_performance()

    print("\nAll performance benchmarks completed successfully!")
