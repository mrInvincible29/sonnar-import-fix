"""Reject HTML error pages, wrong episodes and unsafe torrent metadata."""

import hashlib

import bencodepy
import pytest

from src.core.release_payload import InvalidRelease, release_identity, validate_payload

TITLE = "Example.Show.2018.S04E06.REPACK.1080p.WEB-DL.DUAL.H264-GRP"


def torrent(name=TITLE + ".mkv", extra=None):
    codec = bencodepy.Bencode(dict_ordered=True)
    info = {
        b"name": name.encode(),
        b"length": 100,
        b"piece length": 16384,
        b"pieces": b"x" * 20,
    }
    info.update(extra or {})
    return codec.encode(
        {b"announce": b"https://tracker.invalid/secret", b"info": info}
    ), codec.encode(info)


def test_release_identity_only_ignores_punctuation_case_and_series_year():
    assert release_identity(TITLE, 2018) == release_identity(
        "Example Show S04E06 REPACK 1080p WEB DL DUAL H264 GRP", 2018
    )
    assert release_identity(TITLE, 2018) != release_identity(
        TITLE.replace("E06", "E07"), 2018
    )
    assert release_identity(TITLE, 2018) != release_identity(
        TITLE.replace("REPACK", "REPACK2"), 2018
    )
    assert release_identity(TITLE, 2018) != release_identity(
        TITLE.replace("GRP", "OTHER"), 2018
    )


def test_nzb_error_page_is_not_downloadable_payload():
    with pytest.raises(InvalidRelease):
        validate_payload(b"Invalid download link.", "usenet", TITLE)


def test_nzb_validates_episode_and_contains_real_segments():
    body = (
        f"<nzb xmlns=\"http://www.newzbin.com/DTD/2003/nzb\"><file subject='{TITLE}.part01.rar'>"
        '<segments><segment bytes="100" number="1">article@news.invalid</segment></segments>'
        "</file></nzb>"
    ).encode()
    assert validate_payload(body, "usenet", TITLE).size == 100
    with pytest.raises(InvalidRelease):
        validate_payload(body, "usenet", TITLE.replace("E06", "E07"))
    with pytest.raises(InvalidRelease):
        validate_payload(
            body.replace(b"<segments>", b"<segments/><!--").replace(
                b"</segments>", b"-->"
            ),
            "usenet",
            TITLE,
        )


def test_nzb_rejects_entities_and_wrong_document_even_with_file_nodes():
    for data in [
        b'<html><file subject="S04E06"/></html>',
        b'<!DOCTYPE nzb [<!ENTITY x "secret">]><nzb>&x;</nzb>',
    ]:
        with pytest.raises(InvalidRelease):
            validate_payload(data, "usenet", TITLE)


def test_torrent_hash_is_hash_of_original_info_dictionary():
    body, info = torrent()
    payload = validate_payload(body, "torrent", TITLE)
    assert payload.infohash == hashlib.sha1(info).hexdigest()
    assert payload.size == 100


@pytest.mark.parametrize(
    "name", ["../" + TITLE, TITLE.replace("E06", "E07"), "Unrelated.mkv"]
)
def test_torrent_rejects_wrong_identity_or_unsafe_root(name):
    body, _ = torrent(name)
    with pytest.raises(InvalidRelease):
        validate_payload(body, "torrent", TITLE)


def test_torrent_rejects_html_trailing_data_and_invalid_pieces():
    body, _ = torrent()
    invalid, _ = torrent(extra={b"pieces": b"bad"})
    for data in [b"<html>Login required</html>", body + b"garbage", invalid]:
        with pytest.raises(InvalidRelease):
            validate_payload(data, "torrent", TITLE)


def test_torrent_rejects_extra_episode_in_multifile_payload():
    body, _ = torrent(
        extra={
            b"files": [
                {b"length": 100, b"path": [TITLE.encode() + b".mkv"]},
                {
                    b"length": 100,
                    b"path": [TITLE.replace("E06", "E07").encode() + b".mkv"],
                },
            ]
        }
    )
    with pytest.raises(InvalidRelease):
        validate_payload(body, "torrent", TITLE)


def test_nzb_rejects_different_revision_even_with_same_episode():
    body = (
        f'<nzb><file subject="{TITLE.replace("REPACK", "REPACK2")}.rar"><segments>'
        '<segment bytes="100">article@news.invalid</segment></segments></file></nzb>'
    ).encode()
    with pytest.raises(InvalidRelease):
        validate_payload(body, "usenet", TITLE)


def test_utf16_xml_cannot_bypass_entity_guard():
    body = (
        '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE nzb [<!ENTITY x "secret">]>'
        f'<nzb><file subject="{TITLE}"><segments><segment bytes="100">&x;</segment>'
        "</segments></file></nzb>"
    ).encode("utf-16")
    with pytest.raises(InvalidRelease):
        validate_payload(body, "usenet", TITLE)
