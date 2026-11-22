# 21.11.26

"""Crunchyroll playback: a dub keeps its own soft subtitles and captions even when the main (ja-JP) version offers none."""

from types import SimpleNamespace

import pytest

from VibraVid.services.crunchyroll import client as cr_client

# Shapes taken from the real play-service payloads of Attack on Titan S2E3
JA_MAIN = {
    "audioLocale": "ja-JP",
    "url": "https://example.test/jajp/itit/cenc/dash/manifest.mpd",
    "token": "tok-ja",
    "subtitles": {},
    "captions": {},
    "hardSubs": {"it-IT": {"url": "https://example.test/jajp/itit/cenc/dash/manifest.mpd"}},
}
EN_DUB = {
    "audioLocale": "en-US",
    "url": "https://example.test/enus/clean/cenc/dash/manifest.mpd",
    "token": "tok-en",
    "subtitles": {"en-US": {"format": "ass", "language": "en-US", "url": "https://example.test/enus.ass"}},
    "captions": {"en-US": {"format": "vtt", "language": "en-US", "url": "https://example.test/enus.vtt"}},
}


def _client(payloads, deauthed):
    return SimpleNamespace(
        get_streams=lambda media_id: dict(payloads[media_id]),
        deauth_video=lambda media_id, token: deauthed.append((media_id, token)),
        _get_headers=lambda: {},
        use_tv=False,
    )


def _langs(subtitles):
    return sorted((s["language"], s["format"], s["closed_caption"]) for s in subtitles)


def test_dub_keeps_its_own_subtitles_when_main_has_none():
    deauthed = []
    client = _client({"EN": EN_DUB, "JA": JA_MAIN}, deauthed)

    _, _, subtitles, _, locale = cr_client.get_playback_session(client, "EN", "JA")

    assert locale == "en-US"
    assert _langs(subtitles) == [("en-US", "ass", False), ("en-US", "vtt", True)]


def test_main_languages_are_added_without_duplicating_the_dub_ones():
    main = {
        **JA_MAIN,
        "subtitles": {
            "en-US": {"format": "ass", "language": "en-US", "url": "https://example.test/main-en.ass"},
            "it-IT": {"format": "ass", "language": "it-IT", "url": "https://example.test/main-it.ass"},
        },
    }
    client = _client({"EN": EN_DUB, "JA": main}, [])

    _, _, subtitles, _, _ = cr_client.get_playback_session(client, "EN", "JA")

    assert _langs(subtitles) == [("en-US", "ass", False), ("en-US", "vtt", True), ("it-IT", "ass", False)]
    # The complete set from the main version wins for a language both have
    assert next(s for s in subtitles if s["language"] == "en-US" and s["format"] == "ass")["url"].endswith("main-en.ass")


def test_a_failing_main_fetch_falls_back_to_the_dub_subtitles():
    def get_streams(media_id):
        if media_id == "JA":
            raise RuntimeError("boom")
        return dict(EN_DUB)

    client = SimpleNamespace(get_streams=get_streams, deauth_video=lambda *_: None, _get_headers=lambda: {}, use_tv=False)

    _, _, subtitles, _, _ = cr_client.get_playback_session(client, "EN", "JA")

    assert _langs(subtitles) == [("en-US", "ass", False), ("en-US", "vtt", True)]


def test_both_playback_tokens_are_released():
    deauthed = []
    client = _client({"EN": EN_DUB, "JA": JA_MAIN}, deauthed)

    cr_client.get_playback_session(client, "EN", "JA")

    assert sorted(deauthed) == [("EN", "tok-en"), ("JA", "tok-ja")]


def test_without_main_guid_only_the_version_subtitles_are_used():
    client = _client({"EN": EN_DUB}, [])

    _, _, subtitles, _, _ = cr_client.get_playback_session(client, "EN", None)

    assert _langs(subtitles) == [("en-US", "ass", False), ("en-US", "vtt", True)]


def test_merge_subtitles_keeps_base_entries_and_skips_non_dicts():
    base = [{"language": "en-US", "closed_caption": False, "url": "a"}]
    extra = [{"language": "en-US", "closed_caption": False, "url": "b"}, {"language": "en-US", "closed_caption": True, "url": "c"}, "junk", None]

    merged = cr_client.merge_subtitles(base, extra)

    assert [s["url"] for s in merged] == ["a", "c"]


def test_main_version_is_asked_after_the_dub_slot_is_released():
    # An account with one concurrent stream refuses the main request while the dub's session is still open
    events = []

    def get_streams(media_id):
        events.append(("get", media_id))
        return dict({"EN": EN_DUB, "JA": JA_MAIN}[media_id])

    client = SimpleNamespace(
        get_streams=get_streams,
        deauth_video=lambda media_id, token: events.append(("deauth", media_id)),
        _get_headers=lambda: {},
        use_tv=False,
    )

    cr_client.get_playback_session(client, "EN", "JA")

    assert events == [("get", "EN"), ("deauth", "EN"), ("get", "JA"), ("deauth", "JA")]


# --- versions only served with burned-in subtitles (the ja-JP one) ---

BASE = "https://cr-play-service.prd.crunchyrollsvc.com/v1/manifest/G8WUN/static/majin/e000jajp"
HARD_LOCALES = ["en-US", "de-DE", "es-419", "fr-FR", "it-IT", "pt-BR"]


def _manifest(segment):
    return f"{BASE}/{segment}/cenc/dash/manifest.mpd?token=x"


def _hard_payload(**extra):
    return {
        "url": _manifest("itit"),
        "hardSubs": {loc: {"url": _manifest(loc.replace("-", "").lower())} for loc in HARD_LOCALES},
        **extra,
    }


def test_clean_manifest_is_always_preferred():
    payload = {"url": _manifest("clean"), "hardSubs": {"en-US": {"url": _manifest("enus")}}}
    assert cr_client.manifest_url(payload, "en-US") == payload["url"]


def test_burned_in_only_version_is_refused_with_the_available_languages():
    with pytest.raises(cr_client.CrunchyrollHardSubError) as err:
        cr_client.manifest_url(_hard_payload())
    message = str(err.value)
    assert "it-IT" in message and "pt-BR" in message
    assert "hard_sub_locale" in message and "Conf/login.toml" in message
    assert "[" not in message  # the console would read a [section] as markup and drop it


def test_it_never_picks_a_language_on_its_own():
    # The old fallback silently returned the fr-FR burned-in video
    with pytest.raises(cr_client.CrunchyrollHardSubError):
        cr_client.manifest_url(_hard_payload(), "")


@pytest.mark.parametrize("wanted, segment", [("it-IT", "itit"), ("IT-it", "itit"), ("it", "itit"), ("es-419", "es419"), ("pt-br", "ptbr")])
def test_requested_burned_in_language_is_used(wanted, segment):
    assert cr_client.manifest_url(_hard_payload(), wanted) == _manifest(segment)


def test_unavailable_burned_in_language_is_refused_by_name():
    with pytest.raises(cr_client.CrunchyrollHardSubError) as err:
        cr_client.manifest_url(_hard_payload(), "ja-JP")
    assert "no burned-in ja-JP" in str(err.value)


def test_a_subtitle_free_variant_listed_in_hard_subs_is_accepted():
    payload = _hard_payload()
    payload["hardSubs"]["none"] = {"url": _manifest("none")}
    assert cr_client.manifest_url(payload) == _manifest("none")


def test_download_profile_without_hard_subs_list_is_judged_by_its_path():
    payload = {"url": _manifest("itit"), "hardSubs": {}}
    with pytest.raises(cr_client.CrunchyrollHardSubError):
        cr_client.manifest_url(payload)
    assert cr_client.manifest_url(payload, "it-IT") == payload["url"]


def test_payload_with_nothing_to_judge_keeps_the_root_url():
    assert cr_client.manifest_url({"url": "https://example.test/a.mpd"}) == "https://example.test/a.mpd"
    assert cr_client.manifest_url({}) == ""


def _tv_client(payload, deauthed):
    return SimpleNamespace(
        get_streams=lambda media_id: dict(payload),
        deauth_video=lambda media_id, token: deauthed.append(media_id),
        prefetch_manifest=lambda url: url,
        _get_headers=lambda: {},
        use_tv=True,
    )


def test_session_refuses_the_main_video_and_frees_its_slot(monkeypatch):
    monkeypatch.setattr(cr_client, "configured_hard_sub_locale", lambda: "")
    deauthed = []
    client = _tv_client(_hard_payload(token="tok", audioLocale="ja-JP"), deauthed)

    with pytest.raises(cr_client.CrunchyrollHardSubError):
        cr_client.get_playback_session(client, "JA")

    assert deauthed == ["JA"]


def test_session_uses_the_configured_burned_in_language(monkeypatch):
    monkeypatch.setattr(cr_client, "configured_hard_sub_locale", lambda: "it-IT")
    client = _tv_client(_hard_payload(token="tok", audioLocale="ja-JP"), [])

    mpd_url, _, subtitles, _, locale = cr_client.get_playback_session(client, "JA")

    assert mpd_url == _manifest("itit") and locale == "ja-JP" and subtitles == []


def test_extra_audio_track_is_never_refused(monkeypatch):
    # Only its audio is used, whatever the video burned in
    monkeypatch.setattr(cr_client, "configured_hard_sub_locale", lambda: "")
    client = _tv_client(_hard_payload(token="tok", audioLocale="ja-JP"), [])

    mpd_url, *_ = cr_client.get_playback_session(client, "JA", None, video=False)

    assert mpd_url == _manifest("itit")


def test_configured_locale_is_read_from_the_login_file(monkeypatch):
    seen = {}

    def get(section, key, **kwargs):
        seen["call"] = (section, key)
        return "  it-IT "

    monkeypatch.setattr(cr_client, "config_manager", SimpleNamespace(login=SimpleNamespace(get=get)))

    assert cr_client.configured_hard_sub_locale() == "it-IT"
    assert seen["call"] == ("crunchyroll", "hard_sub_locale")


def test_a_429_is_written_to_the_log_with_its_backoff(monkeypatch, caplog):
    monkeypatch.setattr(cr_client, "_cooldown_until", 0.0)

    with caplog.at_level("WARNING"):
        cr_client.note_rate_limit(520)

    assert any("HTTP 429" in r.message and "520s" in r.message for r in caplog.records)
    assert 500 < cr_client.cooldown_remaining() <= 520
