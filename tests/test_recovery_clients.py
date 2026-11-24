"""Security tests for RecoveryIO.payload: SSRF allow-list and bounded downloads."""

from unittest.mock import MagicMock, patch

import pytest

from src.api import recovery_clients
from src.api.recovery_clients import DeferredRecovery, RecoveryIO

INDEXERS = [{"Settings": {"baseUrl": "http://prowlarr.local:9696/3/"}}]
GOOD_URL = "http://prowlarr.local:9696/3/download?file=x"


class FakeResponse:
    def __init__(self, status=200, chunks=(b"d",), headers=None):
        self.status_code = status
        self.headers = headers or {}
        self.raw = MagicMock()
        self.raw.stream.side_effect = lambda *a, **k: iter(chunks)
        self.closed = False

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


@pytest.fixture
def io():
    instance = RecoveryIO(
        MagicMock(base_url="http://localhost:8989/sonarr", api_key="k")
    )
    instance.trust(INDEXERS)
    return instance


def test_fetches_from_configured_indexer_host(io):
    with patch.object(recovery_clients.requests, "get") as get, patch.object(
        recovery_clients, "validate_payload", return_value="ok"
    ):
        get.return_value = FakeResponse()
        assert io.payload(GOOD_URL, "torrent", "t", 2020) == "ok"
    kwargs = get.call_args.kwargs
    assert kwargs["allow_redirects"] is False
    assert kwargs["headers"]["Accept-Encoding"] == "identity"


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:6379/",
        "http://prowlarr.local:1234/3/download",
        "http://evil.example/x",
    ],
)
def test_rejects_hosts_that_are_not_configured_indexers(io, url):
    with patch.object(recovery_clients.requests, "get") as get:
        with pytest.raises(DeferredRecovery):
            io.payload(url, "torrent", "t", 2020)
    get.assert_not_called()


def test_rejects_everything_until_indexers_are_trusted():
    untrusted = RecoveryIO(MagicMock(base_url="http://localhost:8989", api_key="k"))
    with pytest.raises(DeferredRecovery):
        untrusted.payload(GOOD_URL, "torrent", "t", 2020)


def test_follows_redirect_only_to_trusted_hosts(io):
    redirect = FakeResponse(302, headers={"Location": "http://169.254.169.254/x"})
    with patch.object(recovery_clients.requests, "get", return_value=redirect) as get:
        with pytest.raises(DeferredRecovery):
            io.payload(GOOD_URL, "torrent", "t", 2020)
    assert get.call_count == 1
    assert redirect.closed


def test_follows_trusted_relative_redirect(io):
    hops = [
        FakeResponse(302, headers={"Location": "/3/download?file=y"}),
        FakeResponse(),
    ]
    with patch.object(recovery_clients.requests, "get", side_effect=hops), patch.object(
        recovery_clients, "validate_payload", return_value="ok"
    ):
        assert io.payload(GOOD_URL, "torrent", "t", 2020) == "ok"


def test_redirect_loop_is_capped(io):
    loop = lambda *a, **k: FakeResponse(302, headers={"Location": GOOD_URL})
    with patch.object(recovery_clients.requests, "get", side_effect=loop) as get:
        with pytest.raises(DeferredRecovery):
            io.payload(GOOD_URL, "torrent", "t", 2020)
    assert get.call_count <= recovery_clients.MAX_REDIRECTS + 1


def test_missing_release_is_reported_broken(io):
    with patch.object(recovery_clients.requests, "get", return_value=FakeResponse(404)):
        assert io.payload(GOOD_URL, "torrent", "t", 2020) is None


def test_compressed_response_is_deferred_not_treated_as_broken(io):
    response = FakeResponse(headers={"Content-Encoding": "gzip"})
    with patch.object(recovery_clients.requests, "get", return_value=response):
        with pytest.raises(DeferredRecovery):
            io.payload(GOOD_URL, "torrent", "t", 2020)


def test_oversized_body_is_deferred(io):
    big = FakeResponse(chunks=[b"x" * (recovery_clients.MAX_PAYLOAD + 1)])
    with patch.object(recovery_clients.requests, "get", return_value=big):
        with pytest.raises(DeferredRecovery):
            io.payload(GOOD_URL, "torrent", "t", 2020)


def test_slow_download_hits_total_deadline(io):
    ticks = iter([0, 0, 1000, 1000, 1000])
    with patch.object(
        recovery_clients.requests, "get", return_value=FakeResponse(chunks=[b"a", b"b"])
    ), patch.object(
        recovery_clients.time, "monotonic", side_effect=lambda: next(ticks)
    ):
        with pytest.raises(DeferredRecovery):
            io.payload(GOOD_URL, "torrent", "t", 2020)
