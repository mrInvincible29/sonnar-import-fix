"""
Sonarr API client with retry logic and proper error handling.
"""

import logging
from typing import Dict, List, Optional
from urllib.parse import urljoin

import requests

from ..config.loader import ConfigLoader
from ..utils.cache import TTLCache
from ..utils.decorators import log_execution_time, retry

logger = logging.getLogger(__name__)


class SonarrAPIError(Exception):
    """Raised when Sonarr API returns an error."""

    pass


class SonarrClient:
    """
    Sonarr API client with automatic retry and proper error handling.
    """

    def __init__(self, config: ConfigLoader):
        """
        Initialize Sonarr API client.

        Args:
            config: Configuration loader instance
        """
        self.base_url = config.get("sonarr.url").rstrip("/")
        self.api_key = config.get("sonarr.api_key")
        self.timeout = config.get("sonarr.timeout", 30)

        self.headers = {"X-Api-Key": self.api_key, "Content-Type": "application/json"}
        
        # Create persistent session for connection pooling
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        
        # Configure session for better performance
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=10,
            pool_maxsize=20,
            max_retries=0  # We handle retries manually
        )
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

        # TTL cache for API responses
        self.cache = TTLCache(default_ttl=300)  # 5 minutes default
        
        # Cache for configuration data that rarely changes
        self._custom_formats_cache: Optional[Dict[int, Dict]] = None
        self._quality_profiles_cache: Optional[Dict[int, Dict]] = None
        self._series_profile_map_cache: Optional[Dict[int, int]] = None

        logger.info(f"Initialized Sonarr client for: {self.base_url}")
        
    def close(self):
        """Close the session and cleanup resources."""
        if hasattr(self, 'session'):
            self.session.close()
    
    def __enter__(self):
        """Context manager entry."""
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit with cleanup."""
        self.close()
        
    def get_cache_stats(self) -> Dict:
        """Get cache statistics for monitoring."""
        return {
            'cache_stats': self.cache.stats(),
            'cache_size': self.cache.size()
        }

    def _make_request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        """
        Make HTTP request to Sonarr API.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            endpoint: API endpoint (without /api/v3 prefix)
            **kwargs: Additional arguments for requests
        """
        if not endpoint.startswith("/"):
            endpoint = "/" + endpoint

        if not endpoint.startswith("/api/v3"):
            endpoint = "/api/v3" + endpoint

        url = urljoin(self.base_url + "/", endpoint.lstrip("/"))

        # Set default timeout and headers
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("headers", {}).update(self.headers)

        try:
            response = self.session.request(method, url, **kwargs)

            # Log request for debugging
            logger.debug(f"{method} {url} -> {response.status_code}")

            if response.status_code >= 400:
                error_msg = f"Sonarr API error: {response.status_code}"
                try:
                    error_data = response.json()
                    if "message" in error_data:
                        error_msg += f" - {error_data['message']}"
                except Exception:
                    pass

                raise SonarrAPIError(error_msg)

            return response

        except requests.RequestException as e:
            raise SonarrAPIError(f"Request failed: {e}")

    @retry(
        max_attempts=3,
        delay=1.0,
        exceptions=(SonarrAPIError, requests.RequestException),
    )
    @log_execution_time()
    def test_connection(self) -> bool:
        """
        Test connection to Sonarr API.

        Returns:
            True if connection successful
        """
        try:
            response = self._make_request("GET", "/system/status")
            data = response.json()

            logger.info(f"Connected to Sonarr {data.get('version', 'unknown version')}")
            return True

        except Exception as e:
            logger.error(f"Failed to connect to Sonarr: {e}")
            return False

    @retry(max_attempts=2, delay=2.0, exceptions=SonarrAPIError)
    def fetch_custom_formats(self) -> Dict[int, Dict]:
        """
        Fetch all custom formats from Sonarr.

        Returns:
            Dictionary mapping format ID to format data
        """
        if self._custom_formats_cache is not None:
            return self._custom_formats_cache

        try:
            response = self._make_request("GET", "/customformat")
            formats = response.json()

            result = {cf["id"]: cf for cf in formats}
            self._custom_formats_cache = result

            logger.info(f"Loaded {len(result)} custom formats")
            return result

        except Exception as e:
            logger.error(f"Failed to fetch custom formats: {e}")
            return {}

    @retry(max_attempts=2, delay=2.0, exceptions=SonarrAPIError)
    def fetch_quality_profiles(self) -> Dict[int, Dict]:
        """
        Fetch all quality profiles from Sonarr.

        Returns:
            Dictionary mapping profile ID to profile data
        """
        if self._quality_profiles_cache is not None:
            return self._quality_profiles_cache

        try:
            response = self._make_request("GET", "/qualityprofile")
            profiles = response.json()

            result = {profile["id"]: profile for profile in profiles}
            self._quality_profiles_cache = result

            logger.info(f"Loaded {len(result)} quality profiles")
            return result

        except Exception as e:
            logger.error(f"Failed to fetch quality profiles: {e}")
            return {}

    @retry(max_attempts=2, delay=2.0, exceptions=SonarrAPIError)
    def build_series_profile_map(self) -> Dict[int, int]:
        """
        Build a map of series ID to quality profile ID.

        Returns:
            Dictionary mapping series ID to quality profile ID
        """
        if self._series_profile_map_cache is not None:
            return self._series_profile_map_cache

        try:
            response = self._make_request("GET", "/series")
            series_list = response.json()

            result = {}
            for series in series_list:
                series_id = series["id"]
                profile_id = series.get("qualityProfileId")
                if profile_id:
                    result[series_id] = profile_id

            self._series_profile_map_cache = result

            logger.info(f"Built series profile map for {len(result)} series")
            return result

        except Exception as e:
            logger.error(f"Failed to build series profile map: {e}")
            return {}

    def get_custom_format_scores(self, series_id: int) -> Dict[int, int]:
        """
        Get custom format scores for a series based on its quality profile.

        Args:
            series_id: Sonarr series ID

        Returns:
            Dictionary mapping custom format ID to score
        """
        series_profile_map = self.build_series_profile_map()
        quality_profiles = self.fetch_quality_profiles()

        profile_id = series_profile_map.get(series_id)
        if not profile_id or profile_id not in quality_profiles:
            return {}

        profile = quality_profiles[profile_id]
        scores = {}

        for format_item in profile.get("formatItems", []):
            format_id = format_item.get("format")
            score = format_item.get("score", 0)
            if format_id:
                scores[format_id] = score

        return scores
    
    def get_custom_format_scores_cached(self, series_id: int) -> Dict[int, int]:
        """
        Get custom format scores for a series with caching (5 minute TTL).
        
        Args:
            series_id: Sonarr series ID
            
        Returns:
            Dictionary mapping custom format ID to score
        """
        cache_key = f"custom_format_scores_{series_id}"
        cached_result = self.cache.get(cache_key)
        
        if cached_result is not None:
            logger.debug(f"Using cached custom format scores for series {series_id}")
            return cached_result
            
        result = self.get_custom_format_scores(series_id)
        self.cache.set(cache_key, result, ttl=300)  # 5 minute cache
        return result

    @retry(max_attempts=2, delay=1.0, exceptions=SonarrAPIError)
    def get_queue(self, include_unknown: bool = True) -> List[Dict]:
        """
        Get current download queue.

        Args:
            include_unknown: Include unknown series items

        Returns:
            List of queue items
        """
        params = {
            "pageSize": 1000,
            "includeUnknownSeriesItems": include_unknown,
            "includeSeries": True,
            "includeEpisode": True,
        }

        try:
            response = self._make_request("GET", "/queue", params=params)
            data = response.json()
            return data.get("records", [])

        except Exception as e:
            logger.error(f"Failed to get queue: {e}")
            return []
    
    def get_queue_cached(self, include_unknown: bool = True) -> List[Dict]:
        """
        Get current download queue with caching (60 second TTL).
        
        Args:
            include_unknown: Include unknown series items
            
        Returns:
            List of queue items
        """
        cache_key = f"queue_{include_unknown}"
        cached_result = self.cache.get(cache_key)
        
        if cached_result is not None:
            logger.debug("Using cached queue data")
            return cached_result
            
        result = self.get_queue(include_unknown)
        self.cache.set(cache_key, result, ttl=60)  # 1 minute cache
        return result

    @retry(max_attempts=2, delay=1.0, exceptions=SonarrAPIError)
    def get_series_by_title(self, title: str) -> Optional[Dict]:
        """
        Find series by title (case-insensitive partial match).

        Args:
            title: Series title to search for

        Returns:
            Series data if found, None otherwise
        """
        try:
            response = self._make_request("GET", "/series")
            series_list = response.json()

            title_lower = title.lower()
            for series in series_list:
                if title_lower in series["title"].lower():
                    return series

            return None

        except Exception as e:
            logger.error(f"Failed to search for series '{title}': {e}")
            return None

    @retry(max_attempts=2, delay=1.0, exceptions=SonarrAPIError)
    def get_episode_info(
        self, series_id: int, season: int, episode: int
    ) -> Optional[Dict]:
        """
        Get specific episode information.

        Args:
            series_id: Sonarr series ID
            season: Season number
            episode: Episode number

        Returns:
            Episode data if found, None otherwise
        """
        params = {"seriesId": series_id}

        try:
            response = self._make_request("GET", "/episode", params=params)
            episodes = response.json()

            for ep in episodes:
                if ep["seasonNumber"] == season and ep["episodeNumber"] == episode:
                    return ep

            return None

        except Exception as e:
            logger.error(
                f"Failed to get episode S{season:02d}E{episode:02d} for series {series_id}: {e}"
            )
            return None

    @retry(max_attempts=2, delay=1.0, exceptions=SonarrAPIError)
    def get_history_for_episode(self, episode_id: int, limit: int = 50) -> List[Dict]:
        """
        Get history for specific episode.

        Args:
            episode_id: Sonarr episode ID
            limit: Maximum number of history entries to return

        Returns:
            List of history entries
        """
        params = {
            "pageSize": limit,
            "episodeId": episode_id,
            "sortKey": "date",
            "sortDirection": "descending",
        }

        try:
            response = self._make_request("GET", "/history", params=params)
            data = response.json()
            return data.get("records", [])

        except Exception as e:
            logger.error(f"Failed to get history for episode {episode_id}: {e}")
            return []

    @retry(max_attempts=2, delay=1.0, exceptions=SonarrAPIError)
    def get_episode_file(self, episode_file_id: int) -> Optional[Dict]:
        """
        Get episode file details.

        Args:
            episode_file_id: Sonarr episode file ID

        Returns:
            Episode file data if found, None otherwise
        """
        try:
            response = self._make_request("GET", f"/episodefile/{episode_file_id}")
            return response.json()

        except Exception as e:
            logger.error(f"Failed to get episode file {episode_file_id}: {e}")
            return None

    @retry(max_attempts=2, delay=2.0, exceptions=SonarrAPIError)
    def force_import(
        self, download_id: str, episode_id: int, quality: Dict = None
    ) -> bool:
        """
        Force import of a download.

        Args:
            download_id: Download ID from queue
            episode_id: Episode ID to import to
            quality: Quality information (optional)

        Returns:
            True if successful, False otherwise
        """
        try:
            # Get manual import candidates
            params = {"downloadId": download_id}
            response = self._make_request("GET", "/manualimport", params=params)
            import_items = response.json()

            if not import_items:
                logger.warning(
                    f"No manual import candidates found for download {download_id}"
                )
                return False

            # Prepare import items
            for item in import_items:
                item["episodeIds"] = [episode_id]
                if quality:
                    item["quality"] = quality

            # Execute import
            response = self._make_request("PUT", "/manualimport", json=import_items)

            logger.info(f"Force import successful for download {download_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to force import {download_id}: {e}")
            return False

    @retry(max_attempts=2, delay=1.0, exceptions=SonarrAPIError)
    def remove_from_queue(
        self, queue_id: int, remove_from_client: bool = True, blocklist: bool = False
    ) -> bool:
        """
        Remove item from download queue.

        Args:
            queue_id: Queue item ID
            remove_from_client: Also remove from download client
            blocklist: Add to blocklist

        Returns:
            True if successful, False otherwise
        """
        params = {"removeFromClient": remove_from_client, "blocklist": blocklist}

        try:
            response = self._make_request("DELETE", f"/queue/{queue_id}", params=params)
            logger.info(f"Successfully removed queue item {queue_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to remove queue item {queue_id}: {e}")
            return False

    def clear_cache(self):
        """Clear internal caches."""
        self._custom_formats_cache = None
        self._quality_profiles_cache = None
        self._series_profile_map_cache = None
        logger.info("Cleared Sonarr API caches")
