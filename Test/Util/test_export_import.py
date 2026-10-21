# 19.10.26

"""``--export`` writes the resolved media of any protocol to JSON, ``--import`` downloads it back."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from VibraVid.cli.command import download as download_cmd
from VibraVid.cli.run import setup_argument_parser
from VibraVid.core.downloader.util import _export
from VibraVid.core.downloader.util._export import read_export, write_export
from VibraVid.core.ui.tracker import context_tracker
from VibraVid.remote.capture import _wire_enc_override
from VibraVid.remote.factory import _enc_override
from VibraVid.remote.models import ResolvedMedia

KEY = "aa" * 16 + ":" + "bb" * 16


@pytest.fixture(autouse=True)
def _fresh_run(monkeypatch):
    """Each test is a new run: no file counts as already written."""
    monkeypatch.setattr(_export, "_written", [])
    yield
    context_tracker.export_file = None


def _media(kind="dash", **kw):
    return ResolvedMedia(kind=kind, url=f"https://cdn.example/{kind}", title="Bunny", media_type="Film", site="appletv", **kw)


@pytest.mark.parametrize("kind", ["mp4", "hls", "dash", "ism"])
def test_every_protocol_round_trips_through_the_file(kind, tmp_path):
    media = _media(kind, key=[KEY], desc="video: 1080p")
    file = tmp_path / "out.json"

    assert write_export(media, str(file)) == str(file)
    (back,) = read_export(str(file))

    assert (back.kind, back.url, back.title, back.site) == (kind, media.url, "Bunny", "appletv")
    assert back.key == [KEY]
    assert back.desc == "video: 1080p" and back.version == 1 and back.exported_at and back.vibravid_version


def test_the_file_carries_the_import_command_and_only_what_a_download_needs(tmp_path):
    media = _media("dash", manifest_content="<MPD/>", key=[KEY], headers={"User-Agent": "x"}, cookies={"a": "b"},
                   license_url="https://lic", license_headers={"h": "v"}, license_data="d", license_certificate="c")
    file = tmp_path / "out.json"
    write_export(media, str(file))
    (entry,) = json.loads(file.read_text(encoding="utf-8"))

    assert entry["cmd"] == f"python manual.py --down '{media.url}' --key {KEY} -o 'Bunny'"
    assert list(entry)[0] == "cmd"
    for gone in ("manifest_content", "headers", "cookies", "license_url", "license_headers", "license_data", "license_certificate", "max_segments", "max_time"):
        assert gone not in entry
    assert entry["key"] == [KEY]


def test_entries_of_one_run_pile_up_but_a_new_run_starts_afresh(tmp_path, monkeypatch):
    file = str(tmp_path / "out.json")
    write_export(_media("hls"), file)
    write_export(_media("dash"), file)
    assert [m.kind for m in read_export(file)] == ["hls", "dash"]

    monkeypatch.setattr(_export, "_written", [])
    write_export(_media("ism"), file)
    assert [m.kind for m in read_export(file)] == ["ism"]


def test_the_default_file_is_named_after_site_type_and_title(tmp_path, monkeypatch):
    monkeypatch.setattr(_export.config_manager, "base_path", str(tmp_path), raising=False)
    path = write_export(_media("hls"), "")
    assert path.endswith("appletv_Film_Bunny.json") and path.endswith(str(Path(".cache") / "export" / "appletv_Film_Bunny.json"))


def test_the_hls_aes_key_survives_the_json_as_hex():
    override = {"method": "AES-128", "key_bytes": bytes(range(16)), "iv": "00" * 16}
    wired = json.loads(json.dumps(_wire_enc_override(override)))
    assert wired["key_bytes"] == bytes(range(16)).hex()
    assert _enc_override(wired) == override


@pytest.mark.parametrize(
    "content, message",
    [("not json", "could not parse"), ('{"a": 1}', "no 'kind'/'url'"), ('"text"', "JSON array")],
)
def test_a_bad_file_is_refused_with_a_readable_message(tmp_path, content, message):
    file = tmp_path / "bad.json"
    file.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        read_export(str(file))


def test_a_missing_file_is_refused(tmp_path):
    with pytest.raises(ValueError, match="file not found"):
        read_export(str(tmp_path / "nope.json"))


def _import(monkeypatch, tmp_path, entries, **args):
    file = tmp_path / "in.json"
    file.write_text(json.dumps([m.to_wire() for m in entries]), encoding="utf-8")
    seen = []

    def fake_download(media, path, **kw):
        seen.append((media.kind, path))
        ok = not media.url.endswith("/fail")
        return SimpleNamespace(ok=ok, error=None if ok else "boom")

    monkeypatch.setattr("VibraVid.remote.factory.download_media", fake_download)
    result = download_cmd.handle_import(SimpleNamespace(import_file=str(file), **args))
    return result, seen


def test_import_downloads_each_entry_with_the_matching_kind(monkeypatch, tmp_path):
    entries = [_media(kind) for kind in ("mp4", "hls", "dash", "ism")]
    result, seen = _import(monkeypatch, tmp_path, entries)
    assert result == (True, True)
    assert [kind for kind, _ in seen] == ["mp4", "hls", "dash", "ism"]


def test_import_reports_failure_but_keeps_going(monkeypatch, tmp_path):
    entries = [_media("hls"), ResolvedMedia(kind="dash", url="https://cdn.example/fail"), _media("ism")]
    result, seen = _import(monkeypatch, tmp_path, entries)
    assert result == (True, False)
    assert len(seen) == 3


def test_import_uses_dash_o_only_for_a_single_entry(monkeypatch, tmp_path):
    _, seen = _import(monkeypatch, tmp_path, [_media("hls")], output=str(tmp_path / "one.mkv"))
    assert seen[0][1].endswith("one.mkv")

    _, seen = _import(monkeypatch, tmp_path, [_media("hls"), _media("dash")], output=str(tmp_path / "one.mkv"))
    assert not any(path.endswith("one.mkv") for _, path in seen)


def test_import_without_the_flag_is_not_handled():
    assert download_cmd.handle_import(SimpleNamespace(import_file=None)) == (False, False)


def test_import_of_a_bad_file_fails_cleanly(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("nope", encoding="utf-8")
    assert download_cmd.handle_import(SimpleNamespace(import_file=str(bad))) == (True, False)


def test_the_flags_are_parsed_and_the_old_one_is_gone():
    parser, _ = setup_argument_parser({})
    assert parser.parse_args([]).export_file is None
    assert parser.parse_args(["--export"]).export_file == ""
    assert parser.parse_args(["--export", "a.json"]).export_file == "a.json"
    assert parser.parse_args(["--import", "a.json"]).import_file == "a.json"
    with pytest.raises(SystemExit):
        parser.parse_args(["--down-json", "a.json"])


def test_the_command_names_a_tv_episode_and_repeats_every_key():
    media = _media("hls", key=[KEY, "cc" * 16 + ":" + "dd" * 16], season=2, episode=5, episode_name="Pilot: Part\\1")
    assert _export._download_command(media) == (
        f"python manual.py --down '{media.url}' --key {KEY} --key {'cc' * 16}:{'dd' * 16} -o 'Bunny S2_E5 Pilot_ Part_1'"
    )
