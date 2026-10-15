"""Recovery IO. Sonarr state is read-only; downloads retain configured routing."""

import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import requests

from ..core.release_payload import (
    MAX_PAYLOAD,
    InvalidRelease,
    UnverifiableRelease,
    release_identity,
    validate_payload,
)


class DeferredRecovery(RuntimeError):
    """An inconclusive response must never be treated as a dead link."""


def snapshot(path):
    with sqlite3.connect(
        Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5
    ) as connection:
        connection.row_factory = sqlite3.Row
        result = {}
        for table in ("PendingReleases", "Indexers", "DownloadClients"):
            rows = [dict(row) for row in connection.execute("SELECT * FROM " + table)]
            for row in rows:
                for key in ("Settings", "Tags", "Release", "ParsedEpisodeInfo"):
                    if key in row:
                        row[key] = json.loads(row[key]) if row[key] else {}
            result[table] = rows
    return result


class RecoveryIO:
    def __init__(self, client):
        self.arr_url, self.arr_key = client.base_url, client.api_key
        self.host = urlsplit(client.base_url).hostname

    def reachable(self, url):
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise DeferredRecovery("Unsupported link scheme")
        if parts.hostname in ("localhost", "127.0.0.1", "::1"):
            host = f"[{self.host}]" if ":" in self.host else self.host
            parts = parts._replace(
                netloc=host + (f":{parts.port}" if parts.port else "")
            )
        return urlunsplit(parts)

    def arr(self, method, endpoint, version=3, **kwargs):
        response = requests.request(
            method,
            f"{self.arr_url}/api/v{version}/{endpoint}",
            headers={"X-Api-Key": self.arr_key},
            timeout=30,
            **kwargs,
        )
        response.raise_for_status()
        return response.json() if response.content else None

    def payload(self, url, protocol, title, year):
        """None means definitively broken. Auth/rate/network errors defer recovery."""
        with requests.get(
            self.reachable(url), timeout=(10, 30), stream=True
        ) as response:
            if response.status_code in (404, 410):
                return None
            if response.status_code != 200:
                raise DeferredRecovery("Download endpoint temporarily unavailable")
            body = bytearray()
            for chunk in response.iter_content(65536):
                body.extend(chunk)
                if len(body) > MAX_PAYLOAD:
                    raise DeferredRecovery("Metadata exceeds recovery size limit")
        try:
            return validate_payload(bytes(body), protocol, title, year)
        except UnverifiableRelease as error:
            raise DeferredRecovery("Metadata identity cannot be verified") from error
        except InvalidRelease:
            return None

    def providers(self, rows, protocol, tags, outside_tags):
        implementation = "Newznab" if protocol == "usenet" else "Torznab"
        providers = []
        for row in rows:
            if (
                row["Implementation"] != implementation
                or not row["EnableAutomaticSearch"]
            ):
                continue
            if (
                not outside_tags
                and row["Tags"]
                and not set(row["Tags"]).intersection(tags)
            ):
                continue
            settings = row["Settings"]
            match = re.fullmatch(r"(https?://.+)/(\d+)/?", settings.get("baseUrl", ""))
            if match and settings.get("apiKey"):
                providers.append(
                    (self.reachable(match[1]), int(match[2]), settings["apiKey"], row)
                )
        return providers

    def candidates(self, providers, title):
        # Group providers by manager to issue one bounded query per manager.
        managers = {}
        for base, provider_id, key, row in providers:
            managers.setdefault((base, key), {})[provider_id] = row
        for (base, key), indexers in managers.items():
            response = requests.get(
                base + "/api/v1/search",
                headers={"X-Api-Key": key},
                params={
                    "query": title,
                    "type": "search",
                    "indexerIds": list(indexers),
                    "limit": 100,
                },
                timeout=(10, 180),
            )
            response.raise_for_status()
            for candidate in response.json():
                row = indexers.get(candidate.get("indexerId"))
                if row:
                    yield candidate, row

    @staticmethod
    def clients(rows, protocol, tags, pinned=0):
        implementation = "Sabnzbd" if protocol == "usenet" else "QBittorrent"
        enabled = [
            r for r in rows if r["Enable"] and r["Implementation"] == implementation
        ]
        tagged = [r for r in enabled if set(r["Tags"]).intersection(tags)]
        eligible = tagged or [r for r in enabled if not r["Tags"]]
        if pinned:
            eligible = [r for r in eligible if r["Id"] == pinned]
        return sorted(eligible, key=lambda r: (r["Priority"], r["Id"]))

    def downloader(self, row):
        return Downloader(self, row)


class Downloader:
    def __init__(self, io, row):
        self.row, self.settings = row, row["Settings"]
        self.torrent = row["Implementation"] == "QBittorrent"
        settings = self.settings
        scheme = "https" if settings.get("useSsl") else "http"
        host = settings["host"]
        if ":" in host:
            host = f"[{host}]"
        url = f'{scheme}://{host}:{settings["port"]}/{settings.get("urlBase", "").strip("/")}'
        self.base = io.reachable(url).rstrip("/")
        self.session = requests.Session()
        self.category = settings.get("tvCategory", "")
        if not self.category:
            raise DeferredRecovery("Download client requires a TV category")
        if self.torrent:
            response = self.session.post(
                self.base + "/api/v2/auth/login",
                timeout=30,
                data={
                    "username": settings.get("username", ""),
                    "password": settings.get("password", ""),
                },
            )
            response.raise_for_status()
            if response.text.strip() != "Ok.":
                raise DeferredRecovery("Torrent client authentication failed")

    def close(self):
        self.session.close()

    def _sab(self, mode):
        response = self.session.get(
            self.base + "/api",
            timeout=30,
            params={
                "mode": mode,
                "output": "json",
                "apikey": self.settings["apiKey"],
                "limit": 1000,
                "cat": self.category,
            },
        )
        response.raise_for_status()
        data = response.json()
        if data.get("error") or mode not in data:
            raise DeferredRecovery("Cannot establish downloader duplicate state")
        return data[mode].get("slots", [])

    def find(self, title, infohash, year):
        if self.torrent:
            response = self.session.get(
                self.base + "/api/v2/torrents/info",
                timeout=30,
                params={"hashes": infohash},
            )
            response.raise_for_status()
            for item in response.json():
                if item.get("hash", "").lower() == infohash:
                    if item.get("category") != self.category:
                        raise DeferredRecovery(
                            "Existing torrent belongs to another category"
                        )
                    return infohash
            return None
        for mode in ("queue", "history"):
            for item in self._sab(mode):
                name = item.get("filename") or item.get("name", "")
                if release_identity(name, year) == release_identity(title, year):
                    if item.get("status") in ("Failed", "Deleted"):
                        raise DeferredRecovery("Existing download requires attention")
                    return item["nzo_id"]
        return None

    def add(self, payload, title, seed_criteria=None):
        if self.torrent:
            # Keep qBittorrent's category save path/TMM and configured initial state.
            paused = self.settings.get("initialState", 0) == 1
            data = {
                "category": self.category,
                "paused": str(paused).lower(),
                "stopped": str(paused).lower(),
                "sequentialDownload": str(
                    self.settings.get("sequentialOrder", False)
                ).lower(),
                "firstLastPiecePrio": str(
                    self.settings.get("firstAndLast", False)
                ).lower(),
            }
            layout = {0: "Original", 1: "Subfolder"}.get(
                self.settings.get("contentLayout", 0)
            )
            if layout:
                data["contentLayout"] = layout
            for source, target in (
                ("seedRatio", "ratioLimit"),
                ("seedTime", "seedingTimeLimit"),
            ):
                value = (seed_criteria or {}).get(source)
                if value is not None:
                    data[target] = value
            response = self.session.post(
                self.base + "/api/v2/torrents/add",
                timeout=60,
                data=data,
                files={
                    "torrents": (
                        title + ".torrent",
                        payload.body,
                        "application/x-bittorrent",
                    )
                },
            )
            response.raise_for_status()
            if response.text.strip() not in ("Ok.", ""):
                raise DeferredRecovery("Torrent submission was not acknowledged")
            # A 200 response alone does not prove qBittorrent accepted the torrent.
            return self.find(title, payload.infohash, None)
        data = {
            "mode": "addfile",
            "output": "json",
            "apikey": self.settings["apiKey"],
            "cat": self.category,
            "nzbname": title,
            "priority": self.settings.get("olderTvPriority", -100),
        }
        response = self.session.post(
            self.base + "/api",
            timeout=60,
            data=data,
            files={"nzbfile": (title + ".nzb", payload.body, "application/x-nzb")},
        )
        response.raise_for_status()
        result = response.json()
        ids = result.get("nzo_ids", [])
        if not result.get("status") or len(ids) != 1:
            raise DeferredRecovery("NZB submission was not acknowledged")
        return ids[0]
