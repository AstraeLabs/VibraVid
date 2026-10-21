# 21.10.26

"""Chapters never run past the media: no open-ended last chapter, no chapter starting after the end."""

import os
from types import SimpleNamespace

import pytest

from VibraVid.core.muxing.helper.chapters import clamp_chapters, write_ffmetadata_chapters, write_ogm_chapters
from VibraVid.services.crunchyroll import client as cr_client

# Real skip-events of One Piece S1E1: the credits end (1445 s) after the file does (1400.6 s)
CHAPTERS = [
    {"name": "Chapter 1", "seconds": 0},
    {"name": "Intro", "seconds": 0},
    {"name": "Chapter 2", "seconds": 109},
    {"name": "Credits", "seconds": 1375},
    {"name": "Chapter 3", "seconds": 1445},
]


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    finally:
        os.unlink(path)


def _ffmeta_ends(text):
    return [int(line.split("=")[1]) for line in text.splitlines() if line.startswith("END=")]


def test_clamp_drops_chapters_starting_after_the_end():
    kept = clamp_chapters(CHAPTERS, 1400.6)
    assert [c["name"] for c in kept] == ["Chapter 1", "Intro", "Chapter 2", "Credits"]


def test_clamp_without_a_duration_changes_nothing():
    assert clamp_chapters(CHAPTERS, None) == CHAPTERS
    assert clamp_chapters(CHAPTERS, 0) == CHAPTERS


def test_clamp_never_returns_an_empty_list():
    assert clamp_chapters([{"name": "Late", "seconds": 500}], 100) == [{"name": "Late", "seconds": 500}]


def test_last_ffmetadata_chapter_ends_with_the_media():
    text = _read(write_ffmetadata_chapters(CHAPTERS, 1400.6))
    assert _ffmeta_ends(text)[-1] == 1400600
    assert "Chapter 3" not in text


def test_last_ffmetadata_chapter_keeps_the_open_end_without_a_duration():
    text = _read(write_ffmetadata_chapters([{"name": "A", "seconds": 0}, {"name": "B", "seconds": 60}]))
    assert _ffmeta_ends(text)[-1] == 60000 + 999999000


def test_every_chapter_ends_after_it_starts():
    text = _read(write_ffmetadata_chapters([{"name": "A", "seconds": 0}, {"name": "B", "seconds": 100}], 100.5))
    starts = [int(line.split("=")[1]) for line in text.splitlines() if line.startswith("START=")]
    assert all(end > start for start, end in zip(starts, _ffmeta_ends(text)))


def test_ogm_chapters_are_clamped_too():
    text = _read(write_ogm_chapters(CHAPTERS, 1400.6))
    assert "CHAPTER03NAME=Credits" in text and "CHAPTER04" not in text


class _Session:
    def __init__(self, answers):
        self.answers = answers
        self.asked = []

    def get(self, url):
        self.asked.append(url)
        status, body = self.answers.get(url.split("/")[3], (404, None))
        return SimpleNamespace(status_code=status, json=lambda: body)


def _episode_chapters(answers):
    session = _Session(answers)
    return cr_client.get_episode_chapters(SimpleNamespace(session=session), "GR3VWXP96"), session


def test_crunchyroll_skip_events_become_chapters():
    skip = {"intro": {"start": 0, "end": 109}, "credits": {"start": 1375, "end": 1445}}
    chapters, session = _episode_chapters({"skip-events": (200, skip)})
    assert [(c["name"], c["seconds"]) for c in chapters] == [
        ("Chapter 1", 0), ("Intro", 0), ("Chapter 2", 109), ("Credits", 1375), ("Chapter 3", 1445),
    ]
    assert len(session.asked) == 1


def test_crunchyroll_falls_back_to_the_datalab_intro_file():
    chapters, session = _episode_chapters({"skip-events": (403, None), "datalab-intro-v2": (200, {"startTime": 20, "endTime": 110})})
    assert [(c["name"], c["seconds"]) for c in chapters] == [("Chapter 1", 0), ("Intro", 20), ("Chapter 2", 110)]
    assert len(session.asked) == 2


def test_crunchyroll_without_any_marker_file_has_no_chapters():
    chapters, _ = _episode_chapters({})
    assert chapters == []


@pytest.mark.parametrize("body", [[], "x"])
def test_crunchyroll_ignores_non_object_json(body):
    chapters, _ = _episode_chapters({"skip-events": (200, body)})
    assert chapters == []


@pytest.mark.parametrize(
    "playback, expected",
    [
        ({"audioLocale": "ja-JP"}, "ja-JP"),
        ({"audio_locale": "it-IT"}, "it-IT"),
        ({"audio": {"locale": "fr-FR"}}, "fr-FR"),
        ({}, None),
    ],
)
def test_crunchyroll_audio_locale_is_read_from_the_playback_response(playback, expected):
    client = SimpleNamespace(
        get_streams=lambda _id: {"url": "https://example.test/a.mpd", "token": None, **playback},
        _get_headers=lambda: {},
    )
    assert cr_client.get_playback_session(client, "GR3VWXP96")[4] == expected
