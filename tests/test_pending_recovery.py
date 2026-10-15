"""Exercise recovery at the HTTP, SQLite and durable journal boundaries."""

import json
import sqlite3
from types import SimpleNamespace

import pytest
import requests

from src.core.pending_recovery import PendingRecovery
from tests.test_release_payload import TITLE, torrent

ARR = "http://arr/sonarr/api/v3"
PROWLARR = "http://arr/prowlarr"
SAB = "http://arr:8080/sabnzbd/api"
QB = "http://arr:8081/api/v2"
NZB = (
    f'<nzb><file subject="{TITLE}.rar"><segments>'
    '<segment bytes="100" number="1">article@news.invalid</segment>'
    "</segments></file></nzb>"
).encode()


class Settings:
    def __init__(self, **values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


@pytest.fixture
def setup(tmp_path, requests_mock):
    db = tmp_path / "sonarr.db"
    with sqlite3.connect(db) as connection:
        connection.executescript("""
            CREATE TABLE PendingReleases (Id INTEGER, SeriesId INTEGER, Title TEXT,
              Added TEXT, Release TEXT, ParsedEpisodeInfo TEXT);
            CREATE TABLE Indexers (Id INTEGER, Implementation TEXT, Settings TEXT,
              Tags TEXT, EnableAutomaticSearch INTEGER, DownloadClientId INTEGER);
            CREATE TABLE DownloadClients (Id INTEGER, Enable INTEGER, Implementation TEXT,
              Settings TEXT, Tags TEXT, Priority INTEGER);
        """)
        connection.execute(
            "INSERT INTO PendingReleases VALUES (1,116,?,'2020-01-01T00:00:00Z',?,?)",
            (
                TITLE,
                json.dumps(
                    dict(
                        title=TITLE,
                        downloadUrl="http://dead/release?apikey=private",
                        downloadProtocol=1,
                        size=100,
                        guid="private-guid",
                    )
                ),
                json.dumps(dict(seasonNumber=4, episodeNumbers=[6])),
            ),
        )
        connection.execute(
            "INSERT INTO Indexers VALUES (48,'Newznab',?,'[4]',1,0)",
            (
                json.dumps(
                    dict(baseUrl="http://localhost/prowlarr/22/", apiKey="private")
                ),
            ),
        )
        connection.execute(
            "INSERT INTO DownloadClients VALUES (2,1,'Sabnzbd',?,'[]',1)",
            (
                json.dumps(
                    dict(
                        host="localhost",
                        port=8080,
                        urlBase="sabnzbd",
                        apiKey="private",
                        tvCategory="tv",
                    )
                ),
            ),
        )
    cfg = Settings(
        **{
            "pending_recovery.db_path": str(db),
            "pending_recovery.state_path": str(tmp_path / "state.json"),
            "pending_recovery.allow_outside_tags": True,
        }
    )
    client = SimpleNamespace(base_url="http://arr/sonarr", api_key="private")
    queue = [
        dict(
            id=55,
            seriesId=116,
            episodeId=10078,
            title=TITLE,
            status="delay",
            downloadId=None,
        )
    ]
    requests_mock.get(
        ARR + "/series/116",
        json=dict(id=116, title="Example Show", year=2018, tags=[3]),
    )
    requests_mock.get(
        ARR + "/episode/10078",
        json=dict(
            id=10078, seriesId=116, seasonNumber=4, episodeNumber=6, episodeFileId=9
        ),
    )
    requests_mock.get("http://dead/release", content=b"Invalid download link.")
    requests_mock.get(
        PROWLARR + "/api/v1/search",
        json=[
            dict(
                title=TITLE,
                protocol="usenet",
                indexerId=22,
                size=100,
                downloadUrl="http://fresh/file",
            )
        ],
    )
    requests_mock.get("http://fresh/file", content=NZB)
    requests_mock.get(SAB, json=dict(queue=dict(slots=[]), history=dict(slots=[])))
    requests_mock.post(SAB, json=dict(status=True, nzo_ids=["job-1"]))
    requests_mock.get(ARR + "/history", json=dict(records=[]))
    requests_mock.get(ARR + "/system/status", json=dict(version="5.0.0"))
    requests_mock.get("http://arr/sonarr/api/v5/queue", json=dict(records=queue))
    requests_mock.delete("http://arr/sonarr/api/v5/queue/55", status_code=204)
    requests_mock.post(ARR + "/command", json=dict(id=1))
    return SimpleNamespace(
        db=db,
        cfg=cfg,
        client=client,
        queue=queue,
        http=requests_mock,
        recovery=lambda: PendingRecovery(cfg, client),
    )


def uploads(s):
    return [
        r
        for r in s.http.request_history
        if r.method == "POST" and r.url.startswith(SAB)
    ]


def test_nzb_recovery_survives_restart_and_waits_for_actual_import(setup):
    s = setup
    s.recovery().run(s.queue)
    assert len(uploads(s)) == 1
    assert b'name="cat"\r\n\r\ntv' in uploads(s)[0].body
    assert b'name="nzbfile"' in uploads(s)[0].body
    s.recovery().run(s.queue)
    assert len(uploads(s)) == 1
    assert not any(r.method == "DELETE" for r in s.http.request_history)
    s.http.get(ARR + "/episode/10078", json=dict(episodeFileId=10))
    s.http.get(
        ARR + "/history",
        json=dict(
            records=[
                dict(
                    eventType="downloadFolderImported",
                    episodeId=10078,
                    downloadId="job-1",
                    data=dict(fileId="10"),
                )
            ]
        ),
    )
    s.recovery().run(s.queue)
    deletes = [r for r in s.http.request_history if r.method == "DELETE"]
    assert len(deletes) == 1
    assert deletes[0].qs["removefromclient"] == ["false"]
    assert deletes[0].qs["blocklist"] == ["false"]
    assert len([r for r in s.http.request_history if r.url.endswith("/command")]) == 1
    s.recovery().run(s.queue)
    assert len(uploads(s)) == 1
    state = open(s.cfg.get("pending_recovery.state_path")).read()
    assert "private" not in state and "http://" not in state


@pytest.mark.parametrize(
    "response",
    [
        dict(status_code=429),
        dict(status_code=403),
        dict(status_code=503),
        dict(exc=requests.Timeout),
        dict(content=NZB),
    ],
)
def test_healthy_or_transient_link_is_not_replaced(setup, response):
    s = setup
    s.http.get("http://dead/release", **response)
    s.recovery().run(s.queue)
    assert not uploads(s)
    assert not any("/search" in r.url for r in s.http.request_history)


def test_dry_run_never_submits_or_changes_journal(setup):
    s = setup
    s.recovery().run(s.queue, dry_run=True)
    assert not uploads(s)
    assert (
        not __import__("pathlib")
        .Path(s.cfg.get("pending_recovery.state_path"))
        .exists()
    )


@pytest.mark.parametrize(
    "change",
    [
        dict(title=TITLE.replace("REPACK", "REPACK2")),
        dict(size=200),
        dict(protocol="torrent"),
        dict(indexerId=999),
    ],
)
def test_fallback_must_match_identity_size_protocol_and_configured_provider(
    setup, change
):
    s = setup
    candidate = dict(
        title=TITLE,
        protocol="usenet",
        indexerId=22,
        size=100,
        downloadUrl="http://fresh/file",
    )
    candidate.update(change)
    s.http.get(PROWLARR + "/api/v1/search", json=[candidate])
    s.recovery().run(s.queue)
    assert not uploads(s)


def test_outside_tags_requires_explicit_enable(setup):
    s = setup
    s.cfg.values["pending_recovery.allow_outside_tags"] = False
    s.recovery().run(s.queue)
    assert not uploads(s)


def test_uncertain_upload_is_not_retried_even_after_restart(setup):
    s = setup
    s.http.post(SAB, exc=requests.Timeout)
    s.recovery().run(s.queue)
    s.recovery().run(s.queue)
    assert len(uploads(s)) == 1


def test_existing_sab_job_is_adopted_without_upload(setup):
    s = setup
    s.http.get(
        SAB, json=dict(queue=dict(slots=[dict(filename=TITLE, nzo_id="existing")]))
    )
    s.recovery().run(s.queue)
    assert not uploads(s)


def test_unrelated_import_cannot_clear_pending_record(setup):
    s = setup
    s.recovery().run(s.queue)
    s.http.get(ARR + "/episode/10078", json=dict(episodeFileId=10))
    s.http.get(
        ARR + "/history",
        json=dict(
            records=[
                dict(
                    eventType="downloadFolderImported",
                    episodeId=10078,
                    downloadId="different",
                    data=dict(fileId="10"),
                )
            ]
        ),
    )
    s.recovery().run(s.queue)
    assert not any(r.method == "DELETE" for r in s.http.request_history)


def test_cooldown_prevents_repeated_provider_search(setup):
    s = setup
    s.http.get(PROWLARR + "/api/v1/search", json=[])
    s.recovery().run(s.queue)
    s.recovery().run(s.queue)
    assert len([r for r in s.http.request_history if "/search" in r.url]) == 1


def test_qbittorrent_upload_preserves_category_and_checks_hash(setup):
    s = setup
    body, _ = torrent()
    from src.core.release_payload import validate_payload

    infohash = validate_payload(body, "torrent", TITLE).infohash
    with sqlite3.connect(s.db) as c:
        c.execute(
            "UPDATE PendingReleases SET Release=?",
            (
                json.dumps(
                    dict(
                        title=TITLE,
                        downloadUrl="http://dead/file",
                        downloadProtocol=2,
                        size=100,
                    )
                ),
            ),
        )
        c.execute("UPDATE Indexers SET Implementation='Torznab'")
        c.execute(
            "UPDATE DownloadClients SET Implementation='QBittorrent', Settings=?",
            (
                json.dumps(
                    dict(
                        host="localhost",
                        port=8081,
                        username="user",
                        password="private",
                        tvCategory="tv-sonarr",
                    )
                ),
            ),
        )
    s.http.get("http://dead/file", status_code=410)
    s.http.get(
        PROWLARR + "/api/v1/search",
        json=[
            dict(
                title=TITLE,
                protocol="torrent",
                indexerId=22,
                size=100,
                downloadUrl="http://fresh/file",
            )
        ],
    )
    s.http.get("http://fresh/file", content=body)
    s.http.post(QB + "/auth/login", text="Ok.")
    s.http.get(
        QB + "/torrents/info",
        [
            dict(json=[]),
            dict(json=[dict(hash=infohash, name=TITLE, category="tv-sonarr")]),
        ],
    )
    s.http.post(QB + "/torrents/add", text="Ok.")
    s.recovery().run(s.queue)
    adds = [r for r in s.http.request_history if r.path.endswith("/torrents/add")]
    assert len(adds) == 1
    assert b'name="category"\r\n\r\ntv-sonarr' in adds[0].body
    assert b'name="torrents"' in adds[0].body
    s.recovery().run(s.queue)
    assert (
        len([r for r in s.http.request_history if r.path.endswith("/torrents/add")])
        == 1
    )
    assert not any(r.method == "DELETE" for r in s.http.request_history)


def test_polling_reconciles_recovery_even_with_empty_native_queue(setup):
    from unittest.mock import patch

    from src.core.monitor import SonarrImportMonitor

    s = setup
    s.cfg.values["pending_recovery.enabled"] = True
    with patch("src.core.monitor.SonarrClient", return_value=s.client), patch(
        "src.core.monitor.ScoreAnalyzer"
    ), patch("src.core.monitor.signal.signal"):
        monitor = SonarrImportMonitor(s.cfg)
    monitor._get_queue_smart = lambda: s.queue
    monitor.process_stuck_imports()
    assert len(uploads(s)) == 1
    monitor._get_queue_smart = lambda: []
    s.http.get(ARR + "/episode/10078", json=dict(episodeFileId=10))
    s.http.get(
        ARR + "/history",
        json=dict(
            records=[
                dict(
                    eventType="downloadFolderImported",
                    episodeId=10078,
                    downloadId="job-1",
                    data=dict(fileId="10"),
                )
            ]
        ),
    )
    monitor.process_stuck_imports()
    assert any(r.method == "DELETE" for r in s.http.request_history)


def test_radarr_never_initializes_pending_recovery(setup):
    from unittest.mock import patch

    from src.core.monitor import SonarrImportMonitor

    s = setup
    s.cfg.values.update({"pending_recovery.enabled": True, "app.type": "radarr"})
    with patch("src.core.monitor.SonarrClient", return_value=s.client), patch(
        "src.core.monitor.ScoreAnalyzer"
    ), patch("src.core.monitor.signal.signal"):
        monitor = SonarrImportMonitor(s.cfg)
    monitor._get_queue_smart = lambda: []
    monitor.process_stuck_imports()
    assert not s.http.request_history


def test_nzb_segment_size_includes_parity_overhead(setup):
    s = setup
    s.http.get("http://fresh/file", content=NZB.replace(b'bytes="100"', b'bytes="120"'))
    s.recovery().run(s.queue)
    assert len(uploads(s)) == 1


def test_unavailable_candidate_does_not_block_next_working_provider(setup):
    s = setup
    candidate = dict(title=TITLE, protocol="usenet", indexerId=22, size=100)
    s.http.get(
        PROWLARR + "/api/v1/search",
        json=[
            dict(candidate, downloadUrl="http://temporary/file"),
            dict(candidate, downloadUrl="http://fresh/file"),
        ],
    )
    s.http.get("http://temporary/file", status_code=429)
    s.recovery().run(s.queue)
    assert len(uploads(s)) == 1


def test_client_selection_keeps_tags_priority_and_indexer_pin():
    from src.api.recovery_clients import RecoveryIO

    rows = [
        dict(Id=1, Enable=1, Implementation="QBittorrent", Tags=[], Priority=1),
        dict(Id=3, Enable=1, Implementation="QBittorrent", Tags=[6], Priority=50),
        dict(Id=4, Enable=0, Implementation="QBittorrent", Tags=[6], Priority=1),
    ]
    assert [r["Id"] for r in RecoveryIO.clients(rows, "torrent", [6])] == [3]
    assert RecoveryIO.clients(rows, "torrent", [6], pinned=1) == []
    assert [r["Id"] for r in RecoveryIO.clients(rows, "torrent", [5], pinned=1)] == [1]


@pytest.fixture
def torrent_setup(setup):
    from src.core.release_payload import validate_payload

    s = setup
    body, _ = torrent()
    s.infohash = validate_payload(body, "torrent", TITLE).infohash
    with sqlite3.connect(s.db) as c:
        c.execute(
            "UPDATE PendingReleases SET Release=?",
            (
                json.dumps(
                    dict(
                        title=TITLE,
                        downloadUrl="http://dead/file",
                        downloadProtocol=2,
                        size=100,
                    )
                ),
            ),
        )
        c.execute("UPDATE Indexers SET Implementation='Torznab'")
        c.execute(
            "UPDATE DownloadClients SET Implementation='QBittorrent', Settings=?",
            (
                json.dumps(
                    dict(
                        host="localhost",
                        port=8081,
                        username="user",
                        password="private",
                        tvCategory="tv-sonarr",
                    )
                ),
            ),
        )
    s.http.get("http://dead/file", status_code=404)
    s.http.get(
        PROWLARR + "/api/v1/search",
        json=[
            dict(
                title=TITLE,
                protocol="torrent",
                indexerId=22,
                size=100,
                downloadUrl="http://fresh/file",
            )
        ],
    )
    s.http.get("http://fresh/file", content=body)
    s.http.post(QB + "/auth/login", text="Ok.")
    return s


def test_existing_torrent_is_never_added_or_modified(torrent_setup):
    s = torrent_setup
    s.http.get(
        QB + "/torrents/info", json=[dict(hash=s.infohash, category="tv-sonarr")]
    )
    s.recovery().run(s.queue)
    s.recovery().run(s.queue)
    assert not any(r.path.endswith("/torrents/add") for r in s.http.request_history)
    assert not any(
        r.method == "POST" and "/torrents/" in r.path for r in s.http.request_history
    )


def test_torrent_in_another_category_is_preserved_and_blocks_duplicate(torrent_setup):
    s = torrent_setup
    s.http.get(
        QB + "/torrents/info", json=[dict(hash=s.infohash, category="seed-only")]
    )
    s.recovery().run(s.queue)
    assert not any(r.path.endswith("/torrents/add") for r in s.http.request_history)


def test_torrent_lost_response_is_reconciled_by_hash_without_second_add(torrent_setup):
    s = torrent_setup
    s.http.get(
        QB + "/torrents/info",
        [dict(json=[]), dict(json=[dict(hash=s.infohash, category="tv-sonarr")])],
    )
    s.http.post(QB + "/torrents/add", exc=requests.Timeout)
    s.recovery().run(s.queue)
    s.recovery().run(s.queue)
    assert (
        len([r for r in s.http.request_history if r.path.endswith("/torrents/add")])
        == 1
    )
    assert (
        next(
            iter(
                json.loads(open(s.cfg.get("pending_recovery.state_path")).read())[
                    "items"
                ].values()
            )
        )["phase"]
        == "submitted"
    )


def test_torrent_success_response_without_hash_does_not_trigger_repeat_upload(
    torrent_setup,
):
    s = torrent_setup
    s.http.get(QB + "/torrents/info", json=[])
    s.http.post(QB + "/torrents/add", text="Ok.")
    s.recovery().run(s.queue)
    s.recovery().run(s.queue)
    assert (
        len([r for r in s.http.request_history if r.path.endswith("/torrents/add")])
        == 1
    )


@pytest.mark.parametrize(
    "mutation", ["young", "already_downloading", "multi_episode", "disabled_indexer"]
)
def test_ineligible_pending_release_is_not_uploaded(setup, mutation):
    s = setup
    with sqlite3.connect(s.db) as c:
        if mutation == "young":
            from datetime import datetime, timezone

            c.execute(
                "UPDATE PendingReleases SET Added=?",
                (datetime.now(timezone.utc).isoformat(),),
            )
        elif mutation == "already_downloading":
            s.queue[0]["downloadId"] = "native-job"
        elif mutation == "multi_episode":
            c.execute(
                "UPDATE PendingReleases SET ParsedEpisodeInfo=?",
                (json.dumps(dict(episodeNumbers=[6, 7])),),
            )
        else:
            c.execute("UPDATE Indexers SET EnableAutomaticSearch=0")
    s.recovery().run(s.queue)
    assert not uploads(s)


def test_corrupt_journal_fails_closed_and_leaves_database_unchanged(setup):
    from pathlib import Path

    s = setup
    before = s.db.read_bytes()
    state = Path(s.cfg.get("pending_recovery.state_path"))
    state.write_text("broken journal")
    s.recovery().run(s.queue)
    assert state.read_text() == "broken journal"
    assert s.db.read_bytes() == before
    assert not s.http.request_history


def test_uncertain_sab_upload_recovers_existing_job_on_restart(setup):
    s = setup
    s.http.post(SAB, exc=requests.Timeout)
    s.recovery().run(s.queue)
    s.http.get(
        SAB,
        json=dict(
            queue=dict(slots=[dict(filename=TITLE, nzo_id="accepted-before-timeout")])
        ),
    )
    s.recovery().run(s.queue)
    state = json.loads(open(s.cfg.get("pending_recovery.state_path")).read())
    assert (
        next(iter(state["items"].values()))["download_id"] == "accepted-before-timeout"
    )
    assert len(uploads(s)) == 1


def test_torrent_submission_keeps_indexer_seed_ratio_and_accepts_empty_ack(
    torrent_setup,
):
    s = torrent_setup
    with sqlite3.connect(s.db) as c:
        settings = json.loads(c.execute("SELECT Settings FROM Indexers").fetchone()[0])
        settings["seedCriteria"] = dict(seedRatio=10, seedTime=1440)
        c.execute("UPDATE Indexers SET Settings=?", (json.dumps(settings),))
    s.http.get(
        QB + "/torrents/info",
        [dict(json=[]), dict(json=[dict(hash=s.infohash, category="tv-sonarr")])],
    )
    s.http.post(QB + "/torrents/add", text="")
    s.recovery().run(s.queue)
    add = next(r for r in s.http.request_history if r.path.endswith("/torrents/add"))
    assert b'name="ratioLimit"\r\n\r\n10' in add.body
    assert b'name="seedingTimeLimit"\r\n\r\n1440' in add.body
    assert b'name="contentLayout"\r\n\r\nOriginal' in add.body
    state = json.loads(open(s.cfg.get("pending_recovery.state_path")).read())
    assert next(iter(state["items"].values()))["phase"] == "submitted"


def test_cleanup_batch_requests_one_rss_refresh(setup):
    from pathlib import Path

    s = setup
    records = {}
    queue, history = [], []
    for index in (0, 1):
        episode = 10078 + index
        records[str(index)] = dict(
            phase="submitted",
            episode_id=episode,
            series_id=116,
            title=TITLE,
            download_id=f"job-{index}",
            old_file_id=9,
        )
        queue.append(
            dict(
                id=55 + index,
                episodeId=episode,
                seriesId=116,
                title=TITLE,
                downloadId=None,
            )
        )
        history.append(
            dict(
                eventType="downloadFolderImported",
                episodeId=episode,
                downloadId=f"job-{index}",
                data=dict(fileId="10"),
            )
        )
        s.http.get(ARR + f"/episode/{episode}", json=dict(episodeFileId=10))
        s.http.delete(f"http://arr/sonarr/api/v5/queue/{55 + index}", status_code=204)
    Path(s.cfg.get("pending_recovery.state_path")).write_text(
        json.dumps(dict(version=1, items=records))
    )
    s.http.get(ARR + "/history", json=dict(records=history))
    s.http.get("http://arr/sonarr/api/v5/queue", json=dict(records=queue))
    s.recovery().run([])
    assert len([r for r in s.http.request_history if r.method == "DELETE"]) == 2
    assert len([r for r in s.http.request_history if r.path.endswith("/command")]) == 1


def test_process_crash_after_upload_reconciles_persisted_intent(setup):
    s = setup
    s.http.post(SAB, exc=SystemExit)
    with pytest.raises(SystemExit):
        s.recovery().run(s.queue)
    state = json.loads(open(s.cfg.get("pending_recovery.state_path")).read())
    assert next(iter(state["items"].values()))["phase"] == "submitting"
    s.http.get(
        SAB, json=dict(queue=dict(slots=[dict(filename=TITLE, nzo_id="accepted-job")]))
    )
    s.recovery().run(s.queue)
    assert len(uploads(s)) == 1


def test_two_monitor_processes_share_journal_lock(setup):
    from concurrent.futures import ThreadPoolExecutor

    s = setup
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(s.recovery().run, s.queue) for _ in range(2)]
        for result in results:
            result.result(timeout=10)
    assert len(uploads(s)) == 1
