"""
Unit tests for SonarrClient API wrapper.
"""

from unittest.mock import MagicMock, Mock, patch

import pytest
import requests
import requests_mock

from src.api.sonarr_client import SonarrAPIError, SonarrClient
from src.utils.decorators import RetryError


class TestSonarrClient:
    """Test SonarrClient class"""

    @pytest.fixture
    def client(self, mock_config):
        """Create SonarrClient instance for testing"""
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "sonarr.url": mock_config["sonarr"]["url"],
            "sonarr.api_key": mock_config["sonarr"]["api_key"],
            "sonarr.timeout": 30,
        }.get(key, default)

        return SonarrClient(config)

    def test_init(self, client, mock_config):
        """Test client initialization"""
        assert client.base_url == "http://test-sonarr:8989"
        assert client.api_key == mock_config["sonarr"]["api_key"]
        assert client.timeout == 30
        assert client.headers["X-Api-Key"] == mock_config["sonarr"]["api_key"]
        assert client.headers["Content-Type"] == "application/json"

    def test_init_strips_trailing_slash(self, mock_config):
        """Test that trailing slash is stripped from base URL"""
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "sonarr.url": "http://test-sonarr:8989/",  # With trailing slash
            "sonarr.api_key": mock_config["sonarr"]["api_key"],
            "sonarr.timeout": 30,
        }.get(key, default)

        client = SonarrClient(config)
        assert client.base_url == "http://test-sonarr:8989"


class TestMakeRequest:
    """Test _make_request method"""

    @pytest.fixture
    def client(self, mock_config):
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "sonarr.url": mock_config["sonarr"]["url"],
            "sonarr.api_key": mock_config["sonarr"]["api_key"],
            "sonarr.timeout": 30,
        }.get(key, default)
        return SonarrClient(config)

    def test_make_request_success(self, client):
        """Test successful API request"""
        with requests_mock.Mocker() as m:
            m.get(
                "http://test-sonarr:8989/api/v3/system/status",
                json={"version": "3.0.0"},
                status_code=200,
            )

            response = client._make_request("GET", "/system/status")

            assert response.status_code == 200
            assert response.json()["version"] == "3.0.0"

    def test_make_request_adds_api_prefix(self, client):
        """Test that API prefix is added correctly"""
        with requests_mock.Mocker() as m:
            m.get("http://test-sonarr:8989/api/v3/test", json={}, status_code=200)

            # Test endpoint without prefix
            response = client._make_request("GET", "test")
            assert response.status_code == 200

            # Test endpoint with leading slash
            response = client._make_request("GET", "/test")
            assert response.status_code == 200

    def test_make_request_preserves_api_prefix(self, client):
        """Test that existing API prefix is preserved"""
        with requests_mock.Mocker() as m:
            m.get(
                "http://test-sonarr:8989/api/v3/system/status", json={}, status_code=200
            )

            response = client._make_request("GET", "/api/v3/system/status")
            assert response.status_code == 200

    def test_make_request_http_error(self, client):
        """Test handling of HTTP errors"""
        with requests_mock.Mocker() as m:
            m.get(
                "http://test-sonarr:8989/api/v3/test",
                json={"message": "Not found"},
                status_code=404,
            )

            with pytest.raises(SonarrAPIError) as exc_info:
                client._make_request("GET", "test")

            assert "Sonarr API error: 404" in str(exc_info.value)
            assert "Not found" in str(exc_info.value)

    def test_make_request_http_error_no_json(self, client):
        """Test handling of HTTP errors without JSON response"""
        with requests_mock.Mocker() as m:
            m.get(
                "http://test-sonarr:8989/api/v3/test",
                text="Internal Server Error",
                status_code=500,
            )

            with pytest.raises(SonarrAPIError) as exc_info:
                client._make_request("GET", "test")

            assert "Sonarr API error: 500" in str(exc_info.value)

    def test_make_request_connection_error(self, client):
        """Test handling of connection errors"""
        with requests_mock.Mocker() as m:
            m.get("http://test-sonarr:8989/api/v3/test", exc=requests.ConnectionError)

            with pytest.raises(SonarrAPIError) as exc_info:
                client._make_request("GET", "test")

            assert "Request failed" in str(exc_info.value)


class TestConnectionTesting:
    """Test connection testing functionality"""

    @pytest.fixture
    def client(self, mock_config):
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "sonarr.url": mock_config["sonarr"]["url"],
            "sonarr.api_key": mock_config["sonarr"]["api_key"],
            "sonarr.timeout": 30,
        }.get(key, default)
        return SonarrClient(config)

    def test_test_connection_success(self, client):
        """Test successful connection test"""
        with requests_mock.Mocker() as m:
            m.get(
                "http://test-sonarr:8989/api/v3/system/status",
                json={"version": "3.0.0"},
            )

            result = client.test_connection()
            assert result is True

    def test_test_connection_failure(self, client):
        """Test failed connection test"""
        with requests_mock.Mocker() as m:
            m.get(
                "http://test-sonarr:8989/api/v3/system/status",
                exc=requests.ConnectionError,
            )

            result = client.test_connection()
            assert result is False


class TestCustomFormats:
    """Test custom format related methods"""

    @pytest.fixture
    def client(self, mock_config):
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "sonarr.url": mock_config["sonarr"]["url"],
            "sonarr.api_key": mock_config["sonarr"]["api_key"],
            "sonarr.timeout": 30,
        }.get(key, default)
        return SonarrClient(config)

    def test_fetch_custom_formats_success(self, client):
        """Test successful custom formats fetch"""
        mock_formats = [{"id": 1, "name": "HDR"}, {"id": 2, "name": "Atmos"}]

        with requests_mock.Mocker() as m:
            m.get("http://test-sonarr:8989/api/v3/customformat", json=mock_formats)

            result = client.fetch_custom_formats()

            assert len(result) == 2
            assert result[1]["name"] == "HDR"
            assert result[2]["name"] == "Atmos"

    def test_fetch_custom_formats_cached(self, client):
        """Test that custom formats are cached"""
        mock_formats = [{"id": 1, "name": "HDR"}]

        with requests_mock.Mocker() as m:
            m.get("http://test-sonarr:8989/api/v3/customformat", json=mock_formats)

            # First call
            result1 = client.fetch_custom_formats()

            # Second call should use cache (no additional request)
            result2 = client.fetch_custom_formats()

            assert result1 == result2
            assert len(m.request_history) == 1

    def test_fetch_custom_formats_error(self, client):
        """Test custom formats fetch error handling"""
        with requests_mock.Mocker() as m:
            m.get(
                "http://test-sonarr:8989/api/v3/customformat",
                exc=requests.ConnectionError,
            )

            result = client.fetch_custom_formats()
            assert result == {}


class TestQualityProfiles:
    """Test quality profile related methods"""

    @pytest.fixture
    def client(self, mock_config):
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "sonarr.url": mock_config["sonarr"]["url"],
            "sonarr.api_key": mock_config["sonarr"]["api_key"],
            "sonarr.timeout": 30,
        }.get(key, default)
        return SonarrClient(config)

    def test_fetch_quality_profiles_success(self, client):
        """Test successful quality profiles fetch"""
        mock_profiles = [
            {
                "id": 1,
                "name": "HD-1080p",
                "formatItems": [
                    {"format": 1, "score": 100},
                    {"format": 2, "score": 50},
                ],
            }
        ]

        with requests_mock.Mocker() as m:
            m.get("http://test-sonarr:8989/api/v3/qualityprofile", json=mock_profiles)

            result = client.fetch_quality_profiles()

            assert len(result) == 1
            assert result[1]["name"] == "HD-1080p"

    def test_build_series_profile_map_success(self, client):
        """Test building series to profile mapping"""
        mock_series = [
            {"id": 10, "title": "Test Series", "qualityProfileId": 1},
            {"id": 20, "title": "Another Series", "qualityProfileId": 2},
        ]

        with requests_mock.Mocker() as m:
            m.get("http://test-sonarr:8989/api/v3/series", json=mock_series)

            result = client.build_series_profile_map()

            assert result[10] == 1
            assert result[20] == 2

    def test_get_custom_format_scores(self, client):
        """Test getting custom format scores for a series"""
        # Setup mocks
        with requests_mock.Mocker() as m:
            # Mock series profile map
            m.get(
                "http://test-sonarr:8989/api/v3/series",
                json=[{"id": 10, "qualityProfileId": 1}],
            )

            # Mock quality profiles
            m.get(
                "http://test-sonarr:8989/api/v3/qualityprofile",
                json=[
                    {
                        "id": 1,
                        "name": "HD-1080p",
                        "formatItems": [
                            {"format": 1, "score": 100},
                            {"format": 2, "score": 50},
                        ],
                    }
                ],
            )

            result = client.get_custom_format_scores(10)

            assert result[1] == 100
            assert result[2] == 50

    def test_get_custom_format_scores_series_not_found(self, client):
        """Test getting scores for non-existent series"""
        with requests_mock.Mocker() as m:
            m.get("http://test-sonarr:8989/api/v3/series", json=[])
            m.get("http://test-sonarr:8989/api/v3/qualityprofile", json=[])

            result = client.get_custom_format_scores(999)
            assert result == {}


class TestQueue:
    """Test queue related methods"""

    @pytest.fixture
    def client(self, mock_config):
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "sonarr.url": mock_config["sonarr"]["url"],
            "sonarr.api_key": mock_config["sonarr"]["api_key"],
            "sonarr.timeout": 30,
        }.get(key, default)
        return SonarrClient(config)

    def test_get_queue_success(self, client, mock_sonarr_queue_item):
        """Test successful queue retrieval"""
        with requests_mock.Mocker() as m:
            m.get(
                "http://test-sonarr:8989/api/v3/queue",
                json={"records": [mock_sonarr_queue_item]},
            )

            result = client.get_queue()

            assert len(result) == 1
            assert result[0]["id"] == 1
            assert result[0]["downloadId"] == "abc123def456"

    def test_get_queue_error(self, client):
        """Test queue retrieval error handling"""
        with requests_mock.Mocker() as m:
            m.get("http://test-sonarr:8989/api/v3/queue", exc=requests.ConnectionError)

            result = client.get_queue()
            assert result == []

    def test_remove_from_queue_success(self, client):
        """Test successful queue item removal"""
        with requests_mock.Mocker() as m:
            m.delete("http://test-sonarr:8989/api/v3/queue/1", json={"success": True})

            result = client.remove_from_queue(
                1, remove_from_client=True, blocklist=False
            )
            assert result is True

    def test_remove_from_queue_error(self, client):
        """Test queue item removal error handling"""
        with requests_mock.Mocker() as m:
            m.delete(
                "http://test-sonarr:8989/api/v3/queue/1", exc=requests.ConnectionError
            )

            result = client.remove_from_queue(1)
            assert result is False


class TestHistory:
    """Test history related methods"""

    @pytest.fixture
    def client(self, mock_config):
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "sonarr.url": mock_config["sonarr"]["url"],
            "sonarr.api_key": mock_config["sonarr"]["api_key"],
            "sonarr.timeout": 30,
        }.get(key, default)
        return SonarrClient(config)

    def test_get_history_for_episode_success(self, client, mock_sonarr_history_item):
        """Test successful episode history retrieval"""
        with requests_mock.Mocker() as m:
            m.get(
                "http://test-sonarr:8989/api/v3/history",
                json={"records": [mock_sonarr_history_item]},
            )

            result = client.get_history_for_episode(100)

            assert len(result) == 1
            assert result[0]["episodeId"] == 100
            assert result[0]["eventType"] == "grabbed"

    def test_get_history_for_episode_with_limit(self, client):
        """Test episode history with custom limit"""
        with requests_mock.Mocker() as m:
            m.get("http://test-sonarr:8989/api/v3/history", json={"records": []})

            client.get_history_for_episode(100, limit=25)

            # Check that limit was passed correctly
            request = m.request_history[0]
            assert "pageSize=25" in request.url
            assert "episodeId=100" in request.url

    def test_get_history_for_episode_error(self, client):
        """Test episode history error handling"""
        with requests_mock.Mocker() as m:
            m.get(
                "http://test-sonarr:8989/api/v3/history", exc=requests.ConnectionError
            )

            result = client.get_history_for_episode(100)
            assert result == []

    def test_get_history_for_download_filters_and_encodes_id(
        self, client, mock_sonarr_history_item
    ):
        """Download history uses the Arr downloadId filter and URL encoding."""
        download_id = "ABC/123+mixed="
        with requests_mock.Mocker() as m:
            m.get(
                "http://test-sonarr:8989/api/v3/history",
                json={"records": [mock_sonarr_history_item]},
            )

            result = client.get_history_for_download(download_id, limit=25)

            assert result == [mock_sonarr_history_item]
            request = m.request_history[0]
            assert request.qs["downloadid"] == [download_id.lower()]
            assert request.qs["pagesize"] == ["25"]
            assert request.qs["sortkey"] == ["date"]
            assert request.qs["sortdirection"] == ["descending"]
            assert "%2F" in request.url and "%2B" in request.url

    def test_get_history_for_download_error(self, client):
        """Download history API failures remain distinguishable from no records."""
        with requests_mock.Mocker() as m:
            m.get(
                "http://test-sonarr:8989/api/v3/history",
                exc=requests.ConnectionError,
            )

            with pytest.raises(RetryError):
                client.get_history_for_download("download123")


class TestSeriesOperations:
    """Test series related methods"""

    @pytest.fixture
    def client(self, mock_config):
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "sonarr.url": mock_config["sonarr"]["url"],
            "sonarr.api_key": mock_config["sonarr"]["api_key"],
            "sonarr.timeout": 30,
        }.get(key, default)
        return SonarrClient(config)

    def test_get_series_by_title_found(self, client):
        """Test finding series by title"""
        mock_series = [
            {"id": 1, "title": "Breaking Bad"},
            {"id": 2, "title": "Better Call Saul"},
        ]

        with requests_mock.Mocker() as m:
            m.get("http://test-sonarr:8989/api/v3/series", json=mock_series)

            result = client.get_series_by_title("breaking")

            assert result is not None
            assert result["id"] == 1
            assert result["title"] == "Breaking Bad"

    def test_get_series_by_title_not_found(self, client):
        """Test series not found by title"""
        with requests_mock.Mocker() as m:
            m.get(
                "http://test-sonarr:8989/api/v3/series",
                json=[{"id": 1, "title": "Breaking Bad"}],
            )

            result = client.get_series_by_title("nonexistent")
            assert result is None

    def test_get_series_by_title_case_insensitive(self, client):
        """Test case insensitive series search"""
        mock_series = [{"id": 1, "title": "Breaking Bad"}]

        with requests_mock.Mocker() as m:
            m.get("http://test-sonarr:8989/api/v3/series", json=mock_series)

            # Test various cases
            assert client.get_series_by_title("BREAKING") is not None
            assert client.get_series_by_title("breaking bad") is not None
            assert client.get_series_by_title("Bad") is not None


class TestEpisodeOperations:
    """Test episode related methods"""

    @pytest.fixture
    def client(self, mock_config):
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "sonarr.url": mock_config["sonarr"]["url"],
            "sonarr.api_key": mock_config["sonarr"]["api_key"],
            "sonarr.timeout": 30,
        }.get(key, default)
        return SonarrClient(config)

    def test_get_episode_info_found(self, client):
        """Test finding episode by series, season, and episode number"""
        mock_episodes = [
            {"id": 100, "seriesId": 10, "seasonNumber": 1, "episodeNumber": 1},
            {"id": 101, "seriesId": 10, "seasonNumber": 1, "episodeNumber": 2},
        ]

        with requests_mock.Mocker() as m:
            m.get("http://test-sonarr:8989/api/v3/episode", json=mock_episodes)

            result = client.get_episode_info(10, 1, 2)

            assert result is not None
            assert result["id"] == 101
            assert result["episodeNumber"] == 2

    def test_get_episode_info_not_found(self, client):
        """Test episode not found"""
        with requests_mock.Mocker() as m:
            m.get(
                "http://test-sonarr:8989/api/v3/episode",
                json=[{"id": 100, "seasonNumber": 1, "episodeNumber": 1}],
            )

            result = client.get_episode_info(10, 2, 1)  # Wrong season
            assert result is None

    def test_get_episode_file_success(self, client):
        """Test successful episode file retrieval"""
        mock_file = {
            "id": 50,
            "customFormatScore": 100,
            "customFormats": [{"name": "HDR"}],
        }

        with requests_mock.Mocker() as m:
            m.get("http://test-sonarr:8989/api/v3/episodefile/50", json=mock_file)

            result = client.get_episode_file(50)

            assert result["id"] == 50
            assert result["customFormatScore"] == 100

    def test_get_episode_file_error(self, client):
        """Test episode file retrieval error"""
        with requests_mock.Mocker() as m:
            m.get("http://test-sonarr:8989/api/v3/episodefile/50", status_code=404)

            result = client.get_episode_file(50)
            assert result is None


class TestImportOperations:
    """Test import related methods"""

    @pytest.fixture
    def client(self, mock_config):
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "sonarr.url": mock_config["sonarr"]["url"],
            "sonarr.api_key": mock_config["sonarr"]["api_key"],
            "sonarr.timeout": 30,
        }.get(key, default)
        return SonarrClient(config)

    def test_force_import_success(self, client):
        """Test successful force import"""
        mock_import_items = [
            {
                "id": 1,
                "path": "/downloads/test.mkv",
                "quality": {"quality": {"id": 6}},
                "customFormats": [],
                "series": {"id": 10, "title": "Test Series"},
                "episodes": [{"id": 100, "title": "Test Episode"}],
            }
        ]

        with requests_mock.Mocker() as m:
            # Mock manual import candidates
            m.get("http://test-sonarr:8989/api/v3/manualimport", json=mock_import_items)

            # Mock command execution
            m.post(
                "http://test-sonarr:8989/api/v3/command",
                json={"name": "ManualImport", "id": 123},
                status_code=201,
            )

            success, command_id = client.force_import("download123")
            assert success is True
            assert command_id == 123

    def test_force_import_candidate_uses_validated_sonarr_target(self, client):
        """A validated mismatch candidate is submitted without another lookup."""
        candidate = {
            "path": "/downloads/correct.mkv",
            "series": {"id": 316},
            "episodes": [{"id": 10782}],
        }
        with requests_mock.Mocker() as m:
            m.post(
                "http://test-sonarr:8989/api/v3/command",
                json={"id": 123},
                status_code=201,
            )

            success, _ = client.force_import_candidate(candidate)

            assert success is True
            import json

            payload = json.loads(m.request_history[-1].body)
            assert [item["path"] for item in payload["files"]] == [
                "/downloads/correct.mkv"
            ]
            assert payload["files"][0]["seriesId"] == 316
            assert payload["files"][0]["episodeIds"] == [10782]
            assert all(request.method != "GET" for request in m.request_history)

    def test_force_import_preserves_all_season_pack_candidates(self, client):
        """Ordinary force imports retain the existing season-pack behavior."""
        items = [
            {
                "path": "/downloads/episode1.mkv",
                "series": {"id": 10},
                "episodes": [{"id": 100}],
            },
            {
                "path": "/downloads/episode2.mkv",
                "series": {"id": 10},
                "episodes": [{"id": 101}],
            },
        ]
        with requests_mock.Mocker() as m:
            m.get("http://test-sonarr:8989/api/v3/manualimport", json=items)
            m.post(
                "http://test-sonarr:8989/api/v3/command",
                json={"id": 124},
                status_code=201,
            )

            success, _ = client.force_import("season-pack", content_id=10)

            assert success is True
            import json

            payload = json.loads(m.request_history[-1].body)
            assert [item["path"] for item in payload["files"]] == [
                "/downloads/episode1.mkv",
                "/downloads/episode2.mkv",
            ]

    def test_get_manual_import_candidates_download_id_shape(self, client):
        """Expose Sonarr manual-import candidates without mutating them."""
        mock_items = [
            {
                "path": "/downloads/test.mkv",
                "series": {"id": 10},
                "episodes": [{"id": 100}],
                "customFormatScore": 1404,
            }
        ]
        with requests_mock.Mocker() as m:
            m.get("http://test-sonarr:8989/api/v3/manualimport", json=mock_items)

            result = client.get_manual_import_candidates("ABC/123+")

            assert result == mock_items
            request = m.request_history[0]
            assert request.qs["downloadid"] == ["abc/123+"]
            assert "%2F" in request.url and "%2B" in request.url
            assert "episodeIds" not in result[0]

    def test_get_manual_import_candidates_folder_fallback_params(self, client):
        """Public candidate retrieval retains the folder fallback behavior."""
        mock_items = [
            {
                "path": "/downloads/test.mkv",
                "series": {"id": 10},
                "episodes": [{"id": 100}],
            }
        ]
        with requests_mock.Mocker() as m:
            m.get(
                "http://test-sonarr:8989/api/v3/manualimport",
                [{"json": []}, {"json": mock_items}],
            )

            result = client.get_manual_import_candidates(
                "download123", "/downloads/Series Name", 10
            )

            assert result == mock_items
            fallback = m.request_history[1]
            assert fallback.qs["folder"] == ["/downloads/series name"]
            assert fallback.qs["filterexistingfiles"] == ["false"]
            assert fallback.qs["seriesid"] == ["10"]

    def test_get_manual_import_candidates_api_error(self, client):
        """Candidate API failures remain distinguishable from empty results."""
        with requests_mock.Mocker() as m:
            m.get(
                "http://test-sonarr:8989/api/v3/manualimport",
                exc=requests.ConnectionError,
            )

            with pytest.raises(SonarrAPIError):
                client.get_manual_import_candidates("download123")

    def test_force_import_no_candidates(self, client):
        """Test force import with no candidates returns unimportable sentinel"""
        with requests_mock.Mocker() as m:
            m.get("http://test-sonarr:8989/api/v3/manualimport", json=[])

            success, command_id = client.force_import("download123")
            assert success is False
            assert command_id == -1

    def test_force_import_folder_fallback_success(self, client):
        """Test force import falls back to folder-based lookup"""
        mock_import_items = [
            {
                "path": "/downloads/test.mkv",
                "episodes": [{"id": 100}],
                "series": {"id": 10},
            }
        ]

        with requests_mock.Mocker() as m:
            # First call (by downloadId) returns empty
            # Second call (by folder) returns items
            m.get(
                "http://test-sonarr:8989/api/v3/manualimport",
                [{"json": []}, {"json": mock_import_items}],
            )
            m.post(
                "http://test-sonarr:8989/api/v3/command",
                json={"name": "ManualImport", "id": 456},
                status_code=201,
            )

            success, command_id = client.force_import(
                "download123",
                output_path="/downloads/complete/tv/Test.Episode",
                content_id=10,
            )
            assert success is True
            assert command_id == 456

            # Verify two GET requests were made (qs keys are lowercased)
            get_requests = [h for h in m.request_history if h.method == "GET"]
            assert len(get_requests) == 2
            assert "downloadid" in get_requests[0].qs
            assert "folder" in get_requests[1].qs
            assert get_requests[1].qs["filterexistingfiles"] == ["false"]
            assert get_requests[1].qs["seriesid"] == ["10"]

    def test_force_import_both_methods_empty(self, client):
        """Test force import returns -1 when both methods find no candidates"""
        with requests_mock.Mocker() as m:
            m.get("http://test-sonarr:8989/api/v3/manualimport", json=[])

            success, command_id = client.force_import(
                "download123",
                output_path="/downloads/complete/tv/Test.Episode",
            )
            assert success is False
            assert command_id == -1

    def test_force_import_no_output_path_skips_fallback(self, client):
        """Test force import skips folder fallback when no output_path"""
        with requests_mock.Mocker() as m:
            m.get("http://test-sonarr:8989/api/v3/manualimport", json=[])

            success, command_id = client.force_import("download123")
            assert success is False
            assert command_id == -1

            # Only one GET request (no folder fallback)
            get_requests = [h for h in m.request_history if h.method == "GET"]
            assert len(get_requests) == 1

    def test_force_import_error(self, client):
        """Test force import error handling"""
        with requests_mock.Mocker() as m:
            m.get(
                "http://test-sonarr:8989/api/v3/manualimport",
                exc=requests.ConnectionError,
            )

            success, command_id = client.force_import("download123")
            assert success is False
            assert command_id is None

    def test_should_cleanup_queue_item_with_no_files_message(self, client):
        """Test should_cleanup_queue_item returns True for 'No files found' message"""
        queue_item = {
            "id": 123,
            "statusMessages": [
                {
                    "title": "test.mkv",
                    "messages": [
                        "No files found are eligible for import in /path/to/test.mkv"
                    ],
                }
            ],
        }

        result = client.should_cleanup_queue_item(queue_item)
        assert result is True

    def test_should_cleanup_queue_item_without_cleanup_message(self, client):
        """Test should_cleanup_queue_item returns False for normal messages"""
        queue_item = {
            "id": 123,
            "statusMessages": [
                {"title": "test.mkv", "messages": ["Waiting for download to complete"]}
            ],
        }

        result = client.should_cleanup_queue_item(queue_item)
        assert result is False

    def test_cleanup_post_import_queue_item_success(self, client):
        """Test successful cleanup of post-import queue item"""
        mock_queue = [
            {
                "id": 123,
                "downloadId": "test123",
                "statusMessages": [
                    {
                        "title": "test.mkv",
                        "messages": [
                            "No files found are eligible for import in /path/to/test.mkv"
                        ],
                    }
                ],
            }
        ]

        with requests_mock.Mocker() as m:
            # Mock queue retrieval
            m.get("http://test-sonarr:8989/api/v3/queue", json={"records": mock_queue})

            # Mock queue item removal
            m.delete("http://test-sonarr:8989/api/v3/queue/123", json={"success": True})

            result = client.cleanup_post_import_queue_item("test123")
            assert result is True

    def test_cleanup_post_import_queue_item_not_needed(self, client):
        """Test cleanup when no cleanup is needed"""
        mock_queue = [
            {
                "id": 123,
                "downloadId": "test123",
                "statusMessages": [
                    {
                        "title": "test.mkv",
                        "messages": ["Waiting for download to complete"],
                    }
                ],
            }
        ]

        with requests_mock.Mocker() as m:
            # Mock queue retrieval
            m.get("http://test-sonarr:8989/api/v3/queue", json={"records": mock_queue})

            result = client.cleanup_post_import_queue_item("test123")
            assert result is False


class TestCaching:
    """Test caching behavior"""

    @pytest.fixture
    def client(self, mock_config):
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "sonarr.url": mock_config["sonarr"]["url"],
            "sonarr.api_key": mock_config["sonarr"]["api_key"],
            "sonarr.timeout": 30,
        }.get(key, default)
        return SonarrClient(config)

    def test_clear_cache(self, client):
        """Test cache clearing functionality"""
        # Set some cache values
        client._custom_formats_cache = {"test": "data"}
        client._quality_profiles_cache = {"test": "data"}
        client._series_profile_map_cache = {"test": "data"}

        # Clear cache
        client.clear_cache()

        # Verify cache is cleared
        assert client._custom_formats_cache is None
        assert client._quality_profiles_cache is None
        assert client._series_profile_map_cache is None

    def test_cache_independence(self, client):
        """Test that different cache methods don't interfere"""
        with requests_mock.Mocker() as m:
            # Different endpoints
            m.get(
                "http://test-sonarr:8989/api/v3/customformat",
                json=[{"id": 1, "name": "HDR"}],
            )
            m.get(
                "http://test-sonarr:8989/api/v3/qualityprofile",
                json=[{"id": 1, "name": "HD-1080p"}],
            )

            # Fetch formats first
            formats = client.fetch_custom_formats()
            assert len(formats) == 1

            # Fetch profiles second
            profiles = client.fetch_quality_profiles()
            assert len(profiles) == 1

            # Verify both are cached independently
            assert client._custom_formats_cache is not None
            assert client._quality_profiles_cache is not None


@pytest.mark.api
class TestErrorHandling:
    """Test comprehensive error handling"""

    @pytest.fixture
    def client(self, mock_config):
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "sonarr.url": mock_config["sonarr"]["url"],
            "sonarr.api_key": mock_config["sonarr"]["api_key"],
            "sonarr.timeout": 30,
        }.get(key, default)
        return SonarrClient(config)

    def test_timeout_handling(self, client):
        """Test request timeout handling"""
        with requests_mock.Mocker() as m:
            m.get("http://test-sonarr:8989/api/v3/system/status", exc=requests.Timeout)

            with pytest.raises(SonarrAPIError):
                client._make_request("GET", "/system/status")

    def test_invalid_json_response(self, client):
        """Test handling of invalid JSON responses"""
        with requests_mock.Mocker() as m:
            m.get(
                "http://test-sonarr:8989/api/v3/test",
                text="Invalid JSON",
                status_code=200,
            )

            # Should not raise an error, just return response
            response = client._make_request("GET", "/test")
            assert response.status_code == 200

    def test_api_key_validation(self, client):
        """Test that API key is included in requests"""
        with requests_mock.Mocker() as m:
            m.get("http://test-sonarr:8989/api/v3/test", json={})

            client._make_request("GET", "/test")

            request = m.request_history[0]
            assert "X-Api-Key" in request.headers
            assert request.headers["X-Api-Key"] == client.api_key


class TestRadarrClient:
    """Test SonarrClient in Radarr mode"""

    @pytest.fixture
    def radarr_client(self, mock_radarr_config):
        """Create SonarrClient in Radarr mode"""
        config = MagicMock()
        config.get.side_effect = lambda key, default=None: {
            "app.type": "radarr",
            "radarr.url": mock_radarr_config["radarr"]["url"],
            "radarr.api_key": mock_radarr_config["radarr"]["api_key"],
            "radarr.timeout": 30,
        }.get(key, default)
        return SonarrClient(config, app_type="radarr")

    def test_radarr_init(self, radarr_client):
        """Test Radarr client initialization"""
        assert radarr_client.app_type == "radarr"
        assert radarr_client.base_url == "http://test-radarr:7878"

    def test_radarr_get_queue_params(self, radarr_client):
        """Test that Radarr queue uses movie-specific params"""
        with requests_mock.Mocker() as m:
            m.get(
                "http://test-radarr:7878/api/v3/queue",
                json={"records": []},
            )
            radarr_client.get_queue()

            request = m.request_history[0]
            assert "includeUnknownMovieItems" in request.url
            assert "includeMovie" in request.url
            assert "includeEpisode" not in request.url

    def test_radarr_build_profile_map(self, radarr_client):
        """Test that Radarr uses /movie endpoint for profile map"""
        mock_movies = [
            {"id": 1, "title": "Test Movie", "qualityProfileId": 5},
        ]
        with requests_mock.Mocker() as m:
            m.get("http://test-radarr:7878/api/v3/movie", json=mock_movies)
            result = radarr_client.build_series_profile_map()
            assert result[1] == 5

    def test_radarr_get_history_uses_movieid(self, radarr_client):
        """Test that Radarr history uses movieId param"""
        with requests_mock.Mocker() as m:
            m.get(
                "http://test-radarr:7878/api/v3/history",
                json={"records": []},
            )
            radarr_client.get_history_for_episode(50)

            request = m.request_history[0]
            assert "movieId=50" in request.url
            assert "episodeId" not in request.url

    def test_radarr_get_history_for_download_uses_downloadid(self, radarr_client):
        """Radarr download history uses the same global downloadId filter."""
        record = {"movieId": 50, "downloadId": "ABC123", "eventType": "grabbed"}
        with requests_mock.Mocker() as m:
            m.get(
                "http://test-radarr:7878/api/v3/history",
                json={"records": [record]},
            )

            assert radarr_client.get_history_for_download("ABC123") == [record]
            request = m.request_history[0]
            assert request.qs["downloadid"] == ["abc123"]
            assert "movieid" not in request.qs

    def test_radarr_manual_import_candidate_shape(self, radarr_client):
        """Public retrieval preserves Radarr movie candidate fields."""
        items = [
            {
                "path": "/downloads/Test.Movie.mkv",
                "movie": {"id": 50},
                "customFormatScore": 100,
            }
        ]
        with requests_mock.Mocker() as m:
            m.get("http://test-radarr:7878/api/v3/manualimport", json=items)

            assert radarr_client.get_manual_import_candidates("ABC123") == items

    def test_radarr_get_episode_file_uses_moviefile(self, radarr_client):
        """Test that Radarr uses /moviefile endpoint"""
        mock_file = {"id": 10, "customFormatScore": 50}
        with requests_mock.Mocker() as m:
            m.get("http://test-radarr:7878/api/v3/moviefile/10", json=mock_file)
            result = radarr_client.get_episode_file(10)
            assert result["id"] == 10

    def test_radarr_force_import_uses_movieid(self, radarr_client):
        """Test that Radarr force import extracts movieId"""
        mock_items = [
            {
                "path": "/downloads/test.mkv",
                "movie": {"id": 50, "title": "Test Movie"},
            }
        ]
        with requests_mock.Mocker() as m:
            m.get("http://test-radarr:7878/api/v3/manualimport", json=mock_items)
            m.post(
                "http://test-radarr:7878/api/v3/command",
                json={"name": "ManualImport", "id": 789},
                status_code=201,
            )
            success, command_id = radarr_client.force_import("download456")
            assert success is True
            assert command_id == 789

            # Verify movieId was set in the payload
            post_request = [h for h in m.request_history if h.method == "POST"][0]
            import json

            payload = json.loads(post_request.body)
            assert payload["files"][0]["movieId"] == 50
            assert "episodeIds" not in payload["files"][0]

    def test_radarr_force_import_candidate_uses_validated_movie(self, radarr_client):
        """Radarr submits only the already validated movie candidate."""
        candidate = {"path": "/downloads/correct.mkv", "movie": {"id": 50}}
        with requests_mock.Mocker() as m:
            m.post(
                "http://test-radarr:7878/api/v3/command",
                json={"id": 789},
                status_code=201,
            )

            success, _ = radarr_client.force_import_candidate(candidate)

            assert success is True
            import json

            payload = json.loads(m.request_history[-1].body)
            assert [item["path"] for item in payload["files"]] == [
                "/downloads/correct.mkv"
            ]
            assert payload["files"][0]["movieId"] == 50
            assert all(request.method != "GET" for request in m.request_history)

    def test_radarr_get_series_by_title_uses_movie(self, radarr_client):
        """Test that Radarr searches /movie endpoint"""
        mock_movies = [
            {"id": 1, "title": "The Matrix"},
            {"id": 2, "title": "Inception"},
        ]
        with requests_mock.Mocker() as m:
            m.get("http://test-radarr:7878/api/v3/movie", json=mock_movies)
            result = radarr_client.get_series_by_title("matrix")
            assert result is not None
            assert result["title"] == "The Matrix"

    def test_radarr_get_episode_info_uses_movie_id(self, radarr_client):
        """Test that Radarr fetches /movie/{id} directly"""
        mock_movie = {"id": 50, "title": "Test Movie", "hasFile": True}
        with requests_mock.Mocker() as m:
            m.get("http://test-radarr:7878/api/v3/movie/50", json=mock_movie)
            result = radarr_client.get_episode_info(50)
            assert result["id"] == 50
            assert result["title"] == "Test Movie"
