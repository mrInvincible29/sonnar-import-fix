"""Bounded, conservative validation of replacement download metadata."""

import hashlib
import re
from dataclasses import dataclass
from xml.etree import ElementTree

import bencodepy

MAX_PAYLOAD = 8 * 1024 * 1024
EPISODE = re.compile(r"s\d{1,3}e\d{1,4}", re.I)


class InvalidRelease(ValueError):
    """The response cannot safely represent this single-episode release."""


class UnverifiableRelease(InvalidRelease):
    """Valid metadata requiring a format this recovery cannot verify."""


@dataclass(frozen=True)
class Payload:
    body: bytes
    size: int
    infohash: str = ""


def release_identity(title, year=None):
    """Keep episode, revision, quality and group; normalize separators only."""
    title = re.sub(r"\.(mkv|mp4|avi|torrent|nzb)$", "", title, flags=re.I)
    tokens = re.findall(r"[a-z0-9]+", title.lower())
    if year:
        # Only the series portion may omit the known series year.
        for position, token in enumerate(tokens):
            if EPISODE.fullmatch(token):
                tokens[:position] = [t for t in tokens[:position] if t != str(year)]
                break
    return "".join(tokens)


def _check_episodes(names, title):
    expected = set(EPISODE.findall(title.lower()))
    found = set(EPISODE.findall(" ".join(names).lower()))
    multi = re.search(r"s\d+e\d+(?:[ ._-]*e\d+|-\d+)", " ".join(names), re.I)
    if len(expected) != 1 or found != expected or multi:
        raise InvalidRelease("Payload episode mismatch")


def _safe_component(value):
    if not isinstance(value, bytes):
        raise InvalidRelease("Invalid torrent path")
    name = value.decode("utf-8", errors="strict")
    if name in ("", ".", "..") or any(c in name for c in ("/", "\\", "\x00")):
        raise InvalidRelease("Unsafe torrent path")
    return name


def _nzb(body, title, year):
    if b"\x00" in body or b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        raise InvalidRelease("XML declarations are not accepted")
    root = ElementTree.fromstring(body)
    if root.tag.rsplit("}", 1)[-1] != "nzb":
        raise InvalidRelease("Not an NZB document")
    names, size = [], 0
    for node in root:
        if node.tag.rsplit("}", 1)[-1] != "file":
            continue
        names.append(node.attrib.get("subject", ""))
        segments = [n for n in node.iter() if n.tag.rsplit("}", 1)[-1] == "segment"]
        if not segments or any(
            not n.text or int(n.attrib["bytes"]) <= 0 for n in segments
        ):
            raise InvalidRelease("NZB has no usable segments")
        size += sum(int(n.attrib["bytes"]) for n in segments)
    if names and not EPISODE.search(" ".join(names)):
        raise UnverifiableRelease("Obfuscated NZB identity cannot be verified")
    _check_episodes(names, title)
    filenames = []
    for subject in names:
        quoted = re.search(r'"([^\"]+)"', subject)
        name = quoted[1] if quoted else re.split(r"\s+yEnc\b", subject, flags=re.I)[0]
        name = re.sub(
            r"(?:\.part\d+|\.vol\d+\+\d+)?\.(?:rar|par2|r\d+|nfo|sfv)$",
            "",
            name,
            flags=re.I,
        )
        filenames.append(release_identity(name, year))
    if release_identity(title, year) not in filenames:
        raise InvalidRelease("NZB release identity mismatch")
    if size <= 0:
        raise InvalidRelease("Empty NZB")
    return Payload(body, size)


def _torrent(body, title, year):
    codec = bencodepy.Bencode(max_depth=32, dict_ordered=True)
    data = codec.decode(body)
    # Round trip verifies that hashing the decoded info preserves the exact bytes.
    if codec.encode(data) != body:
        raise InvalidRelease("Noncanonical or trailing torrent data")
    info = data[b"info"]
    if info.get(b"meta version") == 2 and b"pieces" not in info:
        raise UnverifiableRelease("Pure v2 torrent identity is unsupported")
    name = _safe_component(info[b"name"])
    if release_identity(name, year) != release_identity(title, year):
        raise InvalidRelease("Torrent release identity mismatch")
    names = [name]
    if b"files" in info:
        if b"length" in info:
            raise InvalidRelease("Conflicting torrent layout")
        size = 0
        for item in info[b"files"]:
            path = [_safe_component(p) for p in item[b"path"]]
            if not path or item[b"length"] < 0:
                raise InvalidRelease("Invalid torrent file")
            names.append("/".join(path))
            size += item[b"length"]
    else:
        size = info[b"length"]
    _check_episodes(names, title)
    length, pieces = info[b"piece length"], info[b"pieces"]
    if size <= 0 or length <= 0 or len(pieces) != ((size + length - 1) // length) * 20:
        raise InvalidRelease("Invalid torrent pieces")
    return Payload(body, size, hashlib.sha1(codec.encode(info)).hexdigest())


def validate_payload(body, protocol, title, year=None):
    """Validate NZB or v1/hybrid torrent bytes without exposing private metadata."""
    if not body or len(body) > MAX_PAYLOAD:
        raise InvalidRelease("Download metadata is empty or too large")
    try:
        if protocol == "usenet":
            return _nzb(body, title, year)
        if protocol == "torrent":
            return _torrent(body, title, year)
        raise InvalidRelease("Unsupported download protocol")
    except InvalidRelease:
        raise
    except Exception as error:
        raise InvalidRelease("Malformed download metadata") from error
