"""Recover broken pending links, with durable at-most-once upload attempts."""

import fcntl
import hashlib
import json
import logging
import os
import threading
import time
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path

import requests

from ..api.recovery_clients import DeferredRecovery, RecoveryIO, snapshot
from .release_payload import release_identity

logger = logging.getLogger(__name__)


class PendingRecovery:
    def __init__(self, config, client):
        self.io = RecoveryIO(client)
        self.db_path = config.get("pending_recovery.db_path", "/sonarr-state/sonarr.db")
        self.state_path = Path(
            config.get(
                "pending_recovery.state_path", "/app/state/pending-recovery.json"
            )
        )
        self.outside_tags = config.get("pending_recovery.allow_outside_tags", False)
        self.lock = threading.Lock()
        self.items = {}

    def _save(self):
        if self.dry_run:
            return
        temporary = self.state_path.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump({"version": 1, "items": self.items}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.state_path)
        directory = os.open(self.state_path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def run(self, queue, dry_run=False):
        """Failure here does not interrupt normal import processing. Never log URLs."""
        with self.lock:
            self.dry_run = dry_run
            try:
                self._run_locked(queue)
            except Exception as error:
                logger.error(
                    "Pending recovery unavailable (%s); existing imports continue",
                    type(error).__name__,
                )

    def _run_locked(self, queue):
        if not self.dry_run:
            self.state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = (
            nullcontext()
            if self.dry_run
            else open(self.state_path.with_suffix(".lock"), "a")
        )
        with lock as stream:
            if stream:
                fcntl.flock(stream, fcntl.LOCK_EX)
            state = (
                json.loads(self.state_path.read_text())
                if self.state_path.exists()
                else {"version": 1, "items": {}}
            )
            if state["version"] != 1 or not isinstance(state["items"], dict):
                raise DeferredRecovery("Unsupported recovery journal")
            self.items = state["items"]
            tables = snapshot(self.db_path)
            self.refreshing = []
            for record in list(self.items.values()):
                if record["phase"] in (
                    "submitting",
                    "ambiguous",
                    "submitted",
                    "cleaning",
                ):
                    self._reconcile_safely(record, tables)
            if self.refreshing:
                self.io.arr("POST", "command", json={"name": "RssSync"})
                for record in self.refreshing:
                    record["phase"] = "complete"
                    logger.info(
                        "Pending replacement import confirmed for episode %s",
                        record["episode_id"],
                    )
                self._save()
            for row in tables["PendingReleases"]:
                self._pending(row, queue, tables)

    def _pending(self, row, queue, tables):
        key = hashlib.sha256(
            f'{row["Id"]}:{row["SeriesId"]}:{row["Title"]}'.encode()
        ).hexdigest()
        record = self.items.get(key)
        if record and (
            record["phase"] != "backoff" or record["next_attempt"] > time.time()
        ):
            return
        matching = [
            q
            for q in queue
            if q.get("seriesId") == row["SeriesId"]
            and q.get("title") == row["Title"]
            and not q.get("downloadId")
            and q.get("episodeId")
            and q.get("status") in ("delay", "downloadClientUnavailable")
        ]
        if len(matching) != 1:
            return
        item = matching[0]
        episode_id = item["episodeId"]
        if any(q.get("episodeId") == episode_id and q.get("downloadId") for q in queue):
            return
        if any(
            r.get("episode_id") == episode_id
            and r["phase"] not in ("backoff", "complete")
            for r in self.items.values()
        ):
            return
        added = datetime.fromisoformat(row["Added"].replace("Z", "+00:00"))
        if (
            datetime.now(timezone.utc)
            - added.replace(tzinfo=added.tzinfo or timezone.utc)
        ).total_seconds() < 1800:
            return
        record = {
            "phase": "backoff",
            "next_attempt": time.time() + 1800,
            "pending_id": row["Id"],
            "episode_id": episode_id,
            "series_id": row["SeriesId"],
            "title": row["Title"],
        }
        self.items[key] = record
        self._save()
        try:
            self._recover(row, record, tables)
        except Exception as error:
            # A failed POST or process interruption after persisted intent is ambiguous.
            if record["phase"] == "submitting":
                record["phase"] = "ambiguous"
            self._save()
            logger.warning(
                "Pending recovery deferred for episode %s (%s), phase=%s",
                episode_id,
                type(error).__name__,
                record["phase"],
            )

    def _recover(self, row, record, tables):
        release, parsed = row["Release"], row["ParsedEpisodeInfo"]
        numbers = parsed.get("episodeNumbers", [])
        if len(numbers) != 1:
            return
        protocol = {
            1: "usenet",
            2: "torrent",
            "usenet": "usenet",
            "torrent": "torrent",
        }.get(release.get("downloadProtocol"))
        if not protocol:
            return
        series = self.io.arr("GET", f'series/{row["SeriesId"]}')
        episode = self.io.arr("GET", f'episode/{record["episode_id"]}')
        if (
            episode.get("seriesId"),
            episode.get("seasonNumber"),
            episode.get("episodeNumber"),
        ) != (row["SeriesId"], parsed.get("seasonNumber"), numbers[0]):
            return
        year, title, tags = series.get("year"), row["Title"], series.get("tags", [])
        self.io.trust(tables["Indexers"])
        if self.io.payload(release["downloadUrl"], protocol, title, year) is not None:
            return
        logger.info(
            "Broken %s pending link detected for episode %s",
            protocol,
            record["episode_id"],
        )
        providers = self.io.providers(
            tables["Indexers"], protocol, tags, self.outside_tags
        )
        for number, (candidate, indexer) in enumerate(
            self.io.candidates(providers, title)
        ):
            if number >= 20:
                break
            if not self._matches(candidate, release, title, protocol, year):
                continue
            payload = self._candidate_payload(candidate, protocol, title, year)
            if payload is None:
                continue
            clients = self.io.clients(
                tables["DownloadClients"],
                protocol,
                tags,
                indexer.get("DownloadClientId", 0),
            )
            if not clients:
                continue
            record.update(
                year=year,
                protocol=protocol,
                old_file_id=episode.get("episodeFileId", 0),
                infohash=payload.infohash,
                client_id=clients[0]["Id"],
                seed_criteria=indexer["Settings"].get("seedCriteria", {}),
            )
            self._submit(record, payload, tables)
            return

    def _candidate_payload(self, candidate, protocol, title, year):
        try:
            payload = self.io.payload(
                candidate.get("downloadUrl", ""), protocol, title, year
            )
        except (requests.RequestException, DeferredRecovery):
            return None
        # NZB segment bytes include parity/repair overhead; compare reported release
        # sizes separately. Torrent file lengths represent the actual release size.
        if (
            payload
            and protocol == "torrent"
            and not self._size_matches(payload.size, candidate.get("size"))
        ):
            return None
        return payload

    @staticmethod
    def _size_matches(size, expected):
        return (
            isinstance(size, (int, float))
            and isinstance(expected, (int, float))
            and expected > 0
            and abs(size - expected) / expected <= 0.02
        )

    def _matches(self, candidate, release, title, protocol, year):
        return (
            candidate.get("protocol") == protocol
            and release_identity(candidate.get("title", ""), year)
            == release_identity(title, year)
            and self._size_matches(candidate.get("size"), release.get("size"))
        )

    def _submit(self, record, payload, tables):
        if self.dry_run:
            logger.info(
                "Dry run: verified replacement available for episode %s",
                record["episode_id"],
            )
            return
        # Check every configured client of this protocol, including alternate routes.
        implementation = "Sabnzbd" if record["protocol"] == "usenet" else "QBittorrent"
        for row in tables["DownloadClients"]:
            if not row["Enable"] or row["Implementation"] != implementation:
                continue
            downloader = self.io.downloader(row)
            try:
                existing = downloader.find(
                    record["title"], record["infohash"], record["year"]
                )
            finally:
                downloader.close()
            if existing:
                record.update(
                    client_id=row["Id"], download_id=existing, phase="submitted"
                )
                self._save()
                return
        row = next(
            r for r in tables["DownloadClients"] if r["Id"] == record["client_id"]
        )
        downloader = self.io.downloader(row)
        try:
            record["phase"] = "submitting"
            self._save()  # Never issue a non-idempotent upload before durable intent.
            download_id = downloader.add(
                payload, record["title"], record.get("seed_criteria", {})
            )
            if not download_id:
                raise DeferredRecovery("Download acceptance could not be verified")
            record.update(download_id=download_id, phase="submitted")
            self._save()
            logger.info(
                "Pending replacement submitted for episode %s via client %s",
                record["episode_id"],
                row["Id"],
            )
        finally:
            downloader.close()

    def _reconcile_safely(self, record, tables):
        if self.dry_run:
            return
        try:
            self._reconcile(record, tables)
        except Exception as error:
            logger.warning(
                "Pending recovery reconciliation deferred for episode %s (%s)",
                record["episode_id"],
                type(error).__name__,
            )

    def _reconcile(self, record, tables):
        if record["phase"] in ("submitting", "ambiguous"):
            row = next(
                r for r in tables["DownloadClients"] if r["Id"] == record["client_id"]
            )
            downloader = self.io.downloader(row)
            try:
                download_id = downloader.find(
                    record["title"], record["infohash"], record["year"]
                )
            finally:
                downloader.close()
            if not download_id:
                if record["phase"] != "ambiguous":
                    record["phase"] = "ambiguous"
                    self._save()
                return  # Manual attention rather than risking a second upload.
            record.update(download_id=download_id, phase="submitted")
            self._save()
        if record["phase"] == "submitted":
            episode = self.io.arr("GET", f'episode/{record["episode_id"]}')
            history = self.io.arr(
                "GET",
                "history",
                params={"downloadId": record["download_id"], "pageSize": 1000},
            )["records"]
            if not self._imported(record, episode, history):
                return
            record["phase"] = "cleaning"
            self._save()
        self._cleanup(record)

    @staticmethod
    def _imported(record, episode, history):
        file_id = episode.get("episodeFileId")
        if not file_id or file_id == record["old_file_id"]:
            return False
        return any(
            h.get("eventType") == "downloadFolderImported"
            and h.get("episodeId") == record["episode_id"]
            and str(h.get("downloadId", "")).lower() == record["download_id"].lower()
            and str(h.get("data", {}).get("fileId")) == str(file_id)
            for h in history
        )

    def _cleanup(self, record):
        status = self.io.arr("GET", "system/status")
        version = 5 if int(status["version"].split(".")[0]) >= 5 else 3
        queue = self.io.arr(
            "GET",
            "queue",
            version=version,
            params={"pageSize": 1000, "includeUnknownSeriesItems": "true"},
        )["records"]
        for item in queue:
            if (
                item.get("seriesId") == record["series_id"]
                and item.get("episodeId") == record["episode_id"]
                and item.get("title") == record["title"]
                and not item.get("downloadId")
            ):
                self.io.arr(
                    "DELETE",
                    f'queue/{item["id"]}',
                    version=version,
                    params={
                        "removeFromClient": "false",
                        "blocklist": "false",
                        "skipRedownload": "true",
                    },
                )
        # Sonarr v5 removes the DB row but retains its cached queue until RSS completes.
        self.refreshing.append(record)
