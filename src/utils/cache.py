"""
Simple TTL (Time-To-Live) cache implementation for Sonarr API responses.
"""

import time
from typing import Any, Dict, Optional


class TTLCache:
    """
    Simple time-based cache with TTL expiration.
    
    This cache stores API responses in memory with configurable expiration times
    to reduce the number of API calls to Sonarr.
    """

    def __init__(self, default_ttl: int = 300):
        """
        Initialize TTL cache.
        
        Args:
            default_ttl: Default time-to-live in seconds (5 minutes default)
        """
        self.default_ttl = default_ttl
        self.cache: Dict[str, Dict[str, Any]] = {}

    def get(self, key: str) -> Optional[Any]:
        """
        Retrieve value from cache if not expired.
        
        Args:
            key: Cache key
            
        Returns:
            Cached value if found and not expired, None otherwise
        """
        if key not in self.cache:
            return None
            
        entry = self.cache[key]
        current_time = time.time()
        
        # Check if expired
        if current_time > entry['expires']:
            del self.cache[key]
            return None
            
        return entry['value']

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """
        Store value in cache with expiration.
        
        Args:
            key: Cache key
            value: Value to cache
            ttl: Time-to-live in seconds, uses default if None
        """
        ttl = ttl if ttl is not None else self.default_ttl
        expires = time.time() + ttl
        
        self.cache[key] = {
            'value': value,
            'expires': expires
        }

    def invalidate(self, key: str) -> None:
        """Remove specific key from cache."""
        if key in self.cache:
            del self.cache[key]

    def clear(self) -> None:
        """Clear all cache entries."""
        self.cache.clear()

    def cleanup_expired(self) -> int:
        """
        Remove expired entries from cache.
        
        Returns:
            Number of expired entries removed
        """
        current_time = time.time()
        expired_keys = [
            key for key, entry in self.cache.items()
            if current_time > entry['expires']
        ]
        
        for key in expired_keys:
            del self.cache[key]
            
        return len(expired_keys)

    def size(self) -> int:
        """Get current cache size."""
        return len(self.cache)

    def stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.
        
        Returns:
            Dictionary with cache stats
        """
        current_time = time.time()
        expired_count = sum(
            1 for entry in self.cache.values()
            if current_time > entry['expires']
        )
        
        return {
            'size': len(self.cache),
            'expired': expired_count,
            'active': len(self.cache) - expired_count
        }