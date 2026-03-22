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

    def __init__(self, config: ConfigLoader, app_type: str = None):
        """
        Initialize API client for Sonarr or Radarr.

        Args:
            config: Configuration loader instance
            app_type: 'sonarr' or 'radarr' (defaults to config app.type)
        """
        self.app_type = app_type or config.get("app.type", "sonarr")
        prefix = "radarr" if self.app_type == "radarr" else "sonarr"
        self.base_url = config.get(f"{prefix}.url").rstrip("/")
        self.api_key = config.get(f"{prefix}.api_key")
        self.timeout = config.get(f"{prefix}.timeout", 30)

        self.headers = {"X-Api-Key": self.api_key, "Content-Type": "application/json"}

        # Create persistent session for connection pooling
        self.session = requests.Session()
        self.session.headers.update(self.headers)

        # Configure session for better performance
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=10,
            pool_maxsize=20,
            max_retries=0,  # We handle retries manually
        )
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

        # TTL cache for API responses
        self.cache = TTLCache(default_ttl=300)  # 5 minutes default

        # Cache for configuration data that rarely changes
        self._custom_formats_cache: Optional[Dict[int, Dict]] = None
        self._quality_profiles_cache: Optional[Dict[int, Dict]] = None
        self._series_profile_map_cache: Optional[Dict[int, int]] = None

        app_label = "Radarr" if self.app_type == "radarr" else "Sonarr"
        logger.debug(f"Initialized {app_label} client for: {self.base_url}")

    def close(self):
        """Close the session and cleanup resources."""
        if hasattr(self, "session"):
            self.session.close()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit with cleanup."""
        self.close()

    def get_cache_stats(self) -> Dict:
        """Get cache statistics for monitoring."""
        return {"cache_stats": self.cache.stats(), "cache_size": self.cache.size()}

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

            logger.debug(
                f"Connected to Sonarr {data.get('version', 'unknown version')}"
            )
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

            logger.debug(f"Loaded {len(result)} custom formats")
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

            logger.debug(f"Loaded {len(result)} quality profiles")
            return result

        except Exception as e:
            logger.error(f"Failed to fetch quality profiles: {e}")
            return {}

    @retry(max_attempts=2, delay=2.0, exceptions=SonarrAPIError)
    def build_series_profile_map(self) -> Dict[int, int]:
        """
        Build a map of series/movie ID to quality profile ID.

        Returns:
            Dictionary mapping series/movie ID to quality profile ID
        """
        if self._series_profile_map_cache is not None:
            return self._series_profile_map_cache

        endpoint = "/movie" if self.app_type == "radarr" else "/series"
        label = "movie" if self.app_type == "radarr" else "series"

        try:
            response = self._make_request("GET", endpoint)
            items = response.json()

            result = {}
            for item in items:
                item_id = item["id"]
                profile_id = item.get("qualityProfileId")
                if profile_id:
                    result[item_id] = profile_id

            self._series_profile_map_cache = result

            logger.debug(f"Built {label} profile map for {len(result)} {label}")
            return result

        except Exception as e:
            logger.error(f"Failed to build {label} profile map: {e}")
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
        if self.app_type == "radarr":
            params = {
                "pageSize": 1000,
                "includeUnknownMovieItems": include_unknown,
                "includeMovie": True,
            }
        else:
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
        Find series/movie by title (case-insensitive partial match).

        Args:
            title: Series/movie title to search for

        Returns:
            Series/movie data if found, None otherwise
        """
        endpoint = "/movie" if self.app_type == "radarr" else "/series"
        label = "movie" if self.app_type == "radarr" else "series"

        try:
            response = self._make_request("GET", endpoint)
            items = response.json()

            title_lower = title.lower()
            for item in items:
                if title_lower in item["title"].lower():
                    return item

            return None

        except Exception as e:
            logger.error(f"Failed to search for {label} '{title}': {e}")
            return None

    @retry(max_attempts=2, delay=1.0, exceptions=SonarrAPIError)
    def get_episode_info(
        self, series_id: int, season: int = None, episode: int = None
    ) -> Optional[Dict]:
        """
        Get specific episode or movie information.

        For Sonarr: looks up episode by series_id + season + episode number.
        For Radarr: looks up movie by ID (season/episode ignored).

        Args:
            series_id: Sonarr series ID or Radarr movie ID
            season: Season number (Sonarr only)
            episode: Episode number (Sonarr only)

        Returns:
            Episode/movie data if found, None otherwise
        """
        if self.app_type == "radarr":
            try:
                response = self._make_request("GET", f"/movie/{series_id}")
                return response.json()
            except Exception as e:
                logger.error(f"Failed to get movie {series_id}: {e}")
                return None

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
        Get history for specific episode or movie.

        Args:
            episode_id: Sonarr episode ID or Radarr movie ID
            limit: Maximum number of history entries to return

        Returns:
            List of history entries
        """
        id_key = "movieId" if self.app_type == "radarr" else "episodeId"
        params = {
            "pageSize": limit,
            id_key: episode_id,
            "sortKey": "date",
            "sortDirection": "descending",
        }

        try:
            response = self._make_request("GET", "/history", params=params)
            data = response.json()
            return data.get("records", [])

        except Exception as e:
            label = "movie" if self.app_type == "radarr" else "episode"
            logger.error(f"Failed to get history for {label} {episode_id}: {e}")
            return []

    @retry(max_attempts=2, delay=1.0, exceptions=SonarrAPIError)
    def get_episode_file(self, file_id: int) -> Optional[Dict]:
        """
        Get episode/movie file details.

        Args:
            file_id: Sonarr episode file ID or Radarr movie file ID

        Returns:
            File data if found, None otherwise
        """
        endpoint = "/moviefile" if self.app_type == "radarr" else "/episodefile"

        try:
            response = self._make_request("GET", f"{endpoint}/{file_id}")
            return response.json()

        except Exception as e:
            label = "movie" if self.app_type == "radarr" else "episode"
            logger.error(f"Failed to get {label} file {file_id}: {e}")
            return None

    def _get_manual_import_candidates(
        self,
        download_id: str,
        output_path: str = None,
        content_id: int = None,
    ) -> List:
        """
        Get manual import candidates, trying downloadId first then folder fallback.

        Args:
            download_id: Download ID from queue
            output_path: Download output path for folder fallback
            content_id: Series ID (Sonarr) or movie ID (Radarr) for fallback

        Returns:
            List of import candidate items (empty if none found).
        """
        params = {"downloadId": download_id}
        response = self._make_request("GET", "/manualimport", params=params)
        import_items = response.json()

        if not import_items and output_path:
            logger.debug(f"Folder-based manual import fallback for: {output_path}")
            fallback_params = {
                "folder": output_path,
                "filterExistingFiles": "false",
            }
            if content_id:
                id_key = "movieId" if self.app_type == "radarr" else "seriesId"
                fallback_params[id_key] = content_id
            response = self._make_request(
                "GET", "/manualimport", params=fallback_params
            )
            import_items = response.json()

        return import_items

    def _prepare_import_item(self, item: Dict, quality: Dict = None):
        """Prepare a single import item with app-type-specific fields."""
        if self.app_type == "radarr":
            movie = item.get("movie", {})
            movie_id = movie.get("id")
            if movie_id:
                item["movieId"] = movie_id
        else:
            episodes = item.get("episodes", [])
            episode_ids = [ep.get("id") for ep in episodes if ep.get("id")]
            if episode_ids:
                item["episodeIds"] = episode_ids
            series = item.get("series", {})
            sid = series.get("id")
            if sid:
                item["seriesId"] = sid

        if quality:
            item["quality"] = quality

    @retry(max_attempts=2, delay=2.0, exceptions=SonarrAPIError)
    def force_import(
        self,
        download_id: str,
        quality: Dict = None,
        output_path: str = None,
        content_id: int = None,
    ) -> tuple[bool, Optional[int]]:
        """
        Force import of a download.

        Tries downloadId-based lookup first, then falls back to folder-based
        lookup using outputPath if available.

        Args:
            download_id: Download ID from queue
            quality: Quality information (optional)
            output_path: Download output path for folder-based fallback (optional)
            content_id: Series ID (Sonarr) or movie ID (Radarr) for fallback

        Returns:
            Tuple of (success: bool, command_id: Optional[int]).
            command_id is -1 when no import candidates found from any method
            (permanently unimportable), vs None for transient API errors.
        """
        try:
            import_items = self._get_manual_import_candidates(
                download_id, output_path, content_id
            )

            if not import_items:
                logger.warning(
                    f"No manual import candidates found for download {download_id}"
                )
                return False, -1

            # Prepare import items based on app type
            for item in import_items:
                self._prepare_import_item(item, quality)

            # Execute import using command API
            command_payload = {
                "name": "ManualImport",
                "files": import_items,
                "importMode": "move",
            }

            response = self._make_request("POST", "/command", json=command_payload)

            if response.status_code == 201:
                command_data = response.json()
                command_id = command_data.get("id")
                logger.debug(
                    f"Import command queued: download={download_id} cmd={command_id}"
                )
                return True, command_id
            else:
                logger.warning(f"Force import command returned {response.status_code}")
                return False, None

        except Exception as e:
            logger.error(f"Failed to force import {download_id}: {e}")
            return False, None

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
            logger.debug(f"Removed queue item {queue_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to remove queue item {queue_id}: {e}")
            return False

    def should_cleanup_queue_item(self, queue_item: Dict) -> bool:
        """
        Check if a queue item should be cleaned up after force import.

        This happens when using 'move' mode and the file was successfully moved,
        but the queue item persists with "No files found" message.

        Args:
            queue_item: Queue item data

        Returns:
            True if the item should be removed from queue
        """
        # Check if the item has "No files found" message
        status_messages = queue_item.get("statusMessages", [])
        for msg in status_messages:
            messages = msg.get("messages", [])
            for message_text in messages:
                if "No files found are eligible for import" in message_text:
                    logger.debug(
                        f"Queue item {queue_item.get('id')} has 'No files found' message"
                    )
                    return True
        return False

    def cleanup_post_import_queue_item(self, download_id: str) -> bool:
        """
        Clean up queue item after successful force import.

        When using 'move' mode, the file is physically moved but the queue
        item may persist. This method removes such items from the queue.

        Args:
            download_id: Download ID to look for in queue

        Returns:
            True if cleanup was performed, False otherwise
        """
        try:
            # Get current queue
            queue = self.get_queue()

            for item in queue:
                if item.get("downloadId") == download_id:
                    if self.should_cleanup_queue_item(item):
                        queue_id = item.get("id")
                        logger.debug(
                            f"Cleaning up stuck queue item {queue_id} after import"
                        )

                        # Remove from queue but keep in download client
                        success = self.remove_from_queue(
                            queue_id, remove_from_client=False, blocklist=False
                        )

                        if success:
                            logger.debug("Cleaned up post-import queue item")
                        else:
                            logger.warning(
                                "⚠️ Failed to clean up post-import queue item"
                            )

                        return success

            logger.debug(f"No cleanup needed for download {download_id}")
            return False

        except Exception as e:
            logger.error(
                f"Failed to cleanup queue item for download {download_id}: {e}"
            )
            return False

    def clear_cache(self):
        """Clear internal caches."""
        self._custom_formats_cache = None
        self._quality_profiles_cache = None
        self._series_profile_map_cache = None
        logger.debug("Cleared API caches")
