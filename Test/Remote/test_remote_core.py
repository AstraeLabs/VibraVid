# 12.10.26

"""Remote server mode, Django-free part: wire models, config, auth, sessions, capture mode, CLI helpers."""

import json
import time

import pytest

from VibraVid.core.downloader import DASH_Downloader, HLS_Downloader, ISM_Downloader, MP4_Downloader
from VibraVid.core.downloader.yt_dlp_downloader import YTDLPDownloader
from VibraVid.core.ui.tracker import context_tracker
from VibraVid.remote.capture import CaptureBuffer, capturing
from VibraVid.remote.cli import parse_server_options
from VibraVid.remote.config import (
    RemoteUser,
    ServeSettings,
    load_remote_servers,
    load_serve_settings,
    pick_remote_server,
)
from VibraVid.remote.models import RemoteError, ResolvedMedia, pack_text, unpack_text
from VibraVid.remote.server import (
    SessionStore,
    _in_thread,
    allowed_sites,
    authenticate,
    call_license_hook,
    call_refresh_hook,
    resolve_media,
    site_allowed,
)

BIG_MANIFEST = "#EXTM3U\n" + "#EXT-X-STREAM-INF:BANDWIDTH=1\nhttps://cdn.test/seg.m3u8\n" * 400


# ── wire models ───────────────────────────────────────────────────────────────

def test_small_text_is_not_compressed():
    blob = pack_text("tiny")

    assert blob == {"z": False, "d": "tiny"}
    assert unpack_text(blob) == "tiny"


def test_big_text_is_compressed_and_round_trips():
    blob = pack_text(BIG_MANIFEST)

    assert blob["z"] is True
    assert len(blob["d"]) < len(BIG_MANIFEST)
    assert unpack_text(blob) == BIG_MANIFEST


def test_none_and_plain_strings_pass_through_unpack():
    assert pack_text(None) is None
    assert unpack_text(None) is None
    assert unpack_text("already text") == "already text"


def test_resolved_media_round_trips_through_json():
    media = ResolvedMedia(
        kind="dash",
        url="https://cdn.test/a.mpd",
        manifest_content=BIG_MANIFEST,
        headers={"User-Agent": "x"},
        license_url="https://lic.test/",
        license_data={"a": 1},
        has_drm=True,
        license_proxy=True,
        other_tracks=[{"type": "subtitle", "url": "https://s.test/it.srt", "language": "it"}],
        title="Show",
        season=2,
        episode=3,
    )

    wire = json.loads(json.dumps(media.to_wire()))
    back = ResolvedMedia.from_wire(wire)

    assert back == media


def test_from_wire_ignores_unknown_fields():
    wire = ResolvedMedia(kind="hls", url="https://x/m.m3u8").to_wire()
    wire["field_from_a_newer_server"] = 1

    assert ResolvedMedia.from_wire(wire).url == "https://x/m.m3u8"


# ── config ────────────────────────────────────────────────────────────────────

def _write(tmp_path, data) -> str:
    path = tmp_path / "remote.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_serve_settings_defaults_when_file_is_missing(tmp_path):
    settings = load_serve_settings(str(tmp_path / "nope.json"))

    assert settings.host == "127.0.0.1"
    assert settings.port == 8786
    assert settings.users == {}
    assert settings.sites is None


def test_serve_settings_parse_users_and_allowlists(tmp_path):
    path = _write(tmp_path, {
        "serve": {
            "host": "0.0.0.0",
            "port": 9000,
            "sites": ["appletv", "disney"],
            "users": {
                "key-alice": {"username": "alice", "sites": ["appletv"], "server_cdm": True},
                "key-bob": {"username": "bob"},
            },
        }
    })

    settings = load_serve_settings(path)

    assert (settings.host, settings.port) == ("0.0.0.0", 9000)
    assert settings.users["key-alice"] == RemoteUser("alice", frozenset({"appletv"}), True)
    assert settings.users["key-bob"] == RemoteUser("bob", None, False)


def test_a_broken_config_file_degrades_to_defaults(tmp_path):
    path = tmp_path / "remote.json"
    path.write_text("{ not json", encoding="utf-8")

    assert load_serve_settings(str(path)).users == {}
    assert load_remote_servers(str(path)) == {}


def test_remote_servers_need_a_url_and_strip_the_trailing_slash(tmp_path):
    path = _write(tmp_path, {
        "remote_services": {
            "home": {"url": "http://10.0.0.5:8786/", "api_key": "k", "server_cdm": True},
            "broken": {"api_key": "k"},
        }
    })

    servers = load_remote_servers(path)

    assert list(servers) == ["home"]
    assert servers["home"].url == "http://10.0.0.5:8786"
    assert servers["home"].server_cdm is True


def test_pick_remote_server_rules(tmp_path):
    one = _write(tmp_path, {"remote_services": {"home": {"url": "http://a"}}})
    assert pick_remote_server(None, one).name == "home"
    assert pick_remote_server("home", one).name == "home"
    with pytest.raises(LookupError, match="Unknown remote server"):
        pick_remote_server("other", one)

    two = _write(tmp_path, {"remote_services": {"a": {"url": "http://a"}, "b": {"url": "http://b"}}})
    with pytest.raises(LookupError, match="choose one with --server"):
        pick_remote_server(None, two)
    assert pick_remote_server("b", two).url == "http://b"

    with pytest.raises(LookupError, match="No remote server configured"):
        pick_remote_server(None, str(tmp_path / "missing.json"))


# ── auth / allowlists ─────────────────────────────────────────────────────────

SETTINGS = ServeSettings(
    sites=frozenset({"appletv", "disney", "hbomax"}),
    users={
        "key-alice": RemoteUser("alice", frozenset({"appletv", "primevideo"}), True),
        "key-bob": RemoteUser("bob"),
    },
)


def test_authenticate_matches_exact_keys_only():
    assert authenticate(SETTINGS, "key-alice").username == "alice"
    assert authenticate(SETTINGS, "key-bob").username == "bob"
    assert authenticate(SETTINGS, "key-alic") is None
    assert authenticate(SETTINGS, "key-alice ") is None
    assert authenticate(SETTINGS, "") is None
    assert authenticate(SETTINGS, None) is None


def test_site_allowlist_is_global_intersect_per_key():
    alice, bob = SETTINGS.users["key-alice"], SETTINGS.users["key-bob"]

    assert site_allowed(SETTINGS, alice, "appletv")
    assert not site_allowed(SETTINGS, alice, "disney")  # not in her list
    assert not site_allowed(SETTINGS, alice, "primevideo")  # in her list, but not in the global ceiling
    assert site_allowed(SETTINGS, bob, "disney")
    assert not site_allowed(SETTINGS, bob, "pornhub")


def test_no_allowlists_means_everything_is_allowed():
    open_settings = ServeSettings(users={"k": RemoteUser("u")})

    assert site_allowed(open_settings, open_settings.users["k"], "anything")
    assert allowed_sites(open_settings, open_settings.users["k"], ["a", "b"]) == ["a", "b"]
    assert allowed_sites(SETTINGS, SETTINGS.users["key-alice"], ["appletv", "disney", "primevideo"]) == ["appletv"]


# ── sessions ──────────────────────────────────────────────────────────────────

def _media() -> ResolvedMedia:
    return ResolvedMedia(kind="hls", url="https://x/m.m3u8")


def test_session_round_trip_and_ownership():
    store = SessionStore()
    session = store.create("alice", "1.1.1.1", "appletv", [_media()], [{}])

    assert store.get(session.id, "alice", "1.1.1.1") is session
    with pytest.raises(RemoteError) as other_user:
        store.get(session.id, "bob", "1.1.1.1")
    assert other_user.value.status == 403
    with pytest.raises(RemoteError) as other_ip:
        store.get(session.id, "alice", "2.2.2.2")
    assert other_ip.value.status == 403
    with pytest.raises(RemoteError) as unknown:
        store.get("nope", "alice", "1.1.1.1")
    assert unknown.value.status == 404


def test_ip_binding_can_be_switched_off():
    store = SessionStore(bind_to_ip=False)
    session = store.create("alice", "1.1.1.1", "appletv", [_media()])

    assert store.get(session.id, "alice", "9.9.9.9") is session


def test_idle_sessions_expire():
    store = SessionStore(ttl=0)
    session = store.create("alice", "1.1.1.1", "appletv", [_media()])
    time.sleep(0.02)

    with pytest.raises(RemoteError) as gone:
        store.get(session.id, "alice", "1.1.1.1")
    assert gone.value.status == 404
    assert len(store) == 0


def test_session_cap_and_delete():
    store = SessionStore(max_sessions=1)
    first = store.create("alice", "1.1.1.1", "appletv", [_media()])

    with pytest.raises(RemoteError) as full:
        store.create("alice", "1.1.1.1", "appletv", [_media()])
    assert full.value.status == 503

    assert store.delete(first.id, "alice", "1.1.1.1") is True
    assert store.delete(first.id, "alice", "1.1.1.1") is False
    store.create("alice", "1.1.1.1", "appletv", [_media()])  # room again


def test_session_hooks_default_to_empty():
    store = SessionStore()
    session = store.create("alice", "1.1.1.1", "appletv", [_media(), _media()], [{"license_request_fn": print}])

    assert session.hooks(0) == {"license_request_fn": print}
    assert session.hooks(1) == {}


# ── worker thread ─────────────────────────────────────────────────────────────

def test_in_thread_returns_the_value_and_reraises_errors():
    assert _in_thread(lambda: 41 + 1) == 42
    with pytest.raises(KeyError):
        _in_thread(lambda: {}["missing"])


def test_in_thread_gives_up_on_a_stuck_service():
    with pytest.raises(RemoteError) as stuck:
        _in_thread(lambda: time.sleep(5), timeout=0.05)

    assert stuck.value.status == 504


def test_in_thread_runs_with_its_own_context():
    context_tracker.site_name = "main-thread-site"

    seen = _in_thread(lambda: context_tracker.site_name)

    assert seen is None


# ── capture mode on the real downloaders ──────────────────────────────────────

def _hls(tmp_path, **kwargs):
    return HLS_Downloader(
        m3u8_url="https://cdn.test/master.m3u8",
        m3u8_content="#EXTM3U\n",
        headers={"User-Agent": "ua"},
        output_path=str(tmp_path / "out" / "film.mkv"),
        **kwargs,
    )


def test_hls_capture_records_the_inputs_and_touches_nothing(tmp_path):
    buffer = CaptureBuffer()
    downloader = _hls(tmp_path, license_url="https://lic.test/", license_headers={"X": "1"}, has_drm=True, cookies={"c": "v"})

    with capturing(buffer):
        result = downloader.start()

    assert result.ok
    (item,) = buffer.items
    assert (item.kind, item.url, item.manifest_content) == ("hls", "https://cdn.test/master.m3u8", "#EXTM3U\n")
    assert item.headers == {"User-Agent": "ua"}
    assert item.cookies == {"c": "v"}
    assert item.license_url == "https://lic.test/"
    assert item.license_headers == {"X": "1"}
    assert item.has_drm is True
    assert item.license_proxy is False
    assert not (tmp_path / "out").exists()  # no directory, nothing downloaded


def test_capture_keeps_callables_on_the_server_side(tmp_path):
    def sign_and_post(challenge, item):
        return b"license"

    def refresh():
        return "#EXTM3U\n#fresh"

    buffer = CaptureBuffer()
    downloader = _hls(tmp_path, license_url="https://lic.test/", license_request_fn=sign_and_post, manifest_refresh_fn=refresh)

    with capturing(buffer):
        downloader.start()

    (item,) = buffer.items
    assert item.license_proxy is True
    assert item.manifest_refresh is True
    assert item.has_drm is True
    assert buffer.callables == [{"license_request_fn": sign_and_post, "manifest_refresh_fn": refresh}]
    wire = json.dumps(item.to_wire())  # must serialize: no callable in it
    assert "sign_and_post" not in wire


def test_dash_and_ism_capture(tmp_path):
    buffer = CaptureBuffer()
    dash = DASH_Downloader(
        mpd_url="https://cdn.test/a.mpd", mpd_content="<MPD/>", mpd_headers={"H": "1"},
        license_url="https://lic.test/", drm_preference="PR", output_path=str(tmp_path / "d.mkv"),
    )
    ism = ISM_Downloader(ism_url="https://cdn.test/a.ism/manifest", ism_content="<SmoothStreamingMedia/>", output_path=str(tmp_path / "i.mkv"))

    with capturing(buffer):
        dash.start()
        ism.start()

    assert [i.kind for i in buffer.items] == ["dash", "ism"]
    assert buffer.items[0].headers == {"H": "1"}
    assert buffer.items[0].drm_preference == "PR"
    assert buffer.items[1].manifest_content == "<SmoothStreamingMedia/>"
    assert not list(tmp_path.iterdir())


def test_mp4_capture(tmp_path):
    buffer = CaptureBuffer()

    with capturing(buffer):
        path, stopped, error = MP4_Downloader("https://cdn.test/a.mp4", str(tmp_path / "a.mp4"), headers={"R": "1"})

    assert (stopped, error) == (False, None)
    (item,) = buffer.items
    assert (item.kind, item.url, item.headers) == ("mp4", "https://cdn.test/a.mp4", {"R": "1"})
    assert not list(tmp_path.iterdir())


def test_downloads_with_no_remote_form_are_refused_not_run(tmp_path):
    buffer = CaptureBuffer()

    with capturing(buffer):
        path, stopped, error = YTDLPDownloader("https://video.test/watch", str(tmp_path)).start()

    assert path is None and stopped is True
    assert "yt-dlp" in error
    assert buffer.errors and not buffer.items


def test_capture_state_is_restored(tmp_path):
    assert context_tracker.capture is None

    with capturing(CaptureBuffer()):
        assert context_tracker.capture is not None
    assert context_tracker.capture is None
    assert context_tracker.capture_keys is None


def test_normal_runs_do_not_capture(tmp_path):
    downloader = _hls(tmp_path)

    assert context_tracker.capture is None
    assert downloader.file_already_exists is False  # constructing never captured anything


def test_capture_keys_collects_instead_of_logging(tmp_path):
    downloader = _hls(tmp_path)
    keys: list = []

    with capturing(None, keys):
        downloader._log_tracks_json([], [("aa" * 16, "bb" * 16), "cc:dd"], "https://cdn.test/master.m3u8")

    assert keys == [f"{'aa' * 16}:{'bb' * 16}", "cc:dd"]


# ── resolve_media against a fake service adapter ─────────────────────────────

class FakeApi:
    """Stands in for the GUI adapter: its start_download runs real downloaders, as a service would."""

    def __init__(self, per_episode=1, fail=False):
        self.seen_options = None
        self.calls = []
        self.fail = fail
        self.per_episode = per_episode

    def start_download(self, media_item, season=None, episodes=None):
        self.calls.append((media_item, season, episodes))
        self.seen_options = dict(context_tracker.site_options)
        if self.fail:
            YTDLPDownloader("https://x", ".").start()
            return True
        for n in range(self.per_episode):
            context_tracker.season = int(season or 0)
            context_tracker.episode = int(episodes or 0) + n
            context_tracker.episode_name = f"Ep {n}"
            HLS_Downloader(
                m3u8_url=f"https://cdn.test/{n}.m3u8",
                m3u8_content="#EXTM3U\n",
                output_path="never-written.mkv",
                license_request_fn=lambda c, i: b"lic",
            ).start()
        return True


def test_resolve_media_returns_items_and_hooks_and_fills_naming():
    api = FakeApi(per_episode=2)

    items, hooks = resolve_media(api, "ENTRY", "appletv", {"name": "My Show", "year": "2021"}, "1", "3", {"drm": "playready", "bad": object()})

    assert [i.url for i in items] == ["https://cdn.test/0.m3u8", "https://cdn.test/1.m3u8"]
    assert [i.episode for i in items] == [3, 4]
    assert all(i.title == "My Show" and i.year == "2021" and i.media_type == "Serie" for i in items)
    assert all("license_request_fn" in h for h in hooks)
    assert api.seen_options == {"drm": "playready"}  # non-scalar client options are dropped
    assert api.calls == [("ENTRY", "1", "3")]
    assert context_tracker.capture is None  # the caller's thread was never touched


def test_resolve_media_movie_naming():
    items, _ = resolve_media(FakeApi(), "ENTRY", "appletv", {"name": "A Film", "year": "1999", "is_movie": True}, None, None)

    assert items[0].media_type == "Film"
    assert items[0].title == "A Film"


def test_resolve_media_reports_why_nothing_could_be_captured():
    with pytest.raises(RemoteError) as failed:
        resolve_media(FakeApi(fail=True), "ENTRY", "csi", {"name": "x"}, None, None)

    assert failed.value.status == 422
    assert "yt-dlp" in str(failed.value)


def test_resolve_media_with_no_download_is_a_404():
    with pytest.raises(RemoteError) as nothing:
        resolve_media(FakeApi(per_episode=0), "ENTRY", "appletv", {"name": "x"}, "1", "1")

    assert nothing.value.status == 404


# ── server-side hooks ─────────────────────────────────────────────────────────

def test_license_hook_runs_the_service_callable_with_the_pssh_item():
    seen = {}

    def fn(challenge, item):
        seen["args"] = (challenge, item)
        return b"the-license"

    result = call_license_hook({"license_request_fn": fn}, b"chal", {"pssh": "AAA", "key_uri": "skd://x"})

    assert result == b"the-license"
    assert seen["args"] == (b"chal", {"pssh": "AAA", "key_uri": "skd://x"})


def test_license_hook_is_none_without_a_callable_and_rejects_empty_answers():
    assert call_license_hook({}, b"chal", None) is None
    with pytest.raises(RemoteError) as empty:
        call_license_hook({"license_request_fn": lambda c, i: b""}, b"chal", None)
    assert empty.value.status == 502


def test_refresh_hook():
    assert call_refresh_hook({"manifest_refresh_fn": lambda: "#EXTM3U\nnew"}) == "#EXTM3U\nnew"
    with pytest.raises(RemoteError) as missing:
        call_refresh_hook({})
    assert missing.value.status == 400


# ── CLI helpers ───────────────────────────────────────────────────────────────

def test_parse_server_options():
    assert parse_server_options(["drm=playready", "atmos=true", "x=False", "n=4", "store=US"]) == {
        "drm": "playready", "atmos": True, "x": False, "n": 4, "store": "US",
    }
    assert parse_server_options(None) == {}
    with pytest.raises(ValueError):
        parse_server_options(["novalue"])


# ── songs: what the service does after the download travels to the client ────────────────────────────────────────────

SONG = dict(title="Song", artist="Artist", album="Album", year="2020", track_number=3, genre="Pop", cover_url="https://c/x.jpg", album_artist="Artist", lyrics=None)


def test_the_song_post_processing_is_captured_not_run(tmp_path):
    from VibraVid.core.muxing.helper.audio import process_song

    buffer = CaptureBuffer()
    with capturing(buffer):
        DASH_Downloader(mpd_url="amazon://B0.mpd", mpd_content="<MPD/>", output_path=str(tmp_path / "song")).start()
        returned = process_song(file_path=str(tmp_path / "does-not-exist.m4a"), **SONG)  # would fail on a missing file

    assert returned == str(tmp_path / "does-not-exist.m4a")
    assert buffer.items[0].post_process == {"kind": "song", "args": SONG}
    assert json.loads(json.dumps(buffer.items[0].to_wire()))["post_process"]["args"]["artist"] == "Artist"


def test_post_processing_without_a_capture_is_the_normal_one(tmp_path, monkeypatch):
    from VibraVid.core.muxing.helper.audio import convert

    calls = []
    monkeypatch.setattr(convert, "tag_track", lambda **kw: calls.append(kw))
    monkeypatch.setattr(convert, "fix_container_mismatch", lambda path: path)
    monkeypatch.setattr(convert, "ffmpeg_params", None, raising=False)

    convert.process_song(file_path=str(tmp_path / "a.m4a"), title="T", artist="A")

    assert calls and calls[0]["title"] == "T"  # a local run still tags


def test_a_failure_after_the_captured_download_does_not_lose_it():
    class Api:
        def start_download(self, media_item, season=None, episodes=None):
            HLS_Downloader(m3u8_url="https://cdn.test/m.m3u8", m3u8_content="#EXTM3U\n", output_path="never.mkv").start()
            raise FileNotFoundError("tagging a file that was never downloaded")

    items, _ = resolve_media(Api(), "ENTRY", "svc", {"name": "x", "is_movie": True}, None, None)

    assert [i.url for i in items] == ["https://cdn.test/m.m3u8"]


def test_a_failure_before_any_capture_is_still_an_error():
    class Api:
        def start_download(self, media_item, season=None, episodes=None):
            raise ConnectionError("login failed")

    with pytest.raises(ConnectionError):
        resolve_media(Api(), "ENTRY", "svc", {"name": "x", "is_movie": True}, None, None)


def test_what_the_service_set_on_the_downloader_after_building_it_is_captured_and_applied(tmp_path):
    from VibraVid.remote.factory import build_downloader

    buffer = CaptureBuffer()
    downloader = DASH_Downloader(mpd_url="https://cdn.test/a.mpd", mpd_content="<MPD/>", output_path=str(tmp_path / "a.mkv"), display_selected_only=True)
    downloader.custom_filters = {"audio": "ita|it", "drop_clear_av": True}
    with capturing(buffer):
        downloader.start()

    media = ResolvedMedia.from_wire(json.loads(json.dumps(buffer.items[0].to_wire())))
    rebuilt = build_downloader(media, str(tmp_path / "b.mkv"))

    assert media.custom_filters == {"audio": "ita|it", "drop_clear_av": True} and media.display_selected_only is True
    assert rebuilt.custom_filters == {"audio": "ita|it", "drop_clear_av": True} and rebuilt.display_selected_only is True


def test_songs_are_named_for_the_music_folder_from_the_services_own_metadata():
    from VibraVid.remote.cli import output_path_for

    media = ResolvedMedia(kind="dash", url="amazon://B0.mpd", media_type="Film", title="x", post_process={"kind": "song", "args": SONG})

    path = output_path_for(media, "youtube_music")

    assert "Music" in path and "Album" in path and "03. Song" in path  # the song_format of the client's config, in its music folder
    assert not path.endswith(".mkv")  # no extension: the post-processing sets the final one


def test_the_client_runs_the_post_processing_on_the_real_file(monkeypatch):
    import VibraVid.core.muxing.helper.audio as audio
    from VibraVid.remote import cli

    seen = []
    monkeypatch.setattr(audio, "process_song", lambda **kw: seen.append(kw) or kw["file_path"].replace(".mkv", ".flac"))
    media = ResolvedMedia(kind="dash", url="u", post_process={"kind": "song", "args": SONG})

    assert cli._post_process(media, "C:/music/Artist/Song.mkv") is True
    assert seen[0]["file_path"] == "C:/music/Artist/Song.mkv" and seen[0]["title"] == "Song" and seen[0]["track_number"] == 3
    assert cli._post_process(ResolvedMedia(kind="hls", url="u"), "C:/x.mkv") is True  # nothing to run
    assert cli._post_process(media, None) is True


def test_a_failing_post_processing_fails_the_download(monkeypatch):
    import VibraVid.core.muxing.helper.audio as audio
    from VibraVid.remote import cli

    def boom(**kw):
        raise RuntimeError("ffmpeg exploded")

    monkeypatch.setattr(audio, "process_song", boom)

    assert cli._post_process(ResolvedMedia(kind="dash", url="u", post_process={"kind": "song", "args": SONG}), "C:/a.mkv") is False
